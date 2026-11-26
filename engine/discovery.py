"""
Autonomous structural discovery (spec sections 5A/7 + "CRITICAL
REQUIREMENT — FULLY AUTONOMOUS DATABASE DISCOVERY"). For a source's
databases (autodiscovered unless explicitly listed), sweeps every
table/view/procedure/function/synonym via metadata-only queries
(engine/sql_queries.py), classifies each object (engine/classification.py),
fingerprints it (engine/fingerprint.py) for incremental change detection,
and upserts the result into the catalog Postgres schema. No table/column
selection is ever manual — see the spec's Final Autonomy Test.

Fault tolerance (spec section 13): a failure discovering one database
never stops the others; a failure on one source never stops other
sources. Every unit of work here is wrapped so a raised
SourceUnavailableError/QueryFailedError degrades just that unit.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from engine.classification import ObjectFacts, classify_structural
from engine.connectors import QueryFailedError, SourceConnector, SourceUnavailableError
from engine.fingerprint import database_fingerprint, definition_fingerprint, table_fingerprint
from engine.relationships import mine_and_record_join_evidence
from shared.models import (
    CatalogDatabase,
    CatalogEntity,
    CatalogEvidence,
    CatalogField,
    CatalogHistory,
    CatalogRelationship,
    CatalogSource,
)

logger = logging.getLogger("engine.discovery")


def _utcnow():
    return datetime.now(timezone.utc)


def get_or_create_source(session: Session, name: str, host: str, port: int) -> CatalogSource:
    src = session.query(CatalogSource).filter_by(name=name).one_or_none()
    if src is None:
        src = CatalogSource(name=name, host=host, port=port, status="DISCOVERED")
        session.add(src)
        session.flush()
    else:
        src.host, src.port = host, port
    return src


def get_or_create_database(session: Session, source: CatalogSource, database_name: str) -> CatalogDatabase:
    db = session.query(CatalogDatabase).filter_by(source_id=source.id, database_name=database_name).one_or_none()
    if db is None:
        db = CatalogDatabase(source_id=source.id, database_name=database_name)
        session.add(db)
        session.flush()
    db.is_online = True
    db.last_seen_at = _utcnow()
    return db


class DiscoveryContext:
    """One pass over one database's metadata (connector.load_metadata(),
    same row shapes for every database type). Builds lookup indexes once
    (columns/PKs/FKs/dependencies grouped by object_id) so classifying
    N objects doesn't re-scan the raw query results N times."""

    def __init__(self, connector: SourceConnector, database: str):
        self.connector = connector
        self.database = database

        meta = connector.load_metadata(database)
        self.tables = meta["tables"]
        self.views = meta["views"]
        self.procedures = meta["procedures"]
        self.functions = meta["functions"]
        self.synonyms = meta["synonyms"]
        columns = meta["columns"]
        pks = meta["primary_keys"]
        self.foreign_keys = meta["foreign_keys"]
        indexes = meta["indexes"]
        deps = meta["dependencies"]

        self.columns_by_obj: Dict[int, List[dict]] = defaultdict(list)
        for c in columns:
            self.columns_by_obj[c["object_id"]].append(c)

        self.pk_by_obj: Dict[int, List[str]] = defaultdict(list)
        for p in pks:
            self.pk_by_obj[p["object_id"]].append(p["column_name"])

        self.indexes_by_obj: Dict[int, List[dict]] = defaultdict(list)
        for i in indexes:
            self.indexes_by_obj[i["object_id"]].append(i)

        self.fk_out_count: Dict[int, int] = defaultdict(int)
        self.fk_in_count: Dict[int, int] = defaultdict(int)
        for fk in self.foreign_keys:
            self.fk_out_count[fk["parent_object_id"]] += 1
            self.fk_in_count[fk["referenced_object_id"]] += 1

        self.referenced_by: Dict[int, set] = defaultdict(set)
        for d in deps:
            self.referenced_by[d["referenced_object_id"]].add(d["referencing_object_id"])

    def facts_for_table(self, row: dict) -> ObjectFacts:
        oid = row["object_id"]
        return ObjectFacts(
            object_type="TABLE",
            schema_name=row["schema_name"],
            object_name=row["object_name"],
            columns=self.columns_by_obj.get(oid, []),
            pk_columns=self.pk_by_obj.get(oid, []),
            fk_out_count=self.fk_out_count.get(oid, 0),
            fk_in_count=self.fk_in_count.get(oid, 0),
            row_count_estimate=row.get("row_count_estimate") or 0,
            referenced_by_count=len(self.referenced_by.get(oid, ())),
        )

    def facts_for_code_object(self, row: dict, object_type: str) -> ObjectFacts:
        oid = row["object_id"]
        return ObjectFacts(
            object_type=object_type,
            schema_name=row["schema_name"],
            object_name=row["object_name"],
            referenced_by_count=len(self.referenced_by.get(oid, ())),
        )


