"""
GET /v1/schema-graph — the whole-catalog relationship map: every
visible entity as a node, every relationship between two visible
entities as an edge. Backs the web UI's whole-schema diagram view
(Relationships tab's "Whole schema" mode).

Deliberately separate from GET /v1/entities/{id}/relationships — that
one is a focused "what touches THIS table" query reused by
answer/grounding.py itself; this one is "lay out the whole graph," a
different shape and a different bound. Neither should have to
compromise for the other's use case.

Bounded: entities are capped at `limit`, ranked by relevance_score so a
trim keeps the most meaningful tables rather than an arbitrary slice.
Edges are only ever returned between two entities that are BOTH in the
returned node set — a diagram can't draw an edge to a node it doesn't
have, so a truncated edge is worse than useless.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.access import entity_visible, filter_entities_query, get_current_principal
from api.deps import get_db
from api.routers.entities import _names_for
from api.schemas import SchemaGraphOut
from api.serializers import serialize_entity, serialize_relationship
from shared.auth.login import Principal
from shared.models import CatalogDatabase, CatalogEntity, CatalogField, CatalogRelationship, CatalogSource

router = APIRouter(prefix="/v1", tags=["schema-graph"])


@router.get("/schema-graph", response_model=SchemaGraphOut)
def schema_graph(
    trusted_only: bool = Query(False, description="Only TRUSTED entities and TRUSTED relationships — "
                                                     "the safe-for-autonomous-use slice"),
    classification: Optional[str] = None,
    domain: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = Query(300, le=1000, description="Max entities (nodes) to include, ranked by relevance"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> SchemaGraphOut:
    q = db.query(CatalogEntity)
    q = filter_entities_query(q, principal)
    if trusted_only:
        q = q.filter(CatalogEntity.status == "TRUSTED")
    if classification:
        q = q.filter(CatalogEntity.classification == classification)
    if domain:
        q = q.filter(CatalogEntity.domain == domain)
    if source:
        q = (
            q.join(CatalogDatabase, CatalogEntity.database_id == CatalogDatabase.id)
            .join(CatalogSource, CatalogDatabase.source_id == CatalogSource.id)
            .filter(CatalogSource.name == source)
        )

    total_entities = q.count()
    entities = q.order_by(CatalogEntity.relevance_score.desc()).limit(limit).all()
    entity_by_id = {e.id: e for e in entities}
    nodes = [serialize_entity(e, *_names_for(db, e)) for e in entities]

    edges = []
    if entity_by_id:
        rel_q = db.query(CatalogRelationship).filter(
            CatalogRelationship.from_entity_id.in_(entity_by_id.keys()),
            CatalogRelationship.to_entity_id.in_(entity_by_id.keys()),
        )
        if trusted_only:
            rel_q = rel_q.filter(CatalogRelationship.status == "TRUSTED")

        for rel in rel_q.all():
            from_entity = entity_by_id.get(rel.from_entity_id)
            to_entity = entity_by_id.get(rel.to_entity_id)
            if from_entity is None or to_entity is None:
                continue
            # Defense in depth — entity_by_id was already access-filtered
            # by filter_entities_query above, but this matches the same
            # explicit per-edge check /v1/entities/{id}/relationships
            # makes, so the two endpoints can never silently drift apart
            # on what's visible.
            if not (entity_visible(from_entity, principal) and entity_visible(to_entity, principal)):
                continue
            from_field = db.get(CatalogField, rel.from_field_id) if rel.from_field_id else None
            to_field = db.get(CatalogField, rel.to_field_id) if rel.to_field_id else None
            edges.append(serialize_relationship(rel, from_entity, to_entity, from_field, to_field))

    return SchemaGraphOut(nodes=nodes, edges=edges, total_entities=total_entities)
