"""
Startup check: every stored cell is spelled level then category ("20B").

A wrong spelling fails silently (fewer rows, or none, which looks like "no
access"), so both services REFUSE TO START when they find one, and name
where it is. Migration 0009 rewrote the old "B20" spelling. Finding one
now means something wrote it since then, and that is a bug to fix, not
to paper over.

Also checked: category columns hold exactly one upper-case letter, and
config/levels.yaml's cell_labels (loading the lattice raises on "A10").
"""
from __future__ import annotations

import logging
from typing import List

from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.access.cells import CELL_SQL_PATTERN, PROPOSAL_SQL_PATTERN

logger = logging.getLogger("shared.access.cell_check")

_CATEGORY_COLUMNS = (
    ("catalog_entities", "access_category"), ("catalog_entities", "access_ai_category"),
    ("catalog_fields", "access_category"), ("catalog_fields", "access_ai_category"),
    ("catalog_access_labels", "category"), ("catalog_access_rules", "set_category"),
    ("app_role_grants", "category"),
)


class CellSpellingError(RuntimeError):
    pass


def find_misspelled_cells(session: Session) -> List[str]:
    """One line per problem, with up to 3 example values."""
    problems: List[str] = []

    def check(where: str, sql: str, **params) -> None:
        rows = session.execute(text(sql), {"cell": CELL_SQL_PATTERN, **params}).fetchall()
        if rows:
            n = rows[0][1]
            examples = ", ".join(repr(r[0]) for r in rows[:3])
            problems.append(f"{where}: {n} value(s) not written level-then-category, e.g. {examples}")

    for col in ("cells_held", "cells_used"):
        check(f"catalog_ask_log.{col}",
              f"SELECT c, count(*) OVER () FROM catalog_ask_log, unnest({col}) AS c WHERE c !~ :cell LIMIT 3")
    for table in ("catalog_entities", "catalog_fields"):
        check(f"{table}.access_review_dismissed",
              f"SELECT access_review_dismissed, count(*) OVER () FROM {table} "
              "WHERE access_review_dismissed IS NOT NULL AND access_review_dismissed !~ :prop LIMIT 3",
              prop=PROPOSAL_SQL_PATTERN)
    for col in ("old_value", "new_value"):
        check(f"catalog_history.{col} (access_classification)",
              f"SELECT {col}, count(*) OVER () FROM catalog_history WHERE field_changed = 'access_classification' "
              f"AND {col} IS NOT NULL AND {col} <> '(unlabeled)' AND {col} !~ :cell LIMIT 3")
    for table, col in _CATEGORY_COLUMNS:
        check(f"{table}.{col}",
              f"SELECT {col}, count(*) OVER () FROM {table} WHERE {col} IS NOT NULL AND {col} !~ '^[A-Z]$' LIMIT 3")
    return problems


def verify_cell_spelling(session: Session, cfg=None) -> None:
    """Raises CellSpellingError (the service does not start) on any problem."""
    problems = find_misspelled_cells(session)
    if cfg is not None:
        from shared.access.lattice import load_lattice
        try:
            load_lattice(cfg)
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        msg = ("Cells must be written level then category (e.g. 20B). Found:\n  - "
               + "\n  - ".join(problems))
        logger.error(msg)
        raise CellSpellingError(msg)
    logger.info("Cell spelling check passed: every stored cell is level then category.")