def _record_history_if_changed(session: Session, subject_type: str, subject_id, field: str,
                                old_value: Optional[str], new_value: Optional[str], reason: str) -> None:
    if old_value == new_value:
        return
    session.add(CatalogHistory(
        subject_type=subject_type, subject_id=subject_id, field_changed=field,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        reason=reason,
    ))


def _upsert_entity(session: Session, database: CatalogDatabase, schema_name: str, object_name: str,
                    object_type: str, classification: str, relevance_score: float,
                    fingerprint_field: str, fingerprint_value: str,
                    row_count_estimate: Optional[int], pk_columns: Optional[List[str]],
                    fingerprint_enabled: bool) -> tuple[CatalogEntity, bool]:
    """Returns (entity, changed). `changed=False` with fingerprinting on
    means discovery did almost nothing for this object this cycle —
    exactly the incremental behavior spec section 7 asks for."""
    entity = session.query(CatalogEntity).filter_by(
        database_id=database.id, schema_name=schema_name, object_name=object_name
    ).one_or_none()

    if entity is None:
        entity = CatalogEntity(
            database_id=database.id, schema_name=schema_name, object_name=object_name,
            object_type=object_type, classification=classification, relevance_score=relevance_score,
            row_count_estimate=row_count_estimate, primary_key_fields=pk_columns or [],
            status="DISCOVERED", confidence=0.0,
        )
        setattr(entity, fingerprint_field, fingerprint_value)
        session.add(entity)
        session.flush()
        return entity, True

    entity.last_seen_at = _utcnow()
    unchanged = fingerprint_enabled and getattr(entity, fingerprint_field) == fingerprint_value
    if unchanged:
        return entity, False

    _record_history_if_changed(session, "entity", entity.id, "classification",
                                entity.classification, classification, "schema_refresh")
    _record_history_if_changed(session, "entity", entity.id, fingerprint_field,
                                getattr(entity, fingerprint_field), fingerprint_value, "schema_refresh")

    entity.classification = classification
    entity.relevance_score = max(entity.relevance_score, relevance_score)  # never silently downgrade below refined score
    entity.row_count_estimate = row_count_estimate
    entity.primary_key_fields = pk_columns or []
    setattr(entity, fingerprint_field, fingerprint_value)
    # A structural change invalidates prior semantic trust for this
    # object — demote to LEARNING rather than silently keeping a stale
    # TRUSTED status (last-known-good is still visible via catalog_history).
    if entity.status == "TRUSTED":
        entity.status = "LEARNING"
    return entity, True


def _upsert_fields(session: Session, entity: CatalogEntity, columns: List[dict], pk_columns: List[str]) -> None:
    existing = {f.column_name: f for f in session.query(CatalogField).filter_by(entity_id=entity.id).all()}
    seen = set()
    for col in columns:
        name = col["column_name"]
        seen.add(name)
        field = existing.get(name)
        is_pk = name in pk_columns
        if field is None:
            field = CatalogField(
                entity_id=entity.id, column_name=name, ordinal_position=col.get("ordinal_position", 0),
                data_type=col.get("data_type", "unknown"), max_length=col.get("max_length"),
                is_nullable=bool(col.get("is_nullable", True)), is_primary_key=is_pk,
                is_computed=bool(col.get("is_computed", False)), status="DISCOVERED",
            )
            session.add(field)
        else:
            field.ordinal_position = col.get("ordinal_position", field.ordinal_position)
            field.data_type = col.get("data_type", field.data_type)
            field.max_length = col.get("max_length")
            field.is_nullable = bool(col.get("is_nullable", True))
            field.is_primary_key = is_pk
            field.is_computed = bool(col.get("is_computed", False))
            field.last_seen_at = _utcnow()
    # Columns that disappeared (dropped) — mark STALE rather than delete,
    # preserving history for anything that referenced them.
    for name, field in existing.items():
        if name not in seen and field.status != "STALE":
            field.status = "STALE"


