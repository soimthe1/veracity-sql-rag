"""catalog_settings — small live-controllable knob store

Revision ID: 0005_catalog_settings
Revises: 0004_entity_structural_evidence
Create Date: 2026-09-25

Backs shared/settings.py. First consumer: AnswerConfig.min_trust_level —
the "how much trust before Ask will use a table" dial, moved from a
YAML-only setting to one the web UI (Security tab) can change live, with
every already-running process (engine/api/answer) picking it up on its
very next read, no restart needed. See shared/settings.py's module
docstring for why this is a DB table and not just another config.yaml
key: config.yaml still supplies the default.

Generic key/value on purpose (not a single-purpose min_trust_level
column) so the next knob that needs the same "UI-controllable, no
restart" treatment doesn't need its own migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_catalog_settings"
down_revision: Union[str, None] = "0004_entity_structural_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("catalog_settings")
