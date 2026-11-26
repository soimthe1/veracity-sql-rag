from __future__ import annotations

from typing import Optional

from shared.models import CatalogEntity, CatalogField, CatalogRelationship, CatalogSource
from api.schemas import EntityDetailOut, EntityOut, FieldOut, RelationshipOut, SourceOut


def serialize_source(src: CatalogSource, database_count: int) -> SourceOut:
    return SourceOut(
        id=src.id, name=src.name, host=src.host, status=src.status, enabled=src.enabled,
        database_count=database_count,
        last_discovery_at=src.last_discovery_at, last_validation_at=src.last_validation_at,
    )


def serialize_field(f: CatalogField) -> FieldOut:
    return FieldOut(
        id=f.id, column_name=f.column_name, data_type=f.data_type, is_primary_key=f.is_primary_key,
        is_nullable=f.is_nullable, semantic_name=f.semantic_name, description=f.description,
        confidence=f.confidence, status=f.status, sensitivity_flags=f.sensitivity_flags or [],
        null_ratio=f.null_ratio, distinct_estimate=f.distinct_estimate,
        access_level=f.access_level, access_category=f.access_category,
        access_level_source=f.access_level_source, access_category_source=f.access_category_source,
        access_review_pending=bool(f.access_review_pending),
    )


def serialize_entity(e: CatalogEntity, source_name: str, database_name: str) -> EntityOut:
    return EntityOut(
        id=e.id, source_name=source_name, database_name=database_name,
        schema_name=e.schema_name, object_name=e.object_name, object_type=e.object_type,
        classification=e.classification, semantic_name=e.semantic_name, description=e.description,
        domain=e.domain, synonyms=e.synonyms or [], confidence=e.confidence, status=e.status,
        deep_analysis=e.deep_analysis, sensitivity_flags=e.sensitivity_flags or [],
        last_verified_at=e.last_verified_at,
        access_level=e.access_level, access_category=e.access_category,
        access_level_source=e.access_level_source, access_category_source=e.access_category_source,
        access_review_pending=bool(e.access_review_pending),
    )


def serialize_entity_detail(e: CatalogEntity, source_name: str, database_name: str,
                             fields: list[CatalogField]) -> EntityDetailOut:
    base = serialize_entity(e, source_name, database_name)
    return EntityDetailOut(**base.model_dump(), fields=[serialize_field(f) for f in fields])


def serialize_relationship(r: CatalogRelationship, from_entity: CatalogEntity, to_entity: CatalogEntity,
                            from_field: Optional[CatalogField], to_field: Optional[CatalogField]) -> RelationshipOut:
    return RelationshipOut(
        id=r.id, from_entity_id=from_entity.id, from_entity_name=from_entity.object_name,
        from_field=from_field.column_name if from_field else None,
        to_entity_id=to_entity.id, to_entity_name=to_entity.object_name,
        to_field=to_field.column_name if to_field else None,
        relationship_label=r.relationship_label, is_declared_fk=r.is_declared_fk,
        confidence=r.confidence, status=r.status, last_verified_at=r.last_verified_at,
    )
