from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.access import entity_visible, get_current_principal
from api.deps import get_config, get_db
from api.routers.entities import _names_for
from api.schemas import SearchRequest, SearchResultOut
from api.search.factory import build_search_provider
from api.serializers import serialize_entity
from shared.auth.login import Principal
from shared.config import AppConfig
from shared.models import CatalogEntity

router = APIRouter(prefix="/v1", tags=["search"])


@router.post("/search", response_model=List[SearchResultOut])
def search(
    req: SearchRequest, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    principal: Principal = Depends(get_current_principal),
) -> List[SearchResultOut]:
    provider = build_search_provider(cfg.catalog.search)
    hits = provider.search(db, req.query, req.limit)
    out = []
    for hit in hits:
        entity = db.get(CatalogEntity, hit.entity_id)
        if entity is None or not entity_visible(entity, principal):
            continue
        out.append(SearchResultOut(entity=serialize_entity(entity, *_names_for(db, entity)), score=hit.score))
    return out
