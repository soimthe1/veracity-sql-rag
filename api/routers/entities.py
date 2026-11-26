from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.access import entity_visible, filter_entities_query, get_current_principal
from shared.access.policy import field_allowed
from api.deps import get_db
from api.schemas import EntityDetailOut, EntityOut, RelationshipOut
from api.serializers import serialize_entity, serialize_entity_detail, serialize_relationship
from shared.auth.login import Principal
from shared.models import CatalogDatabase, CatalogEntity, CatalogField, CatalogRelationship, CatalogSource

router = APIRouter(prefix="/v1/entities", tags=["entities"])


def _names_for(db: Session, entity: CatalogEntity) -> tuple[str, str]:
    database = db.get(CatalogDatabase, entity.database_id)
    source = db.get(CatalogSource, database.source_id) if database else None
    return (source.name if source else "?", database.database_name if database else "?")


@router.get("", response_model=List[EntityOut])
def list_entities(
    query: Optional[str] = Query(None, description="Simple substring filter on object/semantic name"),
    domain: Optional[str] = None,
    database: Optional[str] = Query(None, description="Exact database name"),
    classification: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    object_type: Optional[str] = Query(None, description="TABLE / VIEW / PROCEDURE / FUNCTION / SYNONYM"),
    sensitivity: Optional[str] = Query(None, description="A single sensitivity_flags value, e.g. PII"),
    deep_analysis: Optional[bool] = None,
    trusted_only: bool = Query(False, description="Only return status=TRUSTED entities"),
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> List[EntityOut]:
    q = db.query(CatalogEntity).join(CatalogDatabase).join(CatalogSource)
    q = filter_entities_query(q, principal)
    if query:
        like = f"%{query}%"
        q = q.filter((CatalogEntity.object_name.ilike(like)) | (CatalogEntity.semantic_name.ilike(like)))
    if domain:
        q = q.filter(CatalogEntity.domain == domain)
    if database:
        q = q.filter(CatalogDatabase.database_name == database)
    if classification:
        q = q.filter(CatalogEntity.classification == classification)
    if status:
        q = q.filter(CatalogEntity.status == status)
    if trusted_only:
        q = q.filter(CatalogEntity.status == "TRUSTED")
    if source:
        q = q.filter(CatalogSource.name == source)
    if object_type:
        q = q.filter(CatalogEntity.object_type == object_type)
    if sensitivity:
        q = q.filter(CatalogEntity.sensitivity_flags.any(sensitivity))
    if deep_analysis is not None:
        q = q.filter(CatalogEntity.deep_analysis.is_(deep_analysis))

    entities = q.order_by(CatalogEntity.object_name).offset(offset).limit(limit).all()
    return [serialize_entity(e, *_names_for(db, e)) for e in entities]


@router.get("/{entity_id}", response_model=EntityDetailOut)
def get_entity(
    entity_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)
) -> EntityDetailOut:
    entity = db.get(CatalogEntity, entity_id)
    if entity is None or not entity_visible(entity, principal):
        # Same 404 either way — a caller without the right (level,
        # category) grant cannot distinguish "doesn't exist" from
        # "exists but you can't see it," matching the anti-enumeration
        # posture shared/auth/login.py already applies to login itself.
        raise HTTPException(status_code=404, detail="entity not found")
    fields = db.query(CatalogField).filter_by(entity_id=entity.id).order_by(CatalogField.ordinal_position).all()
    # Column-level realm: a column in a cell the caller doesn't hold is
    # simply not listed (same rule Ask applies before generating SQL).
    fields = [f for f in fields if field_allowed(principal, entity, f)]
    source_name, database_name = _names_for(db, entity)
    return serialize_entity_detail(entity, source_name, database_name, fields)


@router.get("/{entity_id}/relationships", response_model=List[RelationshipOut])
def get_entity_relationships(
    entity_id: UUID,
    trusted_only: bool = Query(False, description="Only return status=TRUSTED relationships — "
                                                     "what CATALOG_CONSUMER_GUIDE.md recommends for autonomous joins"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> List[RelationshipOut]:
    entity = db.get(CatalogEntity, entity_id)
    if entity is None or not entity_visible(entity, principal):
        raise HTTPException(status_code=404, detail="entity not found")

    q = db.query(CatalogRelationship).filter(
        (CatalogRelationship.from_entity_id == entity_id) | (CatalogRelationship.to_entity_id == entity_id)
    )
    if trusted_only:
        q = q.filter(CatalogRelationship.status == "TRUSTED")

    out = []
    for rel in q.all():
        from_entity = db.get(CatalogEntity, rel.from_entity_id)
        to_entity = db.get(CatalogEntity, rel.to_entity_id)
        # Never surface the OTHER end of a relationship if the caller
        # isn't cleared to see it — same exact-match (level, category)
        # rule applied to every entity endpoint here.
        if not (entity_visible(from_entity, principal) and entity_visible(to_entity, principal)):
            continue
        from_field = db.get(CatalogField, rel.from_field_id) if rel.from_field_id else None
        to_field = db.get(CatalogField, rel.to_field_id) if rel.to_field_id else None
        out.append(serialize_relationship(rel, from_entity, to_entity, from_field, to_field))
    return out
