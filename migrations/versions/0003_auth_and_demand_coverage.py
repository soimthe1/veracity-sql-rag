"""auth, permissions, and demand-driven coverage

Revision ID: 0003_auth_and_demand_coverage
Revises: 0002_access_classification
Create Date: 2026-09-24

Two independent additions, bundled in one migration because they were
designed together:

1. This project's OWN web UI login + (level, category) permission
   system (shared/auth/, api/routers/auth.py) — app_users, app_roles,
   app_role_grants (exact-match grant cells, no inheritance — grants
   attach to roles, not people), app_user_roles (the
   many-to-many membership), app_login_audit (every login attempt,
   success or failure). Entirely inert while catalog.auth.enabled is
   false (the default) — these tables simply stay empty.

2. The demand-driven ("self-correcting") coverage feedback loop —
   catalog_entities.unanswered_hit_count / last_unanswered_at (see
   engine/coverage.py, answer/grounding.py, CoverageConfig.demand_*).
   NOT NULL with a server_default of 0 so this is safe to run against a
   catalog that already has rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_auth_and_demand_coverage"
down_revision: Union[str, None] = "0002_access_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "app_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("username", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "app_role_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=4), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["app_roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "level", "category", name="uq_app_role_grants_cell"),
    )
    op.create_index("ix_app_role_grants_role", "app_role_grants", ["role_id"], unique=False)

    op.create_table(
        "app_user_roles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["app_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    op.create_table(
        "app_login_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("username_attempted", sa.String(length=256), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_login_audit_created_at", "app_login_audit", ["created_at"], unique=False)
    op.create_index("ix_app_login_audit_username", "app_login_audit", ["username_attempted"], unique=False)

    # server_default=0 (not just a Python-side ORM default) so this is
    # safe against a catalog_entities table that already has rows.
    op.add_column(
        "catalog_entities",
        sa.Column("unanswered_hit_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("catalog_entities", sa.Column("last_unanswered_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_catalog_entities_unanswered", "catalog_entities", ["unanswered_hit_count"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_catalog_entities_unanswered", table_name="catalog_entities")
    op.drop_column("catalog_entities", "last_unanswered_at")
    op.drop_column("catalog_entities", "unanswered_hit_count")

    op.drop_table("app_user_roles")
    op.drop_index("ix_app_role_grants_role", table_name="app_role_grants")
    op.drop_table("app_role_grants")
    op.drop_table("app_users")
    op.drop_table("app_roles")
    op.drop_index("ix_app_login_audit_username", table_name="app_login_audit")
    op.drop_index("ix_app_login_audit_created_at", table_name="app_login_audit")
    op.drop_table("app_login_audit")
