"""Local (built-in) accounts and sources added from the web UI.

- app_users.password_hash: set for accounts that sign in with a password
  stored here (catalog.auth.mode: local). NULL for directory (LDAP)
  accounts, which never store a password.
- catalog_source_configs: data sources an administrator adds in the web
  UI (Sources > Add source), alongside the ones in config.yaml. The
  password is stored encrypted (shared/secrets.py), never in clear.

Revision ID: 0011_local_accounts_ui_sources
Revises: 0010_cell_check_constraints
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_local_accounts_ui_sources"
down_revision: Union[str, None] = "0010_cell_check_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("password_hash", sa.String(length=512), nullable=True))
    op.create_table(
        "catalog_source_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("host", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("databases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("schemas", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("options", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_catalog_source_configs_name"),
    )


def downgrade() -> None:
    op.drop_table("catalog_source_configs")
    op.drop_column("app_users", "password_hash")