def _upsert_declared_fk(session: Session, entities_by_object_id: Dict[int, CatalogEntity],
                         fields_by_entity_and_name: Dict, fk_row: dict) -> Optional[CatalogRelationship]:
    from_entity = entities_by_object_id.get(fk_row["parent_object_id"])
    to_entity = entities_by_object_id.get(fk_row["referenced_object_id"])
    if from_entity is None or to_entity is None:
        return None
    from_field = fields_by_entity_and_name.get((from_entity.id, fk_row["parent_column"]))
    to_field = fields_by_entity_and_name.get((to_entity.id, fk_row["referenced_column"]))

    rel = session.query(CatalogRelationship).filter_by(
        from_entity_id=from_entity.id, from_field_id=from_field.id if from_field else None,
        to_entity_id=to_entity.id, to_field_id=to_field.id if to_field else None,
    ).one_or_none()
    if rel is None:
        rel = CatalogRelationship(
            from_entity_id=from_entity.id, from_field_id=from_field.id if from_field else None,
            to_entity_id=to_entity.id, to_field_id=to_field.id if to_field else None,
            relationship_label=f"{from_entity.object_name} references {to_entity.object_name}",
            is_declared_fk=True, status="DISCOVERED",
        )
        session.add(rel)
        session.flush()
    else:
        rel.is_declared_fk = True
        rel.last_seen_at = _utcnow()

    # explicit_fk evidence — one row per discovery cycle would be noisy;
    # only add if not already recorded for this relationship recently is
    # left to confidence.py's aggregation (it de-dupes by evidence_type
    # per subject when scoring), so we simply ensure at least one exists.
    exists = session.query(CatalogEvidence).filter_by(
        subject_type="relationship", subject_id=rel.id, evidence_type="explicit_fk"
    ).first()
    if exists is None:
        session.add(CatalogEvidence(
            subject_type="relationship", subject_id=rel.id, evidence_type="explicit_fk",
            detail={"fk_name": fk_row.get("fk_name")},
        ))
    return rel


