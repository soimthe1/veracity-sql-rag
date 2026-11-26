"""cells are written level then category ("20B")

Every stored cell TEXT is rewritten from the old category-first spelling
("B20") to level-first ("20B"):

- catalog_ask_log.cells_held / cells_used           "B20" -> "20B"
- catalog_entities / catalog_fields
  .access_review_dismissed                          "B20" -> "20B", "B-" -> "-B", "-20" -> "20-"
- catalog_history (field_changed = access_classification)
  .old_value / new_value                            "B20" -> "20B"; "B0"/"0" (no cell) -> "(unlabeled)"
- app_access_audit (old_value / new_value / detail.ai of cell-valued actions):
    placement, placement_reset, review_accept/dismiss, role_create/update,
    rule_create/update/delete (the cell after "->")

Tables' and columns' own cells are (int level, str category) columns and
need no rewrite; their category columns are upper-cased if needed.

The migration then VERIFIES. If any stored cell is still not level-first,
it fails, so the upgrade rolls back instead of leaving two spellings. The
same check runs at every start (shared/access/cell_check.py).

Revision ID: 0009_cell_spelling_level_first
Revises: 0008_ask_log_and_create
"""
import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_cell_spelling_level_first"
down_revision: Union[str, None] = "0008_ask_log_and_create"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = re.compile(r"^([A-Z])([0-9]{1,4})$")              # "B20"
_OLD_PROPOSAL = re.compile(r"^([A-Z]|-)([0-9]{1,4}|-)$")  # "B20", "B-", "-20", "--"
_OLD_PARTIAL = re.compile(r"^([A-Z]|\*|\(keep\))([0-9]{1,4}|\*|\(keep\))$")  # placement/rule text
_CELL = re.compile(r"^[0-9]{1,4}[A-Z]$")


def _cell(v):
    """One cell token; anything not in the old form is returned unchanged."""
    if v is None:
        return v
    m = _OLD.match(v)
    return f"{m.group(2)}{m.group(1)}" if m else v


def _history_cell(v):
    # old writer: cell_token(level or 0, category or "") — "B0" / "0" meant "not placed"
    if v is None:
        return v
    if re.match(r"^([A-Z]?0|[0-9]{1,4})$", v):
        return "(unlabeled)"
    return _cell(v)


def _proposal(v):
    if v is None:
        return v
    m = _OLD_PROPOSAL.match(v)
    return f"{m.group(2)}{m.group(1)}" if m else v


def _partial(v):
    """placement / placement_reset / rule text: "B20", "B*", "*20", "B(keep)", "(keep)20"."""
    if v is None:
        return v
    m = _OLD_PARTIAL.match(v)
    if not m:
        return _history_cell(v)
    cat, lvl = m.group(1), m.group(2)
    cat = "*" if cat == "(keep)" else cat
    lvl = "*" if lvl == "(keep)" else lvl
    return f"{lvl}{cat}"


def _cells_text(v):
    """role grants summary: "A10 C40" or "(none)"."""
    if v is None or v == "(none)":
        return v
    return " ".join(_cell(t) for t in v.split(" "))


