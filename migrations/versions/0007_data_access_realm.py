"""data access realm: bucketing layers, rules, placements, access audit

Adds the data-to-cell mapping layer (shared/access/labeling.py):

- per-object provenance on catalog_entities / catalog_fields (which layer
  decided the level and the category, why, the AI's own proposal, and the
  review state when the AI disagrees with a human decision)
- catalog_access_labels: manual placements ("pins") of a database, schema,
  table or column into a cell
- catalog_access_rules: admin-written bucketing rules
- app_access_audit: every change to who-can-see-what
- app_roles.is_system + a seeded built-in "Administrator" role
- catalog_ask_audit.asked_by, so "recent questions" can be per user

Existing (level, category) values are left in place; the API recomputes
every label under the new rules on its first start after this migration.

Revision ID: 0007_data_access_realm
Revises: 0006_reset_inflated_demand
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_data_access_realm"
down_revision: Union[str, None] = "0006_reset_inflated_demand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVENANCE_COLUMNS = (
    ("access_level_source", sa.String(16), {}),
    ("access_category_source", sa.String(16), {}),
    ("access_detail", postgresql.JSONB(), {"nullable": False, "server_default": sa.text("'{}'::jsonb")}),
    ("access_ai_level", sa.Integer(), {}),
    ("access_ai_category", sa.String(4), {}),
    ("access_review_pending", sa.Boolean(), {"nullable": False, "server_default": sa.false()}),
    ("access_review_dismissed", sa.String(16), {}),
    ("access_signature", sa.String(32), {}),
)


def upgrade() -> None:
    for table in ("catalog_entities", "catalog_fields"):
        for name, type_, kw in _PROVENANCE_COLUMNS:
            op.add_column(table, sa.Column(name, type_, **kw))
        op.create_index(f"ix_{table}_access_review", table, ["access_review_pending"],
                        postgresql_where=sa.text("access_review_pending"))
    op.create_index("ix_catalog_fields_access", "catalog_fields", ["access_level", "access_category"])
    op.execute("CREATE INDEX IF NOT EXISTS ix_catalog_fields_entity ON catalog_fields (entity_id)")

    op.create_table(
        "catalog_access_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("database_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("catalog_databases.id", ondelete="CASCADE")),
        sa.Column("schema_name", sa.String(128)),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("catalog_entities.id", ondelete="CASCADE")),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("catalog_fields.id", ondelete="CASCADE")),
        sa.Column("level", sa.Integer()),
        sa.Column("category", sa.String(4)),
        sa.Column("reason", sa.Text()),
        sa.Column("set_by", sa.String(256)),
        sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("target_type IN ('database', 'schema', 'entity', 'field')", name="ck_catalog_access_labels_target"),
        sa.CheckConstraint("level IS NOT NULL OR category IS NOT NULL", name="ck_catalog_access_labels_value"),
    )
    op.create_index("uq_catalog_access_labels_database", "catalog_access_labels", ["database_id"], unique=True,
                    postgresql_where=sa.text("target_type = 'database'"))
    op.create_index("uq_catalog_access_labels_schema", "catalog_access_labels", ["database_id", "schema_name"], unique=True,
                    postgresql_where=sa.text("target_type = 'schema'"))
    op.create_index("uq_catalog_access_labels_entity", "catalog_access_labels", ["entity_id"], unique=True,
                    postgresql_where=sa.text("target_type = 'entity'"))
    op.create_index("uq_catalog_access_labels_field", "catalog_access_labels", ["field_id"], unique=True,
                    postgresql_where=sa.text("target_type = 'field'"))

    op.create_table(
        "catalog_access_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("source_pattern", sa.String(256)),
        sa.Column("database_pattern", sa.String(256)),
        sa.Column("schema_pattern", sa.String(256)),
        sa.Column("table_pattern", sa.String(256)),
        sa.Column("column_pattern", sa.String(256)),
        sa.Column("object_type", sa.String(16)),
        sa.Column("domain_pattern", sa.String(256)),
        sa.Column("classification", sa.String(32)),
        sa.Column("sensitivity_flag", sa.String(32)),
        sa.Column("set_level", sa.Integer()),
        sa.Column("set_category", sa.String(4)),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('database', 'schema', 'table', 'column')", name="ck_catalog_access_rules_scope"),
        sa.CheckConstraint("set_level IS NOT NULL OR set_category IS NOT NULL", name="ck_catalog_access_rules_value"),
    )

    op.create_table(
        "app_access_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor", sa.String(256)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32)),
        sa.Column("target", sa.Text()),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_app_access_audit_created_at", "app_access_audit", ["created_at"])

    op.add_column("app_roles", sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()))
    bind = op.get_bind()
    exists = bind.execute(sa.text("SELECT 1 FROM app_roles WHERE name = 'Administrator'")).first()
    if not exists:
        bind.execute(
            sa.text("INSERT INTO app_roles (id, name, description, is_admin, is_system) "
                    "VALUES (:id, 'Administrator', :d, true, true)"),
            {"id": uuid.uuid4(), "d": "Built-in. Members manage the whole application and see all data."},
        )
    op.execute("UPDATE app_roles SET is_system = true, is_admin = true WHERE name = 'Administrator'")

    op.add_column("catalog_ask_audit", sa.Column("asked_by", sa.String(256)))
    op.create_index("ix_catalog_ask_audit_asked_by", "catalog_ask_audit", ["asked_by"])


def downgrade() -> None:
    op.drop_index("ix_catalog_ask_audit_asked_by", table_name="catalog_ask_audit")
    op.drop_column("catalog_ask_audit", "asked_by")
    op.execute("DELETE FROM app_roles WHERE name = 'Administrator' AND is_system")
    op.drop_column("app_roles", "is_system")
    op.drop_table("app_access_audit")
    op.drop_table("catalog_access_rules")
    op.drop_table("catalog_access_labels")
    op.drop_index("ix_catalog_fields_access", table_name="catalog_fields")
    for table in ("catalog_entities", "catalog_fields"):
        op.drop_index(f"ix_{table}_access_review", table_name=table)
        for name, _, _ in _PROVENANCE_COLUMNS:
            op.drop_column(table, name)
