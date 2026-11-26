"""
Suggested questions for the Ask tab, built deterministically from the
catalog — no LLM call, so they're instant and never invented: each one
names a real table's meaning (semantic_name) and a question shape the
pipeline can actually answer from that table's real columns:

  - a status/state/type column   -> "How many orders are there by status in Sales?"
  - a date/time column           -> "Show the latest shipments in Inventory"
  - reference/lookup data        -> "List all product categories in Sales"
  - anything else                -> "How many employees are there in HR?"

The database name is included on purpose: the same kind of data often
exists in several databases, and naming it steers the question to the
right one (see answer/grounding.py's database ranking).

Ranked best-evidenced first (TRUSTED, then by confidence and relevance),
spread across databases, skipping tables that make poor questions (logs,
configuration, temporary/staging/system tables).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from shared.models import CatalogDatabase, CatalogEntity, CatalogField

_SKIP_CLASSIFICATIONS = {"LOGGING", "TEMPORARY", "STAGING", "SYSTEM_TECHNICAL", "CONFIGURATION", "ARCHIVE",
                         "AUDIT_HISTORY"}
_STATUS_RANK = {"TRUSTED": 3, "LEARNING": 2, "SUSPECT": 1}
_STATUS_COL_RE = re.compile(r"(status|state|stage|type|category|kind)", re.IGNORECASE)
_DATE_TYPES = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset"}
_NAME_NOISE_RE = re.compile(r"\b(record|entry|table|view|data record|master)\b", re.IGNORECASE)


@dataclass
class Suggestion:
    question: str
    database_name: str
    table: str
    trust_label: str
    kind: str = "count"          # count | breakdown | latest | list
    domain: Optional[str] = None


def kind_of(question: str) -> str:
    if question.startswith("List all"):
        return "list"
    if question.startswith("Show the latest"):
        return "latest"
    if " by " in question:
        return "breakdown"
    return "count"


def pluralize(phrase: str) -> str:
    """'User Transfer Request' -> 'user transfer requests' (naive, English;
    only the last word changes)."""
    words = phrase.strip().split()
    if not words:
        return phrase
    last = words[-1]
    low = last.lower()
    if low.endswith(("ss", "us", "x", "z", "ch", "sh")):
        last += "es"            # class -> classes, status -> statuses
    elif low.endswith("s"):
        pass                    # already plural: Settings, Details
    elif low.endswith("y") and len(low) > 1 and low[-2] not in "aeiou":
        last = last[:-1] + "ies"
    else:
        last += "s"
    words[-1] = last
    return " ".join(words).lower()


def _subject(entity: CatalogEntity) -> str:
    name = _NAME_NOISE_RE.sub("", entity.semantic_name or entity.object_name).strip()
    name = re.sub(r"\s{2,}", " ", name)
    return pluralize(name or entity.object_name)


def _question_for(entity: CatalogEntity, fields: List[CatalogField], database: str) -> str:
    subject = _subject(entity)
    status_col = next((f for f in fields if _STATUS_COL_RE.search(f.column_name) and not f.is_primary_key), None)
    date_col = next((f for f in fields if (f.data_type or "").lower() in _DATE_TYPES), None)
    if entity.classification == "REFERENCE_DATA":
        return f"List all {subject} in {database}"
    if status_col is not None:
        label = re.sub(r"(id|code|cd)$", "", _STATUS_COL_RE.search(status_col.column_name).group(1).lower())
        return f"How many {subject} are there by {label or 'status'} in {database}?"
    if date_col is not None:
        return f"Show the latest {subject} in {database}"
    return f"How many {subject} are there in {database}?"


def build_suggestions(session: Session, limit: int = 8, min_confidence: float = 0.0,
                      allowed_cells: Optional[Set[Tuple[int, str]]] = None) -> List[Suggestion]:
    """`allowed_cells`: only suggest tables in these (level, category)
    cells — None means every table (admin / auth disabled)."""
    rows = (
        session.query(CatalogEntity, CatalogDatabase.database_name)
        .join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
        .filter(CatalogEntity.object_type.in_(("TABLE", "VIEW")))
        .filter(CatalogEntity.semantic_name.isnot(None))
        .filter(CatalogEntity.status != "STALE")
        .all()
    )
    ranked = sorted(
        (
            (e, db) for e, db in rows
            if e.classification not in _SKIP_CLASSIFICATIONS and (e.confidence or 0.0) >= min_confidence
            and (allowed_cells is None or (e.access_level, (e.access_category or "").upper()) in allowed_cells)
            and not re.search(r"(^|_)(temp|tmp|test|log|bak|backup|split)", e.object_name, re.IGNORECASE)
        ),
        key=lambda pair: (
            -_STATUS_RANK.get(pair[0].status, 0), -(pair[0].confidence or 0.0), -(pair[0].relevance_score or 0.0),
        ),
    )

    # Build a question for the best-ranked candidates, then prefer richer
    # question shapes ("by status", "latest", "list all") over a plain
    # count within the same trust level.
    def quality(q: str) -> int:
        return 2 if " by " in q else (1 if q.startswith(("Show the latest", "List all")) else 0)

    built = []
    top = ranked[: max(60, limit * 6)]
    # One query for every candidate's columns (not one per table).
    fields_by_entity: Dict = {}
    if top:
        for f in (session.query(CatalogField).filter(CatalogField.entity_id.in_([e.id for e, _ in top]))
                  .order_by(CatalogField.entity_id, CatalogField.ordinal_position)):
            fields_by_entity.setdefault(f.entity_id, []).append(f)
    for e, db in top:
        q = _question_for(e, fields_by_entity.get(e.id, []), db)
        built.append((e, db, q))
    built.sort(key=lambda x: (-_STATUS_RANK.get(x[0].status, 0), -quality(x[2]), -(x[0].confidence or 0.0)))

    # Spread across databases: first pass takes each database's best
    # question, then keep filling in rank order.
    out: List[Suggestion] = []
    used_dbs: Dict[str, int] = {}
    seen_subjects = set()
    for pass_no in (0, 1):
        for e, db, q in built:
            if len(out) >= limit:
                break
            subject = _subject(e)
            if subject in seen_subjects or (pass_no == 0 and db in used_dbs):
                continue
            out.append(Suggestion(question=q, database_name=db, table=f"{e.schema_name}.{e.object_name}",
                                  trust_label=e.status, kind=kind_of(q), domain=e.domain))
            used_dbs[db] = used_dbs.get(db, 0) + 1
            seen_subjects.add(subject)
    return out


def _history_query(session: Session, asked_by: Optional[str], only_mine: bool, search: Optional[str] = None):
    from shared.models import CatalogAskLog

    q = session.query(CatalogAskLog)
    if only_mine:
        q = q.filter(CatalogAskLog.asked_by == asked_by)
    if search:
        q = q.filter(CatalogAskLog.question.ilike(f"%{search.strip()}%"))
    return q


def _history_item(r) -> dict:
    return {
        "question": r.question, "asked_at": r.created_at, "answered": r.outcome == "ANSWERED",
        "ask_id": str(r.id), "outcome": r.outcome, "trust_label": r.trust_label, "trust_score": r.trust_score,
        "tables": len(r.tables or []), "database_name": r.database_name,
        "label_level": r.label_level, "label_category": r.label_category,
    }


def recent_questions(session: Session, limit: int = 8, asked_by: Optional[str] = None,
                     only_mine: bool = False) -> List[dict]:
    """Distinct recent questions (the latest ask of each), newest first,
    from the question log. With only_mine, just the questions `asked_by`
    asked (signed-in users never see each other's)."""
    from shared.models import CatalogAskLog

    rows = _history_query(session, asked_by, only_mine).order_by(CatalogAskLog.created_at.desc()).limit(limit * 6).all()
    out, seen = [], set()
    for r in rows:
        key = r.question.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_history_item(r))
        if len(out) >= limit:
            break
    return out


def question_history(session: Session, *, asked_by: Optional[str], only_mine: bool, limit: int, offset: int,
                     search: Optional[str] = None) -> Tuple[List[dict], int]:
    """Every question asked (no de-duplication), newest first — Ask's
    "View all"."""
    from shared.models import CatalogAskLog

    q = _history_query(session, asked_by, only_mine, search)
    total = q.count()
    rows = q.order_by(CatalogAskLog.created_at.desc()).offset(offset).limit(limit).all()
    return [_history_item(r) for r in rows], total
