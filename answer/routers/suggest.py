"""
Question starters for the Ask tab (answer/suggestions.py):

  GET /v1/ask/suggestions — example questions built from the catalog
  GET /v1/ask/scope       — how many tables the caller's cells can query
  GET /v1/ask/recent      — distinct recent questions from the audit trail

Read-only and cheap. Suggestions respect the live trust dial (a question
the dial would refuse to answer isn't suggested); like the rest of the
Ask tab, they show table MEANINGS, never row data.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, tuple_
from sqlalchemy.orm import Session

from answer.access_context import optional_ask_access
from answer.deps import get_config, get_db
from answer.schemas import GrantIn, HistoryOut, RecentQuestionOut, ScopeDatabaseOut, ScopeOut, SuggestedQuestionOut
from answer.suggestions import build_suggestions, question_history, recent_questions
from shared.access.cells import CellError, cell_tokens, parse_cell_list
from shared.config import AppConfig
from shared.models import CatalogDatabase, CatalogEntity
from shared.settings import MIN_TRUST_LEVEL_SETTING_KEY, get_int, min_confidence_for_level

router = APIRouter(prefix="/v1/ask", tags=["ask"])


def _cells_to_grants(cells: str) -> List[GrantIn]:
    """"20B,40A" (or "20b, 40a") -> grants. Order is strict: "B20" is not "20B". A wrongly spelled
    cell is a 400 naming it, never skipped (skipping looks like "no access")."""
    try:
        return [GrantIn(level=lv, category=cat) for lv, cat in parse_cell_list(cells)]
    except CellError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/suggestions", response_model=List[SuggestedQuestionOut])
def suggestions(request: Request, limit: int = Query(8, ge=1, le=60),
                cells: str = Query("", max_length=2000, description="Service callers only: the end user's cells, level then category, e.g. 20B,40A"),
                db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)) -> List[SuggestedQuestionOut]:
    access = optional_ask_access(request, db, cfg)
    grants = _cells_to_grants(cells)   # spelling is checked for every caller
    if access.mode == "service":
        access.grants = grants
    level = get_int(db, MIN_TRUST_LEVEL_SETTING_KEY, cfg.catalog.answer.min_trust_level)
    min_conf = min_confidence_for_level(level, cfg.catalog.confidence)
    # Only suggest what this person could actually get an answer from.
    allowed = None if (access.bypass or access.mode == "open") else access.pairs
    return [SuggestedQuestionOut(**s.__dict__)
            for s in build_suggestions(db, limit=limit, min_confidence=min_conf, allowed_cells=allowed)]


@router.get("/scope", response_model=ScopeOut)
def scope(request: Request,
          cells: str = Query("", max_length=2000, description="Service callers only: the end user's cells, level then category, e.g. 40D"),
          db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)) -> ScopeOut:
    """How many tables these cells can query, and in which databases.
    Use this, not "are there suggestions", to decide whether someone has
    database access: suggestions also need a business name and trust."""
    access = optional_ask_access(request, db, cfg)
    grants = _cells_to_grants(cells)
    if access.mode == "service":
        access.grants = grants
    full = access.bypass or access.mode == "open"
    level = get_int(db, MIN_TRUST_LEVEL_SETTING_KEY, cfg.catalog.answer.min_trust_level)
    min_conf = min_confidence_for_level(level, cfg.catalog.confidence)
    pairs = access.pairs
    out = ScopeOut(cells=cell_tokens(pairs), full_access=full, min_confidence=round(min_conf, 3))
    if not full and not pairs:
        return out
    q = (db.query(CatalogDatabase.database_name, func.count(CatalogEntity.id),
                  func.count(CatalogEntity.id).filter(func.coalesce(CatalogEntity.confidence, 0.0) >= min_conf))
         .join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
         .filter(CatalogEntity.object_type.in_(("TABLE", "VIEW")), CatalogEntity.status != "STALE"))
    if not full:
        q = q.filter(tuple_(CatalogEntity.access_level, CatalogEntity.access_category).in_(sorted(pairs)))
    rows = q.group_by(CatalogDatabase.database_name).order_by(func.count(CatalogEntity.id).desc()).all()
    out.databases = [ScopeDatabaseOut(database_name=d, tables=n, tables_usable=u) for d, n, u in rows]
    out.tables = sum(r[1] for r in rows)
    out.tables_usable = sum(r[2] for r in rows)
    return out


@router.get("/recent", response_model=List[RecentQuestionOut])
def recent(request: Request, limit: int = Query(8, ge=1, le=50), db: Session = Depends(get_db),
           cfg: AppConfig = Depends(get_config)) -> List[RecentQuestionOut]:
    access = optional_ask_access(request, db, cfg)
    if access.mode == "service" and not access.username:
        return []
    return [RecentQuestionOut(**r) for r in recent_questions(db, limit=limit, asked_by=access.username,
                                                             only_mine=access.mode != "open")]


@router.get("/history", response_model=HistoryOut)
def history(request: Request, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
            q: str = Query("", max_length=200), db: Session = Depends(get_db),
            cfg: AppConfig = Depends(get_config)) -> HistoryOut:
    """Everything this person has asked (Ask > Recent questions > View all)."""
    access = optional_ask_access(request, db, cfg)
    if access.mode == "service" and not access.username:
        return HistoryOut()
    items, total = question_history(db, asked_by=access.username, only_mine=access.mode != "open",
                                    limit=limit, offset=offset, search=q or None)
    return HistoryOut(items=[RecentQuestionOut(**i) for i in items], total=total)
