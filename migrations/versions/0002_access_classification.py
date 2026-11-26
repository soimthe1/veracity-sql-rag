"""provisional access classification + ask audit trail

Revision ID: 0002_access_classification
Revises: 0001_baseline
Create Date: 2026-09-24

Adds the provisional (level, category) access-classification shim
(access_level / access_category / access_classification_provisional) to
catalog_entities and catalog_fields, plus the catalog_ask_audit table
backing the new answer/ process's /v1/ask endpoint. See
engine/access_classification.py.

v_catalog_entities is recreated (CREATE OR REPLACE) with the three new
columns appended at the end — additive and backwards compatible, per
spec section 18 ("stable, backwards-compatible consumer views"); any
existing SELECT * or named-column consumer keeps working unchanged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_access_classification"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ASK_AUDIT_VERDICTS = ("ALLOWED", "REJECTED", "ERROR")


def upgrade() -> None:
    for table in ("catalog_entities", "catalog_fields"):
        op.add_column(table, sa.Column("access_level", sa.Integer))
        op.add_column(table, sa.Column("access_category", sa.String(4)))
        op.add_column(
            table,
            sa.Column("access_classification_provisional", sa.Boolean, nullable=False, server_default=sa.true()),
        )

    op.create_index("ix_catalog_entities_access", "catalog_entities", ["access_level", "access_category"])

    op.create_table(
        "catalog_ask_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("requested_grants", postgresql.JSONB),
        sa.Column("source_name", sa.String(128)),
        sa.Column("database_name", sa.String(256)),
        sa.Column("generated_sql", sa.Text),
        sa.Column("entities_touched", postgresql.ARRAY(sa.String)),
        sa.Column("guardrail_verdict", sa.String(32), nullable=False),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("row_count", sa.Integer),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(f"guardrail_verdict IN {ASK_AUDIT_VERDICTS}", name="ck_catalog_ask_audit_verdict"),
    )
    op.create_index("ix_catalog_ask_audit_created_at", "catalog_ask_audit", ["created_at"])

    op.execute("DROP VIEW IF EXISTS v_catalog_entities")
    op.execute("""
        CREATE VIEW v_catalog_entities AS
        SELECT e.id, e.schema_name, e.object_name, e.object_type, e.classification,
               e.semantic_name, e.description, e.domain, e.confidence, e.status,
               e.deep_analysis, e.sensitivity_flags, e.last_verified_at,
               d.database_name, s.name AS source_name,
               e.access_level, e.access_category, e.access_classification_provisional
        FROM catalog_entities e
        JOIN catalog_databases d ON d.id = e.database_id
        JOIN catalog_sources s ON s.id = d.source_id
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_catalog_entities")
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

    op.drop_index("ix_catalog_ask_audit_created_at", table_name="catalog_ask_audit")
    op.drop_table("catalog_ask_audit")

    op.drop_index("ix_catalog_entities_access", table_name="catalog_entities")
    for table in ("catalog_entities", "catalog_fields"):
        op.drop_column(table, "access_classification_provisional")
        op.drop_column(table, "access_category")
        op.drop_column(table, "access_level")
