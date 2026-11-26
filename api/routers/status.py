from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from api.deps import get_config, get_db
from api.schemas import (
    AnswerServiceInfoOut,
    CatalogSummaryOut,
    DatabaseSummaryOut,
    LabeledCountOut,
    LlmInfoOut,
    PostgresInfoOut,
    SourceCycleOut,
    StatusOut,
    SystemHealthOut,
)
from shared.config import AppConfig
from shared.db import healthcheck
from shared.models import CatalogDatabase, CatalogEntity, CatalogHealth, CatalogRelationship, CatalogSource
from shared.settings import COVERAGE_TARGET_PCT_SETTING_KEY, clamp_coverage_pct, get_float

router = APIRouter(prefix="/v1", tags=["status"])

# Human-readable labels for engine/classification.py's fixed vocabulary -
# purely cosmetic, the underlying value is always the key. Anything not
# in this map (a classification value added later and not yet labeled
# here) just falls back to a title-cased version of the raw key, so a
# new classification never breaks this endpoint, it just looks a little
# plainer until someone adds a label for it.
_CLASSIFICATION_LABELS = {
    "BUSINESS_ENTITY": "Business data",
    "TRANSACTIONAL_DATA": "Transactions",
    "REFERENCE_DATA": "Reference / lookup",
    "RELATIONSHIP_TABLE": "Relationship / junction",
    "CONFIGURATION": "Configuration",
    "AUDIT_HISTORY": "Audit history",
    "LOGGING": "Logs",
    "ARCHIVE": "Archive",
    "STAGING": "Staging",
    "TEMPORARY": "Temporary",
    "SYSTEM_TECHNICAL": "System / technical",
    "PROCEDURE": "Stored procedure",
    "FUNCTION": "Function",
    "SYNONYM": "Synonym",
    "UNKNOWN": "Not yet classified",
}

# Sensitivity flags are this project's own vocabulary (see
# config.yaml's
# catalog.classification.level_by_sensitivity_flag) - same fallback
# behavior as above for anything added later.
_SENSITIVITY_LABELS = {
    "PII": "Personally identifiable info",
    "HR": "HR data",
    "FINANCIAL": "Financial data",
    "HEALTH": "Health data",
    "DISCIPLINARY": "Disciplinary records",
    "SECURITY": "Security-related",
    "CREDENTIALS": "Credentials / secrets",
}

_OBJECT_TYPE_LABELS = {
    "TABLE": "Tables",
    "VIEW": "Views",
    "PROCEDURE": "Stored procedures",
    "FUNCTION": "Functions",
    "SYNONYM": "Synonyms",
}


def _label(mapping: dict, key: str) -> str:
    return mapping.get(key, key.replace("_", " ").title())


@router.get("/status", response_model=StatusOut)
def status(db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)) -> StatusOut:
    """Backs external status widgets / monitoring dashboards and
    this project's own status UI. Never raises — an unreachable catalog
    DB is reported IN the response body, not as a 5xx, so a dashboard
    polling this can always render something."""
    if not cfg.catalog.enabled:
        return StatusOut(
            enabled=False, catalog_db_connected=False, sources=0, databases=0, tables=0,
            entities_understood=0, trusted=0, learning=0, suspect=0, unknown=0, stale=0,
            avg_confidence=None, coverage_deep_analysis_target_pct=cfg.catalog.coverage.deep_analysis_target_pct,
            deep_analysis_entities=0, last_discovery_at=None, last_validation_at=None,
            llm_enabled=False, search_engine="postgres", components={},
        )

    connected = healthcheck(cfg)
    if not connected:
        return StatusOut(
            enabled=True, catalog_db_connected=False, sources=0, databases=0, tables=0,
            entities_understood=0, trusted=0, learning=0, suspect=0, unknown=0, stale=0,
            avg_confidence=None, coverage_deep_analysis_target_pct=cfg.catalog.coverage.deep_analysis_target_pct,
            deep_analysis_entities=0, last_discovery_at=None, last_validation_at=None,
            llm_enabled=cfg.catalog.inference.effectively_enabled, search_engine="postgres",
            components={},
        )

    sources_count = db.query(CatalogSource).count()
    databases_count = db.query(CatalogDatabase).count()
    tables_count = db.query(CatalogEntity).filter(CatalogEntity.object_type == "TABLE").count()
    entities_understood = db.query(CatalogEntity).filter(CatalogEntity.semantic_name.isnot(None)).count()
    deep_count = db.query(CatalogEntity).filter(CatalogEntity.deep_analysis.is_(True)).count()

    status_counts = dict(
        db.query(CatalogEntity.status, func.count(CatalogEntity.id)).group_by(CatalogEntity.status).all()
    )
    avg_confidence = db.query(func.avg(CatalogEntity.confidence)).filter(CatalogEntity.evidence_count > 0).scalar()

    last_discovery = db.query(func.max(CatalogSource.last_discovery_at)).scalar()
    last_validation = db.query(func.max(CatalogSource.last_validation_at)).scalar()

    components = {h.component: {"status": h.status, "detail": h.detail, "updated_at": h.updated_at.isoformat()}
                  for h in db.query(CatalogHealth).all()}

    return StatusOut(
        enabled=True, catalog_db_connected=True,
        sources=sources_count, databases=databases_count, tables=tables_count,
        entities_understood=entities_understood,
        trusted=status_counts.get("TRUSTED", 0), learning=status_counts.get("LEARNING", 0),
        suspect=status_counts.get("SUSPECT", 0), unknown=status_counts.get("UNKNOWN", 0),
        stale=status_counts.get("STALE", 0),
        avg_confidence=round(avg_confidence, 4) if avg_confidence is not None else None,
        # The LIVE value (Security tab's catalog-depth control, see
        # shared/settings.py) — not just config.yaml's default — so the
        # Overview/Integrity tabs show what the engine is actually using.
        coverage_deep_analysis_target_pct=clamp_coverage_pct(get_float(
            db, COVERAGE_TARGET_PCT_SETTING_KEY, cfg.catalog.coverage.deep_analysis_target_pct,
        )),
        deep_analysis_entities=deep_count,
        entities_total=sum(status_counts.values()),
        last_discovery_at=last_discovery, last_validation_at=last_validation,
        llm_enabled=cfg.catalog.inference.effectively_enabled,
        search_engine="postgres",
        components=components,
    )


