"""
Autonomous relationship inference beyond declared foreign keys (spec:
"Autonomous relationship discovery" — enterprise databases frequently
have missing/incomplete FK definitions).

Two evidence sources live here:
1. naming_similarity + pk_fk_datatype_compatible — pure structural
   comparison over what's already in the catalog (no source I/O, so
   this runs even while a source is STALE/unreachable, using
   last-known-good metadata).
2. view/procedure JOIN-predicate mining — regex-based, best-effort
   extraction of `a.col = b.col`-shaped predicates from SQL definition
   text, run AT DISCOVERY TIME (engine/discovery.py calls
   `mine_and_record_join_evidence` while the definition text is in
   hand) so the raw view/procedure source is never persisted into the
   catalog database — only the derived evidence is. This matters for
   an air-gapped, classification-conscious deployment: the catalog
   should never become a second copy of business logic source code.

Bounded, real VALUE-OVERLAP testing against live data lives in
engine/profiling.py, not here — it needs a source connection and
respects catalog.profiling's safety rails.
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from shared.models import CatalogEntity, CatalogEvidence, CatalogField, CatalogRelationship

logger = logging.getLogger("engine.relationships")

_DECORATION_RE = re.compile(r"(_id|_cd|_code|_no|_num|_key|_fk|_pk)$", re.IGNORECASE)
# Same decorations without the underscore ("FileID", "EmpCode") — only
# stripped when a meaningful root (3+ chars) remains.
_BARE_DECORATION_RE = re.compile(r"(id|cd|code|no|num|key|fk|pk)$", re.IGNORECASE)

# Column roots that carry no information about WHICH table they point at.
# Every table has an "ID"; a column called ID/Code/Key is that table's own
# identifier, never evidence of a link to some other table's ID. Matching
# on these produced links like UserAuthentication.ID -> Weather.ID.
_GENERIC_ROOTS = {"", "id", "code", "cd", "key", "no", "num", "number", "name", "type", "guid", "uid",
                  "rowid", "seq", "value", "description", "desc", "date", "pk", "fk"}

_TYPE_FAMILIES = {
    "numeric": {"int", "bigint", "smallint", "tinyint", "decimal", "numeric", "float", "real", "money", "smallmoney", "bit"},
    "string": {"varchar", "nvarchar", "char", "nchar", "text", "ntext", "sysname"},
    "date": {"date", "datetime", "datetime2", "smalldatetime", "time", "datetimeoffset"},
    "guid": {"uniqueidentifier"},
    "binary": {"binary", "varbinary", "image"},
}
_TYPE_TO_FAMILY = {t: fam for fam, types in _TYPE_FAMILIES.items() for t in types}

NAMING_SIMILARITY_THRESHOLD = 0.72


def _normalize_name(name: str) -> str:
    n = name.lower().replace("_", "")
    n = _DECORATION_RE.sub("", name.lower()).replace("_", "")
    return n


def _root(name: str) -> str:
    """Identifier root with any key-ish decoration removed:
    FileID -> file, Emp_Code -> emp, StatusID -> status, ID -> ''."""
    n = _DECORATION_RE.sub("", (name or "").lower()).replace("_", "")
    stripped = _BARE_DECORATION_RE.sub("", n)
    return stripped if len(stripped) >= 3 else ("" if n in _GENERIC_ROOTS or len(n) <= 2 else n)


def _table_roots(table_name: str) -> set:
    """Plausible bare names for a table: as written, singular, and with a
    key-ish suffix removed — tbl_Employees -> {employees, employee},
    UserTransferStatus -> {usertransferstatus, usertransferstatu*},
    SyncStatusCode -> {syncstatuscode, syncstatus}. (A wrong singular
    like "statu" is harmless: it's only ever compared for equality or
    as a suffix alongside the unmodified name.)"""
    n = re.sub(r"^(tbl|tb|t|vw|v)_", "", (table_name or "").lower()).replace("_", "")
    roots = {n, _root(n)}
    for base in list(roots):
        for suffix in ("ies", "es", "s"):
            if base.endswith(suffix) and len(base) - len(suffix) >= 3:
                roots.add(base[: -len(suffix)] + ("y" if suffix == "ies" else ""))
                break
    return {r for r in roots if len(r) >= 3}


def is_plausible_reference(child_column: str, parent_column: str, parent_table: str) -> bool:
    """Would a person reading the names believe child_column points at
    parent_table's parent_column?

    - A generic child column (ID, Code, Key...) never references another
      table — it's its own table's identifier.
    - If the parent key is itself descriptive (FileID, EmpCode), the child
      must share that root: WorkflowLog.FileID -> Files.FileID.
    - If the parent key is generic (just "ID"), the child must name the
      parent TABLE instead: UserTransfer.StatusID -> UserTransferStatus.ID,
      Orders.CustomerID -> Customers.ID.
    """
    child = _root(child_column)
    if child in _GENERIC_ROOTS:
        return False
    parent = _root(parent_column)
    if parent not in _GENERIC_ROOTS:
        return child == parent or (len(child) >= 4 and (child.endswith(parent) or parent.endswith(child)))
    return len(child) >= 3 and any(
        child == t or t.endswith(child) or child.endswith(t) for t in _table_roots(parent_table)
    )


def naming_similarity_score(a: str, b: str) -> float:
    """0..1. Exact match after normalization scores 1.0; otherwise a
    SequenceMatcher ratio over the normalized forms. Deliberately simple
    over something like a learned embedding — this is a nudge in the
    weighted-evidence sum (config: confidence.weights.naming_similarity),
    never the sole basis for a relationship."""
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 4)


def datatype_compatible(a: str, b: str) -> bool:
    fam_a = _TYPE_TO_FAMILY.get((a or "").lower())
    fam_b = _TYPE_TO_FAMILY.get((b or "").lower())
    return fam_a is not None and fam_a == fam_b


@dataclass
class NamingCandidate:
    from_field: CatalogField
    to_field: CatalogField
    similarity: float
    datatype_ok: bool


def find_naming_candidates(session: Session, database_id: UUID) -> List[NamingCandidate]:
    """Compares every non-PK field against every PK field in the same
    database. O(n*m) but n and m are "columns in one database," not
    "rows" — cheap even for large schemas; still, this is the kind of
    pass that benefits from the coverage knob (see engine/coverage.py) —
    callers should typically restrict `fields`/`pk_fields` to
    deep_analysis=True entities rather than every table in a huge
    database."""
    entities = (
        session.query(CatalogEntity)
        .filter_by(database_id=database_id, deep_analysis=True)
        .filter(CatalogEntity.object_type.in_(("TABLE",)))
        .all()
    )
    if not entities:
        return []
    entity_ids = [e.id for e in entities]

    fields = session.query(CatalogField).filter(CatalogField.entity_id.in_(entity_ids)).all()
    pk_fields = [f for f in fields if f.is_primary_key]
    table_name = {e.id: e.object_name for e in entities}

    existing_pairs = set()
    for rel in session.query(CatalogRelationship).filter(
        CatalogRelationship.from_entity_id.in_(entity_ids)
    ).all():
        existing_pairs.add((rel.from_entity_id, rel.from_field_id, rel.to_entity_id, rel.to_field_id))

    candidates: List[NamingCandidate] = []
    for f in fields:
        if f.is_primary_key:
            continue
        for pk in pk_fields:
            if pk.entity_id == f.entity_id:
                continue  # never propose a table referencing its own PK via a different column, that's not a relationship signal here
            if (f.entity_id, f.id, pk.entity_id, pk.id) in existing_pairs:
                continue
            if not is_plausible_reference(f.column_name, pk.column_name, table_name[pk.entity_id]):
                continue
            dt_ok = datatype_compatible(f.data_type, pk.data_type)
            if _root(pk.column_name) in _GENERIC_ROOTS and not dt_ok:
                continue  # "UserId" (int) can't reference a "Guid" key, however well the names line up
            sim = naming_similarity_score(f.column_name, pk.column_name)
            if _root(pk.column_name) in _GENERIC_ROOTS:
                # Parent key is just "ID": the evidence is that the child
                # column names the parent TABLE (checked above), not that
                # the two column names look alike.
                sim = max(sim, 0.8)
            if sim >= NAMING_SIMILARITY_THRESHOLD:
                candidates.append(NamingCandidate(f, pk, sim, dt_ok))
    return candidates


# Evidence that only ever came from comparing names/types (or a value
# overlap between two identity columns, which every pair of 1..N integer
# keys "passes"). A relationship backed by nothing else is retired when
# its names fail is_plausible_reference(). Join-mining, declared FKs and
# executed-join evidence are real usage signals and are never touched.
_NAME_ONLY_EVIDENCE = {"naming_similarity", "pk_fk_datatype_compatible", "value_overlap"}


def retire_implausible_naming_relationships(session: Session, database_id: UUID) -> int:
    """Deletes inferred relationships in this database that exist ONLY
    because two column names looked alike, where the names don't
    actually refer to each other (see is_plausible_reference) — e.g.
    FilterText.ID ~ Weather.ID. Returns how many were removed."""
    entity_ids = [e.id for e in session.query(CatalogEntity.id).filter_by(database_id=database_id).all()]
    if not entity_ids:
        return 0
    rels = (
        session.query(CatalogRelationship)
        .filter(CatalogRelationship.from_entity_id.in_(entity_ids), CatalogRelationship.is_declared_fk.is_(False))
        .filter(CatalogRelationship.from_field_id.isnot(None), CatalogRelationship.to_field_id.isnot(None))
        .all()
    )
    removed = 0
    for rel in rels:
        evidence = session.query(CatalogEvidence).filter_by(subject_type="relationship", subject_id=rel.id).all()
        if any(ev.evidence_type not in _NAME_ONLY_EVIDENCE for ev in evidence):
            continue
        from_field = session.get(CatalogField, rel.from_field_id)
        to_field = session.get(CatalogField, rel.to_field_id)
        to_entity = session.get(CatalogEntity, rel.to_entity_id)
        if from_field is None or to_field is None or to_entity is None:
            continue
        if is_plausible_reference(from_field.column_name, to_field.column_name, to_entity.object_name):
            continue
        for ev in evidence:
            session.delete(ev)
        session.delete(rel)
        removed += 1
    if removed:
        logger.info("Retired %d implausible name-only relationship(s) in database %s", removed, database_id)
    return removed


def record_naming_candidates(session: Session, candidates: List[NamingCandidate]) -> int:
    """Creates/updates the candidate relationships + their structural
    evidence. Does NOT score confidence — engine/confidence.py does
    that from whatever evidence exists, which may later also include
    value-overlap (engine/profiling.py) and join-mining evidence."""
    created = 0
    for c in candidates:
        rel = session.query(CatalogRelationship).filter_by(
            from_entity_id=c.from_field.entity_id, from_field_id=c.from_field.id,
            to_entity_id=c.to_field.entity_id, to_field_id=c.to_field.id,
        ).one_or_none()
        if rel is None:
            from_entity = session.get(CatalogEntity, c.from_field.entity_id)
            to_entity = session.get(CatalogEntity, c.to_field.entity_id)
            rel = CatalogRelationship(
                from_entity_id=c.from_field.entity_id, from_field_id=c.from_field.id,
                to_entity_id=c.to_field.entity_id, to_field_id=c.to_field.id,
                relationship_label=f"{from_entity.object_name}.{c.from_field.column_name} ~ "
                                    f"{to_entity.object_name}.{c.to_field.column_name}",
                is_declared_fk=False, status="DISCOVERED",
            )
            session.add(rel)
            session.flush()
            created += 1

        _ensure_evidence(session, rel.id, "naming_similarity", {"similarity": c.similarity})
        if c.datatype_ok:
            _ensure_evidence(session, rel.id, "pk_fk_datatype_compatible", {"compatible": True})
    return created


def _ensure_evidence(session: Session, relationship_id: UUID, evidence_type: str, detail: dict) -> None:
    existing = session.query(CatalogEvidence).filter_by(
        subject_type="relationship", subject_id=relationship_id, evidence_type=evidence_type
    ).one_or_none()
    if existing is None:
        session.add(CatalogEvidence(
            subject_type="relationship", subject_id=relationship_id,
            evidence_type=evidence_type, detail=detail,
        ))
    else:
        existing.detail = detail


# --------------------------------------------------------------------
# JOIN-predicate mining (best-effort; see module docstring)
# --------------------------------------------------------------------

_ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+\[?(\w+)\]?\.?\[?(\w+)?\]?\s+(?:AS\s+)?\[?(\w+)\]?\b",
    re.IGNORECASE,
)
_PREDICATE_RE = re.compile(
    r"\[?(\w+)\]?\.\[?(\w+)\]?\s*=\s*\[?(\w+)\]?\.\[?(\w+)\]?"
)


def _resolve_aliases(definition: str) -> Dict[str, str]:
    """Best-effort alias -> real table name map from FROM/JOIN clauses.
    Handles `FROM schema.table alias` and `FROM table AS alias`; falls
    back to mapping a table name to itself so unaliased predicates
    (`Table.Col = Other.Col`) still resolve."""
    aliases: Dict[str, str] = {}
    for m in _ALIAS_RE.finditer(definition):
        first, second, alias = m.groups()
        table = second or first
        if alias:
            aliases[alias.lower()] = table.lower()
        aliases[table.lower()] = table.lower()
    return aliases


def mine_join_predicates(definition: str) -> List[Tuple[str, str, str, str]]:
    """Returns [(table_a, col_a, table_b, col_b), ...] with aliases
    resolved where possible. Best-effort: subqueries, CTEs with
    reused aliases, and non-equality joins are not specially handled —
    false negatives (missing a real join) are safe here (just less
    evidence gathered); false positives are the risk to watch, which
    is why this only ever contributes a small, capped evidence weight
    (see catalog.confidence.weights.view_definition_confirms /
    procedure_usage_confirms), never a relationship by itself."""
    if not definition:
        return []
    aliases = _resolve_aliases(definition)
    out = []
    for m in _PREDICATE_RE.finditer(definition):
        qa, ca, qb, cb = m.groups()
        table_a = aliases.get(qa.lower(), qa.lower())
        table_b = aliases.get(qb.lower(), qb.lower())
        if table_a == table_b:
            continue  # same-table predicate, not a join between two entities
        out.append((table_a, ca, table_b, cb))
    return out


def mine_and_record_join_evidence(session: Session, database_id: UUID, source_object_type: str,
                                   definition: str) -> int:
    """Called from engine/discovery.py right after fetching a view/
    procedure/function's definition text, while it's still in memory —
    the raw text is never written to the catalog DB, only whatever
    join evidence this extracts from it. `source_object_type` is
    "VIEW" or "PROCEDURE"/"FUNCTION" and picks which evidence_type the
    result is recorded as (spec keeps these as distinct evidence
    categories with distinct weights)."""
    predicates = mine_join_predicates(definition)
    if not predicates:
        return 0

    evidence_type = "view_definition_confirms" if source_object_type == "VIEW" else "procedure_usage_confirms"

    entities_by_name = {
        e.object_name.lower(): e
        for e in session.query(CatalogEntity).filter_by(database_id=database_id).all()
    }
    recorded = 0
    for table_a, col_a, table_b, col_b in predicates:
        ea = entities_by_name.get(table_a)
        eb = entities_by_name.get(table_b)
        if ea is None or eb is None:
            continue
        fa = session.query(CatalogField).filter_by(entity_id=ea.id, column_name=col_a).one_or_none()
        fb = session.query(CatalogField).filter_by(entity_id=eb.id, column_name=col_b).one_or_none()
        if fa is None or fb is None:
            continue

        rel = session.query(CatalogRelationship).filter(
            (
                (CatalogRelationship.from_entity_id == ea.id) & (CatalogRelationship.from_field_id == fa.id) &
                (CatalogRelationship.to_entity_id == eb.id) & (CatalogRelationship.to_field_id == fb.id)
            ) | (
                (CatalogRelationship.from_entity_id == eb.id) & (CatalogRelationship.from_field_id == fb.id) &
                (CatalogRelationship.to_entity_id == ea.id) & (CatalogRelationship.to_field_id == fa.id)
            )
        ).one_or_none()

        if rel is None:
            rel = CatalogRelationship(
                from_entity_id=ea.id, from_field_id=fa.id, to_entity_id=eb.id, to_field_id=fb.id,
                relationship_label=f"{ea.object_name}.{fa.column_name} joined with {eb.object_name}.{fb.column_name}",
                is_declared_fk=False, status="DISCOVERED",
            )
            session.add(rel)
            session.flush()

        existing = session.query(CatalogEvidence).filter_by(
            subject_type="relationship", subject_id=rel.id, evidence_type=evidence_type
        ).one_or_none()
        if existing is None:
            session.add(CatalogEvidence(
                subject_type="relationship", subject_id=rel.id, evidence_type=evidence_type,
                detail={"count": 1},
            ))
        else:
            detail = dict(existing.detail or {})
            detail["count"] = int(detail.get("count", 1)) + 1
            existing.detail = detail
        recorded += 1
    return recorded


def refine_entity_relevance(session: Session, database_id: UUID) -> int:
    """Applies engine/classification.py's refine_relevance() using
    real usage evidence now available (declared+inferred relationship
    counts, join-mining hits) — a small nudge, run during validation,
    never a full reclassification."""
    from engine.classification import refine_relevance

    entities = session.query(CatalogEntity).filter_by(database_id=database_id).all()
    updated = 0
    for e in entities:
        rel_count = session.query(CatalogRelationship).filter(
            (CatalogRelationship.from_entity_id == e.id) | (CatalogRelationship.to_entity_id == e.id)
        ).count()
        join_evidence_count = session.query(CatalogEvidence).join(
            CatalogRelationship,
            CatalogEvidence.subject_id == CatalogRelationship.id,
        ).filter(
            CatalogEvidence.subject_type == "relationship",
            CatalogEvidence.evidence_type.in_(("view_definition_confirms", "procedure_usage_confirms")),
            (CatalogRelationship.from_entity_id == e.id) | (CatalogRelationship.to_entity_id == e.id),
        ).count()
        new_score = refine_relevance(e.relevance_score, rel_count, join_evidence_count)
        if new_score != e.relevance_score:
            e.relevance_score = new_score
            updated += 1
    return updated
