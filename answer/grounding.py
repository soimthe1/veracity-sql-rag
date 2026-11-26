"""
Grounding: narrows "some question in plain language" down to a small,
access-filtered slice of the catalog the LLM is allowed to write SQL
against. This is the step that makes SQL generation safe enough to be
"fully autonomous" — the LLM never sees the whole schema, only this
bounded whitelist, and guardrails.py refuses anything that strays
outside it.

Trust is reported, not enforced, here: earlier versions only ever grounded on entities/
relationships already promoted to TRUSTED, and returned an empty result
otherwise. With very few explicit foreign keys in a real schema, almost
nothing ever reaches TRUSTED on its own, so that made Ask unusable
rather than safe. Access control (the grants below) is the actual gate
now; confidence is instead computed per-answer (see worst_trust()) and
handed back to the caller, the way a search engine reports a relevance
score instead of hiding low-confidence results outright.

Reuses api.search.factory / api.joinpaths exactly as already built for
POST /v1/resolve — no second copy of search-or-join-path logic (see
DECISIONS.md's single-sourcing principle, the same one that keeps the
classification lattice itself out of this project entirely).

Access filtering here is EXACT-MATCH on (access_level, access_category)
against the caller's supplied grants — no inheritance on either axis,
deliberately following a strict document-retrieval access model
("filtering happens before retrieval, never after"; "exact-
match on (level, category), no inheritance"). Grants are never resolved
here — they arrive already resolved; nothing the client sends is
trusted beyond the pairs it explicitly hands over.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.joinpaths import find_join_paths
from api.search.factory import build_search_provider
from shared.config import AnswerConfig, ConfidenceConfig, SearchConfig
from shared.models import CatalogDatabase, CatalogEntity, CatalogField, CatalogRelationship, CatalogSource
from shared.settings import MIN_TRUST_LEVEL_SETTING_KEY, get_int, min_confidence_for_level

logger = logging.getLogger("answer.grounding")

# How many of a database's best matches count toward choosing it — enough
# for a question that spans a few related tables, few enough that a pile
# of weak matches can't outvote one strong one.
_TOP_MATCHES_PER_DATABASE = 3

# How many of a question's best-matching, not-yet-TRUSTED tables get a
# demand hit when the question goes unanswered (engine/coverage.py then
# prioritizes them for deep analysis).
_DEMAND_TABLES_PER_QUESTION = 5

# STALE is the one status still excluded outright, and it isn't a trust
# judgement — it means the last discovery pass didn't find this object
# in the source schema any more, so grounding the LLM on it would just
# generate SQL against a table that's gone. Every other status
# (UNKNOWN/DISCOVERED/SUSPECT/LEARNING/TRUSTED) is eligible; see the
# module docstring for why this is no longer a TRUSTED-only gate.
_EXCLUDED_STATUS = ("STALE",)

# Used only to (a) rank candidates when there are more matches than
# max_entities_in_context allows, preferring better-evidenced tables,
# and (b) compute the overall trust label reported back to the caller —
# never to exclude anything outright.
_STATUS_RANK = {"UNKNOWN": 0, "DISCOVERED": 0, "SUSPECT": 1, "LEARNING": 2, "TRUSTED": 3}


@dataclass
class GroundedTable:
    entity: CatalogEntity
    fields: List[CatalogField] = field(default_factory=list)


def worst_trust(tables: List[GroundedTable]) -> Tuple[str, float]:
    """The weakest link among the tables actually used to answer a
    question — an answer is only as reliable as its least-verified
    table, so this reports the minimum status/confidence rather than an
    average that could hide one poorly-evidenced join among several
    solid ones."""
    if not tables:
        return "UNKNOWN", 0.0
    worst = min(tables, key=lambda t: _STATUS_RANK.get(t.entity.status, 0))
    score = min((t.entity.confidence or 0.0) for t in tables)
    return worst.entity.status, round(score, 3)


@dataclass
class GroundingResult:
    source: Optional[CatalogSource]
    database: Optional[CatalogDatabase]
    tables: List[GroundedTable]
    whitelist: Set[Tuple[str, str]]  # {(schema.lower(), object.lower())}
    notes: str
    # Additive: the weakest-evidenced table used, per worst_trust() —
    # None only for a GroundingResult that never got as far as having
    # tables (see the various early-return branches below).
    trust_label: Optional[str] = None
    trust_score: Optional[float] = None
    # Additive: a small visual — just the tables actually used and the
    # relationships between them (not the whole schema; see
    # SchemaGraph.tsx for that) — so the Ask tab can show what the
    # answer was actually grounded on instead of only prose. Plain
    # dicts (not a nested dataclass) since these go straight out as
    # JSON via answer/schemas.py's AskOut and nothing else here needs
    # to read them back in.
    graph_nodes: List[dict] = field(default_factory=list)
    graph_edges: List[dict] = field(default_factory=list)
    # How many OTHER databases also had matching tables — ask.py tries
    # them in order when this one can't answer.
    other_candidate_databases: int = 0
    # Best-matching not-yet-TRUSTED tables — ask.py records a demand hit
    # for these only if the question ends up unanswered.
    demand_candidate_ids: List[UUID] = field(default_factory=list)
    # Column-level realm: per grounded table, the columns the caller may
    # use (all of them, not just the ones shown to the LLM) and the ones
    # they may NOT — guardrails.py rejects SQL that touches the latter.
    allowed_columns: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)
    denied_columns: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)
    # Every (level, category) cell the grounded tables and visible columns
    # sit in — the answer is labelled with the most sensitive of these.
    cells: Set[Tuple[int, str]] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return bool(self.tables) and self.database is not None


def _grant_set(grants) -> Set[Tuple[int, str]]:
    return {(g.level, (g.category or "").upper()) for g in grants}


def _coverage_hint(session: Session) -> str:
    """One human sentence on what the catalog DOES cover (databases and
    their main domains), so "nothing found" still tells the person what
    they can ask about instead of being a dead end."""
    try:
        rows = (
            session.query(CatalogDatabase.database_name, CatalogEntity.domain, func.count(CatalogEntity.id))
            .join(CatalogEntity, CatalogEntity.database_id == CatalogDatabase.id)
            .filter(CatalogEntity.domain.isnot(None))
            .group_by(CatalogDatabase.database_name, CatalogEntity.domain)
            .order_by(func.count(CatalogEntity.id).desc())
            .all()
        )
        names = [n for (n,) in session.query(CatalogDatabase.database_name).order_by(CatalogDatabase.database_name).all()]
    except Exception:  # noqa: BLE001 - a hint must never break the answer path
        session.rollback()
        return ""
    if not names:
        return " Nothing has been cataloged yet."
    domains: Dict[str, List[str]] = {}
    for db_name, domain, _ in rows:
        if len(domains.setdefault(db_name, [])) < 3:
            domains[db_name].append(domain)
    parts = [f"{n} ({', '.join(domains[n])})" if domains.get(n) else n for n in names[:8]]
    more = f" and {len(names) - 8} more" if len(names) > 8 else ""
    return f" The catalog currently covers: {', '.join(parts)}{more}."


def ground_question(
    session: Session, search_cfg: SearchConfig, answer_cfg: AnswerConfig,
    confidence_cfg: ConfidenceConfig, question: str, grants, search_text: Optional[str] = None,
    exclude_databases: Optional[Set[UUID]] = None, bypass_grants: Optional[bool] = None,
    only_databases: Optional[Set[UUID]] = None,
) -> GroundingResult:
    """`search_text` (answer/understanding.py's QuestionUnderstanding.
    search_text()) is what the catalog is actually searched with when
    present — the question plus the LLM's "kinds of data" terms, so a
    question about a person by name still finds the employee tables.
    Falls back to the raw question when the understanding step was
    unavailable.

    `exclude_databases`: databases already tried for this question —
    answer/routers/ask.py calls this again with them excluded when the
    best-ranked database produced no answer, so the NEXT most likely
    database is grounded instead (the same kind of data — employees,
    applications, statuses — often exists in several databases)."""
    # require_grants is a deliberate, reversible testing knob (see
    # AnswerConfig.require_grants) — when it's off, the (level, category)
    # grant check below is skipped entirely rather than deleted, so
    # flipping it back to true instantly restores exact-match
    # enforcement with no code change. guardrails.py's SQL-safety layer
    # is untouched by this flag either way.
    # bypass_grants (answer/access_context.py) wins when given: True for
    # an administrator or auth-disabled testing mode, False for a signed-in
    # user or a service caller — require_grants only matters when the
    # caller didn't decide.
    bypass = (not answer_cfg.require_grants) if bypass_grants is None else bypass_grants

    # The user-facing trust dial (Security tab) — see AnswerConfig.
    # min_trust_level's docstring. Read from catalog_settings first (the
    # live UI-controlled value); config.yaml's value is only the
    # fallback for before anyone has touched the control.
    min_trust_level = get_int(session, MIN_TRUST_LEVEL_SETTING_KEY, answer_cfg.min_trust_level)
    min_confidence = min_confidence_for_level(min_trust_level, confidence_cfg)

    if not grants and not bypass:
        return GroundingResult(
            source=None, database=None, tables=[], whitelist=set(),
            notes="No access grants were supplied with this question — the catalog answerer is "
                  "default-deny without them, so no database context was used.",
        )
    allowed = _grant_set(grants)

    provider = build_search_provider(search_cfg)
    pool_size = max(20, answer_cfg.max_entities_in_context * 4)
    # `only_databases` (the Ask page's database picker): search wider, then
    # keep only that database's tables.
    if only_databases:
        pool_size *= 5
    hits = provider.search(session, search_text or question, limit=pool_size)

    exclude_databases = exclude_databases or set()
    score_by_id: Dict[UUID, float] = {h.entity_id: h.score for h in hits}
    candidates: List[CatalogEntity] = []
    # Demand signal (self-correcting coverage — see answer/demand.py,
    # engine/coverage.py): every TABLE/VIEW the search matched that
    # ISN'T TRUSTED yet, regardless of this caller's own access grants —
    # whether an object needs deeper analysis is a data-completeness
    # question, not an access-control one, so it's tracked even for
    # entities this particular caller couldn't see anyway. This no
    # longer decides whether the entity is USABLE (see module docstring)
    # — it's still worth recording so under-evidenced tables keep
    # getting prioritized for deeper analysis.
    not_yet_trusted_hits: List[UUID] = []
    denied_by_grant = 0
    denied_by_trust = 0
    for hit in hits:
        entity = session.get(CatalogEntity, hit.entity_id)
        if entity is None:
            continue
        if entity.object_type not in ("TABLE", "VIEW"):
            continue
        if entity.status in _EXCLUDED_STATUS:
            continue
        if entity.status != "TRUSTED":
            not_yet_trusted_hits.append(entity.id)
        if (entity.confidence or 0.0) < min_confidence:
            denied_by_trust += 1
            continue
        if not bypass and (entity.access_level, (entity.access_category or "").upper()) not in allowed:
            denied_by_grant += 1
            continue
        if entity.database_id in exclude_databases:
            continue
        if only_databases and entity.database_id not in only_databases:
            continue
        candidates.append(entity)

    # Demand is no longer recorded here, for every search hit (up to ~48
    # per question — which flagged nearly every table in the catalog).
    # ask.py records it only when the question ends up UNANSWERED, and
    # only for the few tables it was really grounded on
    # (GroundingResult.demand_candidate_ids).
    trust_denied_ids = [
        h.entity_id for h in hits
        if (e := session.get(CatalogEntity, h.entity_id)) is not None
        and e.object_type in ("TABLE", "VIEW") and e.status not in _EXCLUDED_STATUS
        and (e.confidence or 0.0) < min_confidence
    ][:_DEMAND_TABLES_PER_QUESTION]

    if not candidates:
        # Distinguish WHY nothing matched — "the search found nothing at
        # all" (most often: the question named a specific value like a
        # person's name rather than the kind of data being asked about —
        # this search matches table/column metadata, not row content),
        # vs "found some below the trust level currently set" (the user's
        # own dial — Security tab), vs "found some but they're not
        # visible under your current grants", vs "found some but none
        # are queryable objects right now" (e.g. stale) — different
        # fixes, so a single generic message was actively unhelpful.
        if exclude_databases:
            notes = "No other cataloged database has tables matching this question."
        elif not hits:
            notes = (
                f'I couldn\'t find anything in the catalog related to "{question}".{_coverage_hint(session)} '
                "If this data lives in a database that isn't cataloged yet, add it under catalog.sources "
                "and it will be picked up on the next discovery cycle."
            )
        elif denied_by_trust:
            notes = (
                f"Found {denied_by_trust} matching entit{'y' if denied_by_trust == 1 else 'ies'} for this "
                f"question, but below the trust level currently set ({min_trust_level}/10, requires confidence "
                f"≥ {min_confidence:.2f}). Lower the trust level in Security settings, or wait for more "
                "evidence to accumulate."
            )
        elif denied_by_grant:
            notes = (
                f"Found {denied_by_grant} matching entit{'y' if denied_by_grant == 1 else 'ies'} for this "
                "question, but none are visible under the grants this question was asked with."
            )
        else:
            notes = "Matched entities were found, but none are queryable tables or views right now (e.g. stale)."
        logger.info("Ask %r -> nothing grounded (hits=%d, below_trust=%d, denied_by_grant=%d)",
                    question, len(hits), denied_by_trust, denied_by_grant)
        return GroundingResult(source=None, database=None, tables=[], whitelist=set(), notes=notes,
                               demand_candidate_ids=trust_denied_ids)

    # One database per grounding (a single query can't join across
    # databases). Which one: the database whose BEST few matches are
    # strongest, weighted by how trusted they are — NOT the one with the
    # most matches. Counting matches let a database with many loosely
    # related tables win over the one that actually holds the data (e.g.
    # "user transfer requests" grounded on 9 loosely matching
    # Shop tables instead of Sales's TRUSTED
    # dbo.TransferRequests). If the chosen database can't answer, ask.py
    # moves on to the next one via exclude_databases.
    by_database: Dict[UUID, List[CatalogEntity]] = {}
    for c in candidates:
        by_database.setdefault(c.database_id, []).append(c)

    def _db_score(db_id: UUID) -> float:
        weighted = sorted(
            (score_by_id.get(e.id, 0.0) * (0.5 + (e.confidence or 0.0)) for e in by_database[db_id]),
            reverse=True,
        )
        return sum(weighted[:_TOP_MATCHES_PER_DATABASE])

    ranked_dbs = sorted(by_database, key=_db_score, reverse=True)
    database_id = ranked_dbs[0]
    candidates = sorted(by_database[database_id], key=lambda e: -score_by_id.get(e.id, 0.0))

    # Expand with join-path neighbors so a question spanning two tables
    # (e.g. "employee" + "health") can still be answered even if only
    # one of them matched the search directly.
    candidate_ids = [c.id for c in candidates]
    entity_by_id: Dict[UUID, CatalogEntity] = {c.id: c for c in candidates}
    for jp in find_join_paths(session, candidate_ids):
        for step in jp.steps:
            if step.entity_id in entity_by_id:
                continue
            neighbor = session.get(CatalogEntity, step.entity_id)
            if neighbor is None or neighbor.database_id != database_id:
                continue
            if neighbor.status in _EXCLUDED_STATUS:
                continue
            if (neighbor.confidence or 0.0) < min_confidence:
                continue
            if not bypass and (neighbor.access_level, (neighbor.access_category or "").upper()) not in allowed:
                continue
            entity_by_id[neighbor.id] = neighbor

    # Best-evidenced first: when there are more matches than
    # max_entities_in_context allows, keep the ones with the strongest
    # trust rather than an arbitrary/insertion-order slice.
    entities = sorted(
        entity_by_id.values(),
        key=lambda e: (-score_by_id.get(e.id, 0.0), -_STATUS_RANK.get(e.status, 0)),
    )
    entities = entities[: answer_cfg.max_entities_in_context]

    database = session.get(CatalogDatabase, database_id)
    source = session.get(CatalogSource, database.source_id) if database else None

    tables: List[GroundedTable] = []
    whitelist: Set[Tuple[str, str]] = set()
    allowed_columns: Dict[Tuple[str, str], Set[str]] = {}
    denied_columns: Dict[Tuple[str, str], Set[str]] = {}
    cells: Set[Tuple[int, str]] = set()
    for entity in entities:
        all_fields = (
            session.query(CatalogField)
            .filter_by(entity_id=entity.id)
            .order_by(CatalogField.ordinal_position)
            .all()
        )
        key = (entity.schema_name.lower(), entity.object_name.lower())
        visible: List[CatalogField] = []
        denied: Set[str] = set()
        for f in all_fields:
            cell = (f.access_level if f.access_level is not None else entity.access_level,
                    (f.access_category or entity.access_category or "").upper())
            if bypass or cell in allowed:
                if f.status != "STALE":
                    visible.append(f)
                    if cell[0] is not None:
                        cells.add(cell)
            else:
                denied.add(f.column_name.lower())
        allowed_columns[key] = {f.column_name.lower() for f in visible}
        if denied:
            denied_columns[key] = denied
        if entity.access_level is not None and entity.access_category:
            cells.add((entity.access_level, entity.access_category.upper()))
        tables.append(GroundedTable(entity=entity, fields=visible[: answer_cfg.max_fields_per_entity]))
        whitelist.add(key)

    trust_label, trust_score = worst_trust(tables)
    scope_note = "table(s)" if bypass else "access-permitted table(s)"
    db_name = database.database_name if database else "?"
    notes = (
        f"Grounded on {len(tables)} {scope_note} in {db_name} — weakest evidence used: "
        f"{trust_label} (confidence {trust_score:.2f}). Treat any non-TRUSTED table/relationship above "
        "as inferred, not independently confirmed."
    )
    if bypass:
        notes += " Access-grant enforcement is currently bypassed for testing (catalog.answer.require_grants: false) — every matching entity was eligible regardless of grants."

    # The graph: just these tables + whatever relationships already
    # connect two of them (both endpoints must be in this grounded set —
    # a relationship to a table that got excluded, e.g. by the trust
    # dial or the max_entities_in_context cap, is out of scope for this
    # specific answer's picture). Deliberately the final, truncated
    # `entities` list — not entity_by_id's full pre-cap set — so the
    # graph never shows an edge to a table that isn't actually part of
    # this answer's whitelist.
    entity_ids = [e.id for e in entities]
    graph_nodes = [
        {
            "id": str(e.id),
            "label": f"{e.schema_name}.{e.object_name}",
            "status": e.status,
            "confidence": round(e.confidence or 0.0, 3),
        }
        for e in entities
    ]
    rel_rows = (
        session.query(CatalogRelationship)
        .filter(CatalogRelationship.from_entity_id.in_(entity_ids), CatalogRelationship.to_entity_id.in_(entity_ids))
        .all()
    )
    graph_edges = [
        {"from_id": str(r.from_entity_id), "to_id": str(r.to_entity_id), "status": r.status}
        for r in rel_rows
    ]

    hidden = sum(len(v) for v in denied_columns.values())
    if hidden:
        notes += (f" {hidden} column(s) in these tables are outside your access and were not used.")

    return GroundingResult(
        source=source, database=database, tables=tables, whitelist=whitelist, notes=notes,
        allowed_columns=allowed_columns, denied_columns=denied_columns, cells=cells,
        other_candidate_databases=len(ranked_dbs) - 1,
        demand_candidate_ids=[e.id for e in candidates if e.status != "TRUSTED"][:_DEMAND_TABLES_PER_QUESTION],
        trust_label=trust_label, trust_score=trust_score,
        graph_nodes=graph_nodes, graph_edges=graph_edges,
    )
