"""
Question audit (administrators only) — Security > Question audit.

  GET /v1/audit/questions        who asked what: filters + headline counts
  GET /v1/audit/questions/{id}   one question: every generated query (exact
                                 SQL, verdict, rows) and documents created

Reads catalog_ask_log (one row per question), catalog_ask_audit (one per
generated query) and catalog_ask_exports. Flags what a security review
looks for: blocked queries (the safety check refused them — e.g. they
reached for restricted columns), answers with columns withheld, highly
classified answers, and documents created.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api.access import require_admin
from api.deps import get_db
from shared.models import CatalogAskAudit, CatalogAskExport, CatalogAskLog

router = APIRouter(prefix="/v1/audit", tags=["audit"], dependencies=[Depends(require_admin)])

_PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30),
            "90d": timedelta(days=90)}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _item(r: CatalogAskLog, exports: List[str]) -> Dict[str, Any]:
    return {
        "id": str(r.id), "question": r.question, "asked_by": r.asked_by, "mode": r.mode,
        "created_at": _iso(r.created_at), "outcome": r.outcome, "database_name": r.database_name,
        "scope_database": r.scope_database, "label_level": r.label_level, "label_category": r.label_category,
        "cells_used": list(r.cells_used or []), "tables": len(r.tables or []), "queries": r.queries,
        "queries_blocked": r.queries_blocked, "queries_failed": r.queries_failed, "row_count": r.row_count,
        "columns_withheld": r.columns_withheld, "trust_label": r.trust_label, "trust_score": r.trust_score,
        "duration_ms": r.duration_ms, "exports": exports,
    }


def _filtered(db: Session, period: str, user: str, outcome: str, flag: str, q: str, min_level: Optional[int]):
    query = db.query(CatalogAskLog)
    if period in _PERIODS:
        query = query.filter(CatalogAskLog.created_at >= datetime.now(timezone.utc) - _PERIODS[period])
    if user:
        query = query.filter(CatalogAskLog.asked_by == user) if user != "-" else query.filter(CatalogAskLog.asked_by.is_(None))
    if outcome:
        query = query.filter(CatalogAskLog.outcome == outcome.upper())
    if q:
        query = query.filter(CatalogAskLog.question.ilike(f"%{q.strip()}%"))
    if min_level is not None:
        query = query.filter(CatalogAskLog.label_level >= min_level)
    exported = db.query(CatalogAskExport.ask_id).filter(CatalogAskExport.ask_id == CatalogAskLog.id).exists()
    if flag == "blocked":
        query = query.filter(or_(CatalogAskLog.queries_blocked > 0, CatalogAskLog.outcome == "BLOCKED"))
    elif flag == "withheld":
        query = query.filter(CatalogAskLog.columns_withheld > 0)
    elif flag == "exported":
        query = query.filter(exported)
    elif flag == "attention":
        query = query.filter(or_(CatalogAskLog.queries_blocked > 0, CatalogAskLog.outcome == "BLOCKED",
                                 CatalogAskLog.columns_withheld > 0))
    return query


@router.get("/questions")
def questions(
    period: str = Query("7d"), user: str = Query(""), outcome: str = Query(""), flag: str = Query(""),
    q: str = Query("", max_length=200), min_level: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
) -> Dict[str, Any]:
    base = _filtered(db, period, user, outcome, flag, q, min_level)
    total = base.count()
    rows = base.order_by(CatalogAskLog.created_at.desc()).offset(offset).limit(limit).all()
    ids = [r.id for r in rows]
    exports: Dict[UUID, List[str]] = {}
    if ids:
        for ask_id, fmt in db.query(CatalogAskExport.ask_id, CatalogAskExport.format).filter(
                CatalogAskExport.ask_id.in_(ids)).order_by(CatalogAskExport.created_at):
            exports.setdefault(ask_id, []).append(fmt)

    # Headline counts for the period (ignoring the other filters, so they
    # read as "this week" at a glance).
    period_q = _filtered(db, period, "", "", "", "", None)
    sub = period_q.with_entities(CatalogAskLog.id).subquery()
    kpis = period_q.with_entities(
        func.count(CatalogAskLog.id),
        func.count(func.distinct(CatalogAskLog.asked_by)),
        func.count(CatalogAskLog.id).filter(or_(CatalogAskLog.queries_blocked > 0, CatalogAskLog.outcome == "BLOCKED")),
        func.count(CatalogAskLog.id).filter(CatalogAskLog.columns_withheld > 0),
        func.count(CatalogAskLog.id).filter(CatalogAskLog.outcome == "ANSWERED"),
    ).one()
    n_exports = db.query(func.count(CatalogAskExport.id)).filter(CatalogAskExport.ask_id.in_(sub)).scalar() or 0
    people = [u for (u,) in period_q.with_entities(CatalogAskLog.asked_by).filter(CatalogAskLog.asked_by.isnot(None))
              .distinct().order_by(CatalogAskLog.asked_by).limit(200)]
    return {
        "items": [_item(r, exports.get(r.id, [])) for r in rows],
        "total": total,
        "kpis": {"questions": kpis[0], "people": kpis[1], "blocked": kpis[2], "withheld": kpis[3],
                 "answered": kpis[4], "exports": n_exports},
        "people": people,
    }


@router.get("/questions/{ask_id}")
def question_detail(ask_id: UUID, db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = db.get(CatalogAskLog, ask_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found.")
    ex = db.query(CatalogAskExport).filter(CatalogAskExport.ask_id == r.id).order_by(CatalogAskExport.created_at).all()
    audits = db.query(CatalogAskAudit).filter(CatalogAskAudit.ask_id == r.id).order_by(CatalogAskAudit.created_at).all()
    out = _item(r, [e.format for e in ex])
    out.update({
        "tables_list": list(r.tables or []), "cells_held": list(r.cells_held or []),
        "queries_list": [{
            "sql": a.generated_sql, "verdict": a.guardrail_verdict, "reason": a.rejection_reason, "rows": a.row_count,
            "error": a.error, "database_name": a.database_name, "duration_ms": a.duration_ms,
            "created_at": _iso(a.created_at),
        } for a in audits],
        "exports_list": [{"format": e.format, "asked_by": e.asked_by, "created_at": _iso(e.created_at)} for e in ex],
    })
    return out
