"""
GET /v1/suggestions — a transparency view into the demand-driven
("self-correcting") coverage loop (engine/coverage.py, answer/demand.py):
entities the /v1/ask process has repeatedly matched but couldn't ground
on yet, ranked by how much demand they've accumulated. Backs the web
UI's Suggestions tab. Read-only, like everything else in api/ — this
endpoint doesn't itself change coverage; it just shows what
engine/coverage.py's own next cycle is already going to do about it.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.access import filter_entities_query, get_current_principal
from api.deps import get_config, get_db
from api.routers.entities import _names_for
from api.schemas import SuggestionOut
from shared.auth.login import Principal
from shared.config import AppConfig
from shared.models import CatalogEntity

router = APIRouter(prefix="/v1", tags=["suggestions"])


@router.get("/suggestions", response_model=List[SuggestionOut])
def list_suggestions(
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    cfg: AppConfig = Depends(get_config),
    principal: Principal = Depends(get_current_principal),
) -> List[SuggestionOut]:
    q = db.query(CatalogEntity).filter(CatalogEntity.unanswered_hit_count > 0)
    q = filter_entities_query(q, principal)
    entities = q.order_by(CatalogEntity.unanswered_hit_count.desc(), CatalogEntity.last_unanswered_at.desc()).limit(limit).all()

    threshold = cfg.catalog.coverage.demand_promote_threshold
    never_categories = set(cfg.catalog.coverage.never_deep_categories)

    out = []
    for e in entities:
        source_name, database_name = _names_for(db, e)
        out.append(SuggestionOut(
            id=e.id, source_name=source_name, database_name=database_name,
            schema_name=e.schema_name, object_name=e.object_name, classification=e.classification,
            status=e.status, deep_analysis=e.deep_analysis,
            unanswered_hit_count=e.unanswered_hit_count, last_unanswered_at=e.last_unanswered_at,
            will_force_deep_analysis=(
                e.unanswered_hit_count >= threshold and e.classification not in never_categories
            ),
        ))
    return out
