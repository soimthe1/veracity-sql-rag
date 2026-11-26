"""
The question audit trail. Two levels, both always written (never
best-effort like CatalogEvidence/CatalogObservation — see CatalogAskAudit's
own docstring in shared/models.py):

- catalog_ask_audit: one row per generated query — allowed, rejected by
  the guardrail, or errored — with the exact SQL.
- catalog_ask_log: one row per question — who asked, their cells, the
  cells/tables the answer drew on and its classification, the outcome.

Cost: one small insert per query plus one per question — negligible next
to the LLM and the query itself. The answer's data rows are never stored.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from shared.models import CatalogAskAudit, CatalogAskExport, CatalogAskLog

logger = logging.getLogger("answer.audit")

# Who is asking — set once per /v1/ask request (answer/routers/ask.py) so
# every audit row of that request carries it without threading it through
# every call.
current_asker: ContextVar[Optional[str]] = ContextVar("current_asker", default=None)


@dataclass
class QueryRecord:
    """One generated query of the current question, as audited."""
    purpose: Optional[str]
    sql: Optional[str]
    verdict: str                 # ALLOWED | REJECTED | ERROR
    rows: Optional[int]
    reason: Optional[str] = None


@dataclass
class AskTracker:
    """Everything the per-question log row needs, collected while the
    question runs (every audit.record call feeds it)."""
    ask_id: uuid.UUID = field(default_factory=uuid.uuid4)
    started: float = field(default_factory=time.perf_counter)
    queries: List[QueryRecord] = field(default_factory=list)

    @property
    def ran(self) -> int:
        return sum(1 for q in self.queries if q.sql and q.verdict == "ALLOWED")

    @property
    def blocked(self) -> int:
        return sum(1 for q in self.queries if q.sql and q.verdict == "REJECTED")

    @property
    def failed(self) -> int:
        return sum(1 for q in self.queries if q.verdict == "ERROR")

    @property
    def rows(self) -> int:
        return sum(q.rows or 0 for q in self.queries)


current_ask: ContextVar[Optional[AskTracker]] = ContextVar("current_ask", default=None)


def _purpose_from(reason: Optional[str]) -> Optional[str]:
    if reason and reason.startswith("[") and "]" in reason:
        return reason[1:reason.index("]")]
    return None


def record(
    session: Session, *, question: str, requested_grants: Optional[list], source_name: Optional[str],
    database_name: Optional[str], generated_sql: Optional[str], entities_touched: Optional[List[str]],
    guardrail_verdict: str, rejection_reason: Optional[str], row_count: Optional[int],
    duration_ms: Optional[float], error: Optional[str], enabled: bool, purpose: Optional[str] = None,
) -> None:
    tracker = current_ask.get()
    if tracker is not None:
        tracker.queries.append(QueryRecord(
            purpose=purpose or _purpose_from(rejection_reason), sql=generated_sql, verdict=guardrail_verdict,
            rows=row_count, reason=rejection_reason or error,
        ))
    if not enabled:
        return
    try:
        session.add(CatalogAskAudit(
            question=question[:8000],
            requested_grants=requested_grants,
            source_name=source_name,
            database_name=database_name,
            generated_sql=generated_sql,
            entities_touched=entities_touched or [],
            guardrail_verdict=guardrail_verdict,
            rejection_reason=rejection_reason,
            row_count=row_count,
            duration_ms=int(duration_ms) if duration_ms is not None else None,
            error=error,
            asked_by=current_asker.get(),
            ask_id=tracker.ask_id if tracker else None,
        ))
        session.commit()
    except Exception:  # noqa: BLE001 - the audit trail must never take the request down with it
        logger.exception("Failed to write ask audit row (question continued normally)")
        session.rollback()


def outcome_for(tracker: AskTracker, *, answered: bool, grounded: bool, disabled: bool = False) -> str:
    if disabled:
        return "DISABLED"
    if answered:
        return "ANSWERED"
    if tracker.ran:
        return "NO_ROWS"
    if tracker.blocked:
        return "BLOCKED"
    if tracker.failed:
        return "ERROR"
    return "DECLINED" if grounded else "NOT_FOUND"


def record_question(session: Session, tracker: AskTracker, *, question: str, asked_by: Optional[str], mode: str,
                    scope_database: Optional[str], database_name: Optional[str], outcome: str,
                    trust_label: Optional[str], trust_score: Optional[float], label_level: Optional[int],
                    label_category: Optional[str], cells_held: List[str], cells_used: List[str], tables: List[str],
                    columns_withheld: int, enabled: bool) -> None:
    if not enabled:
        return
    try:
        session.add(CatalogAskLog(
            id=tracker.ask_id, question=question[:8000], asked_by=asked_by, mode=mode,
            scope_database=scope_database, database_name=database_name, outcome=outcome,
            trust_label=trust_label, trust_score=trust_score, label_level=label_level, label_category=label_category,
            cells_held=cells_held[:200], cells_used=cells_used, tables=tables[:100],
            queries=tracker.ran, queries_blocked=tracker.blocked, queries_failed=tracker.failed,
            row_count=tracker.rows, columns_withheld=columns_withheld,
            duration_ms=int((time.perf_counter() - tracker.started) * 1000),
        ))
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write ask log row (question continued normally)")
        session.rollback()


def record_export(session: Session, log: CatalogAskLog, asked_by: Optional[str], fmt: str, enabled: bool) -> None:
    if not enabled:
        return
    try:
        session.add(CatalogAskExport(ask_id=log.id, asked_by=asked_by, format=fmt,
                                     label_level=log.label_level, label_category=log.label_category))
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write ask export row")
        session.rollback()


# ---------------------------------------------------------------------------
# Retention (catalog.answer.audit_retention_days; 0 = keep forever)
# ---------------------------------------------------------------------------

def prune(session: Session, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    n = session.query(CatalogAskLog).filter(CatalogAskLog.created_at < cutoff).delete(synchronize_session=False)
    n += session.query(CatalogAskAudit).filter(CatalogAskAudit.created_at < cutoff).delete(synchronize_session=False)
    session.commit()
    if n:
        logger.info("Question audit retention: removed %d record(s) older than %d day(s)", n, retention_days)
    return n


def start_retention_thread(session_factory, retention_days: int) -> None:
    """Daily prune in the background — nothing to run or schedule."""
    if retention_days <= 0:
        return

    def loop() -> None:
        while True:
            try:
                with session_factory() as s:
                    prune(s, retention_days)
            except Exception:  # noqa: BLE001
                logger.exception("Question audit retention run failed")
            time.sleep(24 * 3600)

    threading.Thread(target=loop, name="ask-audit-retention", daemon=True).start()
