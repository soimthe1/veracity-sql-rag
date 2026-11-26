"""baseline catalog schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-23

Creates every catalog_* table plus the three stable consumer views
(v_catalog_entities, v_catalog_relationships, v_catalog_sources) that
form the read-only database contract described in CATALOG_CONSUMER_GUIDE.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUS_VALUES = (
    "DISCOVERED", "LEARNING", "TRUSTED", "SUSPECT", "STALE", "DEGRADED", "DISABLED", "UNKNOWN",
)
OBJECT_TYPES = ("TABLE", "VIEW", "PROCEDURE", "FUNCTION", "SYNONYM")
CLASSIFICATIONS = (
    "BUSINESS_ENTITY", "TRANSACTIONAL_DATA", "REFERENCE_DATA", "RELATIONSHIP_TABLE",
    "AUDIT_HISTORY", "SYSTEM_TECHNICAL", "STAGING", "TEMPORARY", "ARCHIVE",
    "LOGGING", "CONFIGURATION", "UNKNOWN",
)
SOURCE_STATUS_VALUES = ("DISCOVERED", "HEALTHY", "DEGRADED", "UNAVAILABLE", "DISABLED")
EVIDENCE_TYPES = (
    "explicit_fk", "pk_fk_datatype_compatible", "value_overlap", "uniqueness_cardinality",
    "view_definition_confirms", "procedure_usage_confirms", "executed_join_evidence",
    "naming_similarity", "llm_semantic_interpretation", "contradictory_evidence",
)


def upgrade() -> None:
    op.create_table(
        "catalog_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("host", sa.String(256), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="1433"),
        sa.Column("status", sa.String(32), nullable=False, server_default="DISCOVERED"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_discovery_at", sa.DateTime(timezone=True)),
        sa.Column("last_validation_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(f"status IN {SOURCE_STATUS_VALUES}", name="ck_catalog_sources_status"),
    )

    op.create_table(
        "catalog_databases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("database_name", sa.String(256), nullable=False),
        sa.Column("is_online", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("schema_fingerprint", sa.String(64)),
        sa.Column("entity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_id", "database_name", name="uq_catalog_databases_source_db"),
    )

    op.create_table(
        "catalog_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("database_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_databases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_name", sa.String(128), nullable=False),
        sa.Column("object_name", sa.String(256), nullable=False),
        sa.Column("object_type", sa.String(16), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("relevance_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("deep_analysis", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("semantic_name", sa.String(256)),
        sa.Column("description", sa.Text),
        sa.Column("domain", sa.String(128)),
        sa.Column("synonyms", postgresql.ARRAY(sa.String)),
        sa.Column("primary_key_fields", postgresql.ARRAY(sa.String)),
        sa.Column("row_count_estimate", sa.Integer),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="DISCOVERED"),
        sa.Column("evidence_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sensitivity_flags", postgresql.ARRAY(sa.String)),
        sa.Column("definition_hash", sa.String(64)),
        sa.Column("object_fingerprint", sa.String(64)),
        sa.Column("properties", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("database_id", "schema_name", "object_name", name="uq_catalog_entities_object"),
        sa.CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_entities_status"),
        sa.CheckConstraint(f"object_type IN {OBJECT_TYPES}", name="ck_catalog_entities_object_type"),
        sa.CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="ck_catalog_entities_classification"),
    )
    op.create_index("ix_catalog_entities_semantic_name", "catalog_entities", ["semantic_name"])
    op.create_index("ix_catalog_entities_domain", "catalog_entities", ["domain"])
    op.create_index("ix_catalog_entities_status", "catalog_entities", ["status"])
    op.create_index("ix_catalog_entities_deep_analysis", "catalog_entities", ["deep_analysis"])

    op.create_table(
        "catalog_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("column_name", sa.String(256), nullable=False),
        sa.Column("ordinal_position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("data_type", sa.String(64), nullable=False),
        sa.Column("max_length", sa.Integer),
        sa.Column("is_nullable", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_primary_key", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_computed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("semantic_name", sa.String(256)),
        sa.Column("description", sa.Text),
        sa.Column("null_ratio", sa.Float),
        sa.Column("distinct_estimate", sa.Integer),
        sa.Column("sample_min", sa.String(256)),
        sa.Column("sample_max", sa.String(256)),
        sa.Column("top_values", postgresql.ARRAY(sa.String)),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="DISCOVERED"),
        sa.Column("sensitivity_flags", postgresql.ARRAY(sa.String)),
        sa.Column("properties", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("entity_id", "column_name", name="uq_catalog_fields_column"),
        sa.CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_fields_status"),
    )
    op.create_index("ix_catalog_fields_semantic_name", "catalog_fields", ["semantic_name"])

    op.create_table(
        "catalog_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_field_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_fields.id", ondelete="SET NULL")),
        sa.Column("to_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_field_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_fields.id", ondelete="SET NULL")),
        sa.Column("relationship_label", sa.String(256)),
        sa.Column("is_declared_fk", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="DISCOVERED"),
        sa.Column("evidence_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("evidence_summary", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("from_entity_id", "from_field_id", "to_entity_id", "to_field_id",
                             name="uq_catalog_relationships_edge"),
        sa.CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_relationships_status"),
    )
    op.create_index("ix_catalog_relationships_from", "catalog_relationships", ["from_entity_id"])
    op.create_index("ix_catalog_relationships_to", "catalog_relationships", ["to_entity_id"])

    op.create_table(
        "catalog_join_paths",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_entity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hop_count", sa.Integer, nullable=False),
        sa.Column("path", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="LEARNING"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_join_paths_status"),
    )
    op.create_index("ix_catalog_join_paths_from_to", "catalog_join_paths", ["from_entity_id", "to_entity_id"])

    op.create_table(
        "catalog_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("weight_applied", sa.Float, nullable=False, server_default="0"),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("subject_type IN ('entity','field','relationship')",
                            name="ck_catalog_evidence_subject_type"),
        sa.CheckConstraint(f"evidence_type IN {EVIDENCE_TYPES}", name="ck_catalog_evidence_type"),
    )
    op.create_index("ix_catalog_evidence_subject", "catalog_evidence", ["subject_type", "subject_id"])

    op.create_table(
        "catalog_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_type", sa.String(64), nullable=False),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_catalog_observations_subject", "catalog_observations", ["subject_type", "subject_id"])

    op.create_table(
        "catalog_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_changed", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("old_confidence", sa.Float),
        sa.Column("new_confidence", sa.Float),
        sa.Column("reason", sa.Text),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_catalog_history_subject", "catalog_history", ["subject_type", "subject_id"])
    op.create_index("ix_catalog_history_changed_at", "catalog_history", ["changed_at"])

    op.create_table(
        "catalog_health",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("component", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="DISCOVERED"),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Stable, backwards-compatible consumer views (spec section 18) — the
    # long-lived contract for a direct read-only Postgres consumer that
    # never needs to know the underlying table shape.
    op.execute("""
        CREATE VIEW v_catalog_sources AS
        SELECT s.id, s.name, s.host, s.status, s.enabled,
               s.last_discovery_at, s.last_validation_at,
               count(DISTINCT d.id) AS database_count
        FROM catalog_sources s
        LEFT JOIN catalog_databases d ON d.source_id = s.id
        GROUP BY s.id
    """)

    op.execute("""
        CREATE VIEW v_catalog_entities AS
        SELECT e.id, e.schema_name, e.object_name, e.object_type, e.classification,
               e.semantic_name, e.description, e.domain, e.confidence, e.status,
               e.deep_analysis, e.sensitivity_flags, e.last_verified_at,
               d.database_name, s.name AS source_name
        FROM catalog_entities e
        JOIN catalog_databases d ON d.id = e.database_id
        JOIN catalog_sources s ON s.id = d.source_id
    """)

    op.execute("""
        CREATE VIEW v_catalog_relationships AS
        SELECT r.id, r.relationship_label, r.is_declared_fk, r.confidence, r.status,
               r.last_verified_at,
               fe.object_name AS from_entity, ff.column_name AS from_field,
               te.object_name AS to_entity, tf.column_name AS to_field
        FROM catalog_relationships r
        JOIN catalog_entities fe ON fe.id = r.from_entity_id
        JOIN catalog_entities te ON te.id = r.to_entity_id
        LEFT JOIN catalog_fields ff ON ff.id = r.from_field_id
        LEFT JOIN catalog_fields tf ON tf.id = r.to_field_id
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_catalog_relationships")
    op.execute("DROP VIEW IF EXISTS v_catalog_entities")
    op.execute("DROP VIEW IF EXISTS v_catalog_sources")
    op.drop_table("catalog_health")
    op.drop_table("catalog_history")
    op.drop_table("catalog_observations")
    op.drop_table("catalog_evidence")
    op.drop_table("catalog_join_paths")
    op.drop_table("catalog_relationships")
    op.drop_table("catalog_fields")
    op.drop_table("catalog_entities")
    op.drop_table("catalog_databases")
    op.drop_table("catalog_sources")
