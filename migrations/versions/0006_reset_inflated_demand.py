"""reset inflated demand counters

Until this revision, answer/grounding.py added a demand hit
(catalog_entities.unanswered_hit_count) to EVERY not-yet-TRUSTED table a
question's search matched — up to ~48 per question, whether or not the
question was then answered. That flagged nearly the whole catalog for
forced deep analysis (the Suggestions tab showed almost every table as
"will force deep analysis") and drowned the real signal.

Demand is now recorded only for questions that end up unanswered, and
only for the few tables each grounding relied on (answer/routers/ask.py).
The old counts can't be recomputed from history, so they're reset once;
real demand builds back up from the next unanswered questions.

Revision ID: 0006_reset_inflated_demand
Revises: 0005_catalog_settings
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_reset_inflated_demand"
down_revision: Union[str, None] = "0005_catalog_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE catalog_entities SET unanswered_hit_count = 0, last_unanswered_at = NULL")


def downgrade() -> None:
    # The inflated values are intentionally not restored.
    pass