@router.get("/status/summary", response_model=CatalogSummaryOut)
def status_summary(db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)) -> CatalogSummaryOut:
    """The human-readable 'what has this catalog actually figured out'
    view behind the web UI's Overview tab - domains, object-type mix,
    classification/sensitivity breakdown, relationship counts, and per-
    source next-run timing. Deliberately separate from GET /v1/status:
    that endpoint is a stable external contract (external
    status widgets poll it); this one is free to grow as the
    Overview tab's own needs grow, without touching that contract."""
    now = datetime.now(timezone.utc)
    discovery_minutes = cfg.catalog.discovery.interval_minutes
    validation_hours = cfg.catalog.validation.interval_hours

    def _next(last: Optional[datetime], delta: timedelta) -> Optional[datetime]:
        return (last + delta) if last is not None else None

    sources = [
        SourceCycleOut(
            name=s.name,
            status=s.status,
            last_discovery_at=s.last_discovery_at,
            next_discovery_at=_next(s.last_discovery_at, timedelta(minutes=discovery_minutes)),
            last_validation_at=s.last_validation_at,
            next_validation_at=_next(s.last_validation_at, timedelta(hours=validation_hours)),
            last_error=s.last_error,
        )
        for s in db.query(CatalogSource).order_by(CatalogSource.name).all()
    ]

    domain_rows = (
        db.query(CatalogEntity.domain, func.count(CatalogEntity.id))
        .filter(CatalogEntity.domain.isnot(None))
        .group_by(CatalogEntity.domain)
        .order_by(func.count(CatalogEntity.id).desc())
        .limit(12)
        .all()
    )
    domains = [LabeledCountOut(key=d, label=d, count=c) for d, c in domain_rows]

    type_rows = (
        db.query(CatalogEntity.object_type, func.count(CatalogEntity.id))
        .group_by(CatalogEntity.object_type)
        .all()
    )
    object_types = [
        LabeledCountOut(key=t, label=_label(_OBJECT_TYPE_LABELS, t), count=c) for t, c in type_rows
    ]

    class_rows = (
        db.query(CatalogEntity.classification, func.count(CatalogEntity.id))
        .group_by(CatalogEntity.classification)
        .order_by(func.count(CatalogEntity.id).desc())
        .all()
    )
    classifications = [
        LabeledCountOut(key=cl, label=_label(_CLASSIFICATION_LABELS, cl), count=c) for cl, c in class_rows
    ]

    # sensitivity_flags is a Postgres ARRAY(String) column - unnest() is
    # the standard way to turn "one row with an array" into "one row per
    # array element" so it can be GROUP BY'd like any other value; this
    # is a read against our own catalog Postgres (never a SQL Server
    # source), so raw SQL here doesn't go through engine/connectors/
    # mssql.py's read-only guard at all - it's a separate, simpler path.
    sens_rows = db.execute(
        text(
            """
            SELECT flag, COUNT(*) AS n
            FROM catalog_entities, unnest(sensitivity_flags) AS flag
            GROUP BY flag
            ORDER BY n DESC
            """
        )
    ).all()
    sensitivity = [
        LabeledCountOut(key=flag, label=_label(_SENSITIVITY_LABELS, flag), count=n) for flag, n in sens_rows
    ]

    relationships_total = db.query(CatalogRelationship).count()
    relationships_declared = db.query(CatalogRelationship).filter(CatalogRelationship.is_declared_fk.is_(True)).count()

    # Per-database breakdown. Counts come from the catalog; what the last
    # discovery scan SAW comes from the source's health detail (engine/
    # scheduler.py stores discover_source()'s per-database result there).
    seen: dict = {}
    for h in db.query(CatalogHealth).filter(CatalogHealth.component.like("source:%")).all():
        src_name = h.component.split(":", 1)[1]
        for d in (h.detail or {}).get("databases", []) or []:
            if isinstance(d, dict) and d.get("database"):
                seen[(src_name, d["database"])] = d
    per_db = (
        db.query(
            CatalogSource.name, CatalogDatabase.database_name,
            func.count(CatalogEntity.id),
            func.count(CatalogEntity.id).filter(CatalogEntity.object_type == "TABLE"),
            func.count(CatalogEntity.id).filter(CatalogEntity.object_type == "VIEW"),
            func.count(CatalogEntity.id).filter(CatalogEntity.object_type.in_(("PROCEDURE", "FUNCTION"))),
            func.count(CatalogEntity.id).filter(CatalogEntity.status == "TRUSTED"),
            func.count(CatalogEntity.id).filter(CatalogEntity.status == "LEARNING"),
            func.count(CatalogEntity.id).filter(CatalogEntity.status.in_(("UNKNOWN", "DISCOVERED"))),
            func.avg(CatalogEntity.confidence).filter(CatalogEntity.object_type.in_(("TABLE", "VIEW"))),
        )
        .join(CatalogDatabase, CatalogDatabase.source_id == CatalogSource.id)
        .outerjoin(CatalogEntity, CatalogEntity.database_id == CatalogDatabase.id)
        .group_by(CatalogSource.name, CatalogDatabase.database_name)
        .order_by(func.count(CatalogEntity.id).desc(), CatalogDatabase.database_name)
        .all()
    )
    databases = []
    for src_name, db_name, n, tables, views, prog, trusted, learning, unknown, avg_conf in per_db:
        scan = seen.get((src_name, db_name), {})
        objects_seen = scan.get("objects_seen")
        visible = objects_seen if objects_seen is not None else n
        warning = "none" if visible == 0 else ("few" if tables + views <= 3 else None)
        databases.append(DatabaseSummaryOut(
            source_name=src_name, database_name=db_name, objects=n, tables=tables, views=views,
            programmable=prog, trusted=trusted, learning=learning, unknown=unknown,
            avg_confidence=round(avg_conf, 3) if avg_conf is not None else None,
            objects_seen_last_discovery=objects_seen, last_error=scan.get("error"),
            visibility_warning=warning,
        ))

    # A database the scan saw but that has no catalog row yet (e.g. it
    # exposed zero objects) is exactly the one worth showing.
    listed = {(d.source_name, d.database_name) for d in databases}
    for (src_name, db_name), scan in seen.items():
        if (src_name, db_name) not in listed:
            n_seen = scan.get("objects_seen") or 0
            databases.append(DatabaseSummaryOut(
                source_name=src_name, database_name=db_name, objects=0, tables=0, views=0, programmable=0,
                trusted=0, learning=0, unknown=0, avg_confidence=None, objects_seen_last_discovery=n_seen,
                last_error=scan.get("error"), visibility_warning="none" if n_seen == 0 else "few",
            ))

    return CatalogSummaryOut(
        generated_at=now,
        databases=databases,
        discovery_interval_minutes=discovery_minutes,
        validation_interval_hours=validation_hours,
        sources=sources,
        domains=domains,
        object_types=object_types,
        classifications=classifications,
        sensitivity=sensitivity,
        relationships_total=relationships_total,
        relationships_declared_fk=relationships_declared,
        relationships_inferred=relationships_total - relationships_declared,
    )