def discover_database(session: Session, source: CatalogSource, connector: SourceConnector,
                       database_name: str, fingerprint_enabled: bool = True) -> dict:
    """Runs one full structural sweep of one database and upserts the
    result. Returns a small summary dict for logging/health reporting."""
    summary = {"database": database_name, "objects_seen": 0, "objects_changed": 0, "error": None}
    try:
        ctx = DiscoveryContext(connector, database_name)
    except (SourceUnavailableError, QueryFailedError) as exc:
        logger.warning("Discovery failed for %s/%s: %s", source.name, database_name, exc)
        summary["error"] = str(exc)
        return summary

    db_row = get_or_create_database(session, source, database_name)

    entities_by_object_id: Dict[int, CatalogEntity] = {}
    entity_fingerprints: List[str] = []
    fields_by_entity_and_name = {}

    def _persist_fields_index(entity: CatalogEntity) -> None:
        for f in session.query(CatalogField).filter_by(entity_id=entity.id).all():
            fields_by_entity_and_name[(entity.id, f.column_name)] = f

    for row in ctx.tables:
        facts = ctx.facts_for_table(row)
        cls = classify_structural(facts)
        fp = table_fingerprint(facts.columns, facts.pk_columns, ctx.indexes_by_obj.get(row["object_id"], []))
        entity, changed = _upsert_entity(
            session, db_row, row["schema_name"], row["object_name"], "TABLE",
            cls.classification, cls.relevance_score, "object_fingerprint", fp,
            row.get("row_count_estimate"), facts.pk_columns, fingerprint_enabled,
        )
        _upsert_fields(session, entity, facts.columns, facts.pk_columns)
        session.flush()
        _persist_fields_index(entity)
        entities_by_object_id[row["object_id"]] = entity
        entity_fingerprints.append(fp)
        summary["objects_seen"] += 1
        summary["objects_changed"] += int(changed)

    for row in ctx.views:
        facts = ObjectFacts(
            object_type="VIEW", schema_name=row["schema_name"], object_name=row["object_name"],
            columns=ctx.columns_by_obj.get(row["object_id"], []),
            fk_in_count=0, fk_out_count=0,
            referenced_by_count=len(ctx.referenced_by.get(row["object_id"], ())),
        )
        cls = classify_structural(facts)
        fp = definition_fingerprint(row.get("definition") or "")
        entity, changed = _upsert_entity(
            session, db_row, row["schema_name"], row["object_name"], "VIEW",
            cls.classification, cls.relevance_score, "definition_hash", fp,
            None, None, fingerprint_enabled,
        )
        _upsert_fields(session, entity, facts.columns, [])
        session.flush()
        entities_by_object_id[row["object_id"]] = entity
        entity_fingerprints.append(fp)
        summary["objects_seen"] += 1
        summary["objects_changed"] += int(changed)
        # Join-predicate mining happens NOW, while the definition text is
        # in memory — only the derived evidence is persisted, never the
        # raw view source (see engine/relationships.py's module docstring).
        if changed and row.get("definition"):
            summary.setdefault("join_evidence_mined", 0)
            summary["join_evidence_mined"] += mine_and_record_join_evidence(
                session, db_row.id, "VIEW", row["definition"]
            )

    for rows, object_type in ((ctx.procedures, "PROCEDURE"), (ctx.functions, "FUNCTION")):
        for row in rows:
            facts = ctx.facts_for_code_object(row, object_type)
            cls = classify_structural(facts)
            fp = definition_fingerprint(row.get("definition") or "")
            entity, changed = _upsert_entity(
                session, db_row, row["schema_name"], row["object_name"], object_type,
                cls.classification, cls.relevance_score, "definition_hash", fp,
                None, None, fingerprint_enabled,
            )
            entities_by_object_id[row["object_id"]] = entity
            entity_fingerprints.append(fp)
            summary["objects_seen"] += 1
            summary["objects_changed"] += int(changed)
            if changed and row.get("definition"):
                summary.setdefault("join_evidence_mined", 0)
                summary["join_evidence_mined"] += mine_and_record_join_evidence(
                    session, db_row.id, object_type, row["definition"]
                )

    for row in ctx.synonyms:
        entity, changed = _upsert_entity(
            session, db_row, row["schema_name"], row["object_name"], "SYNONYM",
            "UNKNOWN", 0.3, "definition_hash", definition_fingerprint(row.get("base_object_name") or ""),
            None, None, fingerprint_enabled,
        )
        summary["objects_seen"] += 1
        summary["objects_changed"] += int(changed)

    for fk_row in ctx.foreign_keys:
        _upsert_declared_fk(session, entities_by_object_id, fields_by_entity_and_name, fk_row)

    db_row.schema_fingerprint = database_fingerprint(entity_fingerprints)
    db_row.entity_count = summary["objects_seen"]
    db_row.last_verified_at = _utcnow()

    return summary


def discover_source(session: Session, source_cfg, connector: SourceConnector,
                     fingerprint_enabled: bool = True) -> dict:
    """Discovers every target database for one source. A single
    database failing never stops the others (spec section 13)."""
    source = get_or_create_source(session, source_cfg.name, source_cfg.display_host, source_cfg.port)
    results = []
    try:
        databases = connector.resolve_target_databases()
    except (SourceUnavailableError, QueryFailedError) as exc:
        source.status = "UNAVAILABLE"
        source.last_error = str(exc)
        source.consecutive_failures += 1
        logger.warning("Source %s unreachable: %s", source_cfg.name, exc)
        return {"source": source_cfg.name, "databases": [], "error": str(exc)}

    for db_name in databases:
        result = discover_database(session, source, connector, db_name, fingerprint_enabled)
        results.append(result)
        session.commit()  # commit per-database so one failure doesn't roll back everything already learned

    source.last_discovery_at = _utcnow()
    if any(r.get("error") for r in results) and len(results) > 0:
        failed = sum(1 for r in results if r.get("error"))
        source.status = "DEGRADED" if failed < len(results) else "UNAVAILABLE"
    else:
        source.status = "HEALTHY"
        source.consecutive_failures = 0
    session.commit()
    return {"source": source_cfg.name, "databases": results}
