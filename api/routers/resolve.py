from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.access import entity_visible, get_current_principal
from api.deps import get_config, get_db
from api.joinpaths import find_join_paths
from api.routers.entities import _names_for
from api.schemas import ResolveOut, ResolveRequest, SearchResultOut
from api.search.factory import build_search_provider
from api.serializers import serialize_entity, serialize_relationship
from shared.auth.login import Principal
from shared.config import AppConfig
from shared.models import CatalogEntity, CatalogField, CatalogRelationship

router = APIRouter(prefix="/v1", tags=["resolve"])


@router.post("/resolve", response_model=ResolveOut)
def resolve(
    req: ResolveRequest, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    principal: Principal = Depends(get_current_principal),
) -> ResolveOut:
    """The one-call entry point spec section 16/"Consumer simplicity"
    describes: a business question in, candidate entities + trusted
    fields/relationships + join paths + confidence/freshness/sensitivity
    out. A consuming AI application should not need to inspect SQL
    Server metadata itself to use this. When catalog.auth.enabled,
    everything below is filtered to the caller's (level, category)
    grants first — same rule api/access.py applies everywhere else."""
    provider = build_search_provider(cfg.catalog.search)
    hits = provider.search(db, req.query, req.limit)

    candidate_entities = []
    concepts = set()
    entity_ids = []
    for hit in hits:
        entity = db.get(CatalogEntity, hit.entity_id)
        if entity is None or not entity_visible(entity, principal):
            continue
        entity_ids.append(entity.id)
        candidate_entities.append(SearchResultOut(entity=serialize_entity(entity, *_names_for(db, entity)),
                                                    score=hit.score))
        if entity.domain:
            concepts.add(entity.domain)
        if entity.semantic_name:
            concepts.add(entity.semantic_name)

    trusted_rels = []
    if entity_ids:
        rels = db.query(CatalogRelationship).filter(
            CatalogRelationship.status == "TRUSTED",
            (CatalogRelationship.from_entity_id.in_(entity_ids)) | (CatalogRelationship.to_entity_id.in_(entity_ids)),
        ).all()
        for rel in rels:
            from_entity = db.get(CatalogEntity, rel.from_entity_id)
            to_entity = db.get(CatalogEntity, rel.to_entity_id)
            if not (entity_visible(from_entity, principal) and entity_visible(to_entity, principal)):
                continue
            from_field = db.get(CatalogField, rel.from_field_id) if rel.from_field_id else None
            to_field = db.get(CatalogField, rel.to_field_id) if rel.to_field_id else None
            trusted_rels.append(serialize_relationship(rel, from_entity, to_entity, from_field, to_field))

    join_paths = [
        jp for jp in find_join_paths(db, entity_ids)
        if all(entity_visible(db.get(CatalogEntity, step.entity_id), principal) for step in jp.steps)
    ]

    untrusted_count = sum(1 for c in candidate_entities if c.entity.status != "TRUSTED")
    notes = (
        "Fields/relationships above status TRUSTED are safe for autonomous use. "
        f"{untrusted_count} of {len(candidate_entities)} candidate entities are not yet TRUSTED "
        "(LEARNING/SUSPECT/UNKNOWN/STALE) — treat their semantic_name/description as a hint, not a "
        "fact, and verify before using them in an automated decision. See CATALOG_CONSUMER_GUIDE.md "
        "for the full graceful-degradation pattern."
        if candidate_entities else
        "No matching entities were found in the catalog for this query."
    )

    return ResolveOut(
        query=req.query,
        business_concepts=sorted(concepts),
        candidate_entities=candidate_entities,
        trusted_relationships=trusted_rels,
        join_paths=join_paths,
        notes=notes,
    )