@router.get("/status/system", response_model=SystemHealthOut)
def status_system(cfg: AppConfig = Depends(get_config)) -> SystemHealthOut:
    """Connection-level facts for every moving part the Health tab shows
    — the catalog Postgres itself, the LLM, and whether the answer
    service is even turned on in config. Config-only reads plus one live
    Postgres ping; deliberately doesn't touch the SQL Server sources
    (that's GET /v1/sources/config, already admin-gated, already has
    host/port/driver/live status/last error per source) or the answer
    process (the web UI pings that directly via its own /health, since
    this API process has no way to know if a SEPARATE process is up)."""
    db = cfg.catalog.database
    inf = cfg.catalog.inference

    return SystemHealthOut(
        postgres=PostgresInfoOut(
            host=db.host, port=db.port, database=db.database, sslmode=db.sslmode,
            connected=healthcheck(cfg),
        ),
        llm=LlmInfoOut(
            enabled=inf.enabled, effectively_enabled=inf.effectively_enabled,
            provider=inf.provider, endpoint=inf.endpoint, model=inf.model or None,
        ),
        answer=AnswerServiceInfoOut(
            enabled_in_config=cfg.catalog.answer.enabled, port=cfg.catalog.answer.port,
        ),
        auth_enabled=cfg.catalog.auth.enabled,
        search_engine="postgres",
    )
