"""database CHECK constraints: a stored cell can only be level-then-category

Migration 0009 rewrote every stored cell to "20B", and the services check
at startup (shared/access/cell_check.py). This adds the same rule to the
database itself, so nothing (a bug, a script, a hand-written UPDATE) can
store another spelling:

- cell text arrays (catalog_ask_log.cells_held / cells_used): every element
  ^[0-9]{1,4}[A-Z]$, via an IMMUTABLE helper catalog_cells_valid(text[])
- dismissed AI proposals: ^([0-9]{1,4}|-)([A-Z]|-)$
- cell moves in catalog_history: a cell or "(unlabeled)"
- every category column: exactly one upper-case letter
- every level column: >= 0

The same constraints are declared on the models (shared/models.py).

Revision ID: 0010_cell_check_constraints
Revises: 0009_cell_spelling_level_first
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010_cell_check_constraints"
down_revision: Union[str, None] = "0009_cell_spelling_level_first"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FN = """
CREATE OR REPLACE FUNCTION catalog_cells_valid(cells text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT coalesce(bool_and(c ~ '^[0-9]{1,4}[A-Z]$'), true) FROM unnest(cells) AS c
$$
"""


_CELL = "'^[0-9]{1,4}[A-Z]$'"
_CAT = "'^[A-Z]$'"


def _cat(col):
    return f"{col} IS NULL OR {col} ~ {_CAT}"


def _lvl(col):
    return f"{col} IS NULL OR {col} >= 0"


def _hist(col):
    return f"{col} IS NULL OR {col} = '(unlabeled)' OR {col} ~ {_CELL}"


# (table, constraint name, expression). Written out here, not imported, so
# this migration never changes; tests/test_access_unit.py checks that
# shared/models.py declares the same list.
CHECKS = [
    ("catalog_entities", "ck_catalog_entities_access_category", _cat("access_category")),
    ("catalog_entities", "ck_catalog_entities_access_ai_category", _cat("access_ai_category")),
    ("catalog_entities", "ck_catalog_entities_access_level", _lvl("access_level")),
    ("catalog_entities", "ck_catalog_entities_access_ai_level", _lvl("access_ai_level")),
    ("catalog_entities", "ck_catalog_entities_review_dismissed",
     "access_review_dismissed IS NULL OR access_review_dismissed ~ '^([0-9]{1,4}|-)([A-Z]|-)$'"),
    ("catalog_fields", "ck_catalog_fields_access_category", _cat("access_category")),
    ("catalog_fields", "ck_catalog_fields_access_ai_category", _cat("access_ai_category")),
    ("catalog_fields", "ck_catalog_fields_access_level", _lvl("access_level")),
    ("catalog_fields", "ck_catalog_fields_access_ai_level", _lvl("access_ai_level")),
    ("catalog_fields", "ck_catalog_fields_review_dismissed",
     "access_review_dismissed IS NULL OR access_review_dismissed ~ '^([0-9]{1,4}|-)([A-Z]|-)$'"),
    ("catalog_history", "ck_catalog_history_cell_values",
     f"field_changed <> 'access_classification' OR (({_hist('old_value')}) AND ({_hist('new_value')}))"),
    ("catalog_ask_log", "ck_catalog_ask_log_cells_held", "catalog_cells_valid(cells_held)"),
    ("catalog_ask_log", "ck_catalog_ask_log_cells_used", "catalog_cells_valid(cells_used)"),
    ("catalog_ask_log", "ck_catalog_ask_log_label_category", _cat("label_category")),
    ("catalog_ask_log", "ck_catalog_ask_log_label_level", _lvl("label_level")),
    ("app_role_grants", "ck_app_role_grants_category", "category ~ " + _CAT),
    ("app_role_grants", "ck_app_role_grants_level", "level >= 0"),
    ("catalog_access_labels", "ck_catalog_access_labels_category", _cat("category")),
    ("catalog_access_labels", "ck_catalog_access_labels_level", _lvl("level")),
    ("catalog_access_rules", "ck_catalog_access_rules_set_category", _cat("set_category")),
    ("catalog_access_rules", "ck_catalog_access_rules_set_level", _lvl("set_level")),
]


def upgrade() -> None:
    op.execute(_FN)
    for table, name, expr in CHECKS:
        op.create_check_constraint(name, table, expr)


def downgrade() -> None:
    for table, name, _expr in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
    op.execute("DROP FUNCTION IF EXISTS catalog_cells_valid(text[])")