def _rule_text(v):
    """"table: HR rule -> C20"  (the cell is the last token after "->")."""
    if v is None or " -> " not in v:
        return v
    head, _, cell = v.rpartition(" -> ")
    return f"{head} -> {_partial(cell)}"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. category columns: one upper-case letter (a lower-case "b" would never match "B")
    for table, col in (("catalog_entities", "access_category"), ("catalog_entities", "access_ai_category"),
                       ("catalog_fields", "access_category"), ("catalog_fields", "access_ai_category"),
                       ("catalog_access_labels", "category"), ("catalog_access_rules", "set_category"),
                       ("app_role_grants", "category")):
        bind.execute(sa.text(f"UPDATE {table} SET {col} = upper({col}) WHERE {col} <> upper({col})"))

    # 2. ask log cell arrays
    rows = bind.execute(sa.text("SELECT id, cells_held, cells_used FROM catalog_ask_log")).fetchall()
    for rid, held, used in rows:
        new_held = [_cell(c) for c in held] if held is not None else None
        new_used = [_cell(c) for c in used] if used is not None else None
        if new_held != held or new_used != used:
            bind.execute(sa.text("UPDATE catalog_ask_log SET cells_held = :h, cells_used = :u WHERE id = :id"),
                         {"h": new_held, "u": new_used, "id": rid})

    # 3. dismissed AI proposals
    for table in ("catalog_entities", "catalog_fields"):
        rows = bind.execute(sa.text(
            f"SELECT id, access_review_dismissed FROM {table} WHERE access_review_dismissed IS NOT NULL")).fetchall()
        for rid, v in rows:
            nv = _proposal(v)
            if nv != v:
                bind.execute(sa.text(f"UPDATE {table} SET access_review_dismissed = :v WHERE id = :id"),
                             {"v": nv, "id": rid})

    # 4. cell moves in catalog_history
    rows = bind.execute(sa.text(
        "SELECT id, old_value, new_value FROM catalog_history WHERE field_changed = 'access_classification'")).fetchall()
    for rid, old, new in rows:
        no, nn = _history_cell(old), _history_cell(new)
        if (no, nn) != (old, new):
            bind.execute(sa.text("UPDATE catalog_history SET old_value = :o, new_value = :n WHERE id = :id"),
                         {"o": no, "n": nn, "id": rid})

    # 5. access audit rows that carry a cell
    rows = bind.execute(sa.text(
        "SELECT id, action, old_value, new_value, detail FROM app_access_audit WHERE action IN "
        "('placement','placement_reset','review_accept','review_dismiss','role_create','role_update',"
        "'rule_create','rule_update','rule_delete')")).fetchall()
    for rid, action, old, new, detail in rows:
        if action in ("placement", "placement_reset"):
            no, nn = _partial(old), _partial(new)
        elif action in ("review_accept", "review_dismiss"):
            no, nn = _history_cell(old), _history_cell(new)
        elif action in ("role_create", "role_update"):
            no, nn = _cells_text(old), _cells_text(new)
        else:
            no, nn = _rule_text(old), _rule_text(new)
        nd = detail
        if action == "review_dismiss" and isinstance(detail, dict) and isinstance(detail.get("ai"), str):
            nd = {**detail, "ai": _proposal(detail["ai"])}
        if (no, nn, nd) != (old, new, detail):
            bind.execute(sa.text("UPDATE app_access_audit SET old_value = :o, new_value = :n, "
                                 "detail = CAST(:d AS jsonb) WHERE id = :id"),
                         {"o": no, "n": nn, "d": json.dumps(nd or {}), "id": rid})

    # 6. verify — fail the upgrade rather than leave two spellings behind
    bad = []
    for rid, held, used in bind.execute(sa.text("SELECT id, cells_held, cells_used FROM catalog_ask_log")).fetchall():
        for c in (held or []) + (used or []):
            if not _CELL.match(c):
                bad.append(f"catalog_ask_log {rid}: {c!r}")
    for table in ("catalog_entities", "catalog_fields"):
        n = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE access_review_dismissed IS NOT NULL "
            "AND access_review_dismissed !~ '^([0-9]{1,4}|-)([A-Z]|-)$'")).scalar()
        if n:
            bad.append(f"{table}.access_review_dismissed: {n} row(s)")
    n = bind.execute(sa.text(
        "SELECT count(*) FROM catalog_history WHERE field_changed = 'access_classification' AND ("
        "(old_value IS NOT NULL AND old_value !~ '^[0-9]{1,4}[A-Z]$' AND old_value <> '(unlabeled)') OR "
        "(new_value IS NOT NULL AND new_value !~ '^[0-9]{1,4}[A-Z]$' AND new_value <> '(unlabeled)'))")).scalar()
    if n:
        bad.append(f"catalog_history access_classification: {n} row(s)")
    if bad:
        raise RuntimeError("cells still not written level-then-category after migration: " + "; ".join(bad[:20]))


def downgrade() -> None:
    # Deliberately one-way: the old spelling is not to be written again.
    pass
