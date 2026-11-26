"""
The one spelling of a cell: cells are written level then category:

    LEVEL then CATEGORY  ->  "20B", "40A", "10C"

(the same order as a .../20/B/... folder convention). It is used in every API
response, stored value, audit row, log line and UI chip.

Input is forgiving on CASE and WHITESPACE only: " 20b " reads as "20B"
(a lower-case letter can only mean one cell, so there is nothing to guess).
ORDER is strict: "B20" is NOT "20B" and is rejected with an error, never
re-read or "fixed". Anything that is not a cell after that normalisation
is an error too, never skipped: a skipped grant is a silent "no access"
(fewer rows, or none), which looks exactly like a real denial. Output and
stored values are always the canonical upper-case form.

Internally a cell is always the (int level, str category) pair.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Tuple

# Canonical (stored/output) form: 1-4 digits, then one upper-case category letter.
# Accepted input: the same, but any letter case and surrounding whitespace.
_INPUT_RE = re.compile(r"^\s*([0-9]{1,4})([A-Za-z])\s*$")
_CATEGORY_RE = re.compile(r"^[A-Z]$")
MAX_LEVEL = 9999
# The same rule as a Postgres regex (used by the stored-value check).
CELL_SQL_PATTERN = "^[0-9]{1,4}[A-Z]$"

# A dismissed AI proposal (catalog_entities/fields.access_review_dismissed):
# a cell, with "-" for an axis the AI didn't propose — "20B", "20-", "-B".
PROPOSAL_SQL_PATTERN = "^([0-9]{1,4}|-)([A-Z]|-)$"

EXAMPLE = "20B"


class CellError(ValueError):
    """A cell token that is not written level-then-category, e.g. "20B"."""


def cell_token(level: int, category: str) -> str:
    """(20, "B") -> "20B"."""
    return f"{int(level)}{(category or '').upper()}"


def cell_text(level: Optional[int], category: Optional[str], missing: str = "(unlabeled)") -> str:
    """For audit/display text about an object that may not be placed yet."""
    if level is None or not category:
        return missing
    return cell_token(level, category)


def partial_cell_text(level: Optional[int], category: Optional[str], wildcard: str = "*") -> str:
    """A placement or rule that may set only one axis, still level first:
    "20B", "20*" (level only), "*B" (category only)."""
    return f"{level if level is not None else wildcard}{(category or wildcard).upper()}"


def parse_cell(token: str) -> Tuple[int, str]:
    """"20B" / "20b" / " 20B " -> (20, "B"). Anything else ("B20", "20:B", "20") raises CellError."""
    m = _INPUT_RE.match(token) if isinstance(token, str) else None
    if not m:
        raise CellError(f"invalid cell {token!r}: a cell is written level then category, e.g. {EXAMPLE}")
    return int(m.group(1)), m.group(2).upper()


def normalize_level(value: Any) -> int:
    """A grant's level from a structured input: 20, "20", " 20 " -> 20.
    Not a whole number in 0..9999 (bool, 20.5, "-1", "x") -> CellError."""
    if isinstance(value, bool):
        raise CellError(f"invalid level {value!r}: a whole number 0-{MAX_LEVEL}, e.g. 20")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 0 <= value <= MAX_LEVEL:
        raise CellError(f"invalid level {value!r}: a whole number 0-{MAX_LEVEL}, e.g. 20")
    return value


def normalize_category(value: Any) -> str:
    """A grant's category from a structured input: "b", " B " -> "B".
    Not exactly one letter A-Z ("", "BB", "1", None) -> CellError."""
    cat = value.strip().upper() if isinstance(value, str) else None
    if not cat or not _CATEGORY_RE.match(cat):
        raise CellError(f"invalid category {value!r}: one letter A-Z, e.g. B")
    return cat


def coerce_grant(value: Any) -> Any:
    """One structured grant, as a server caller may send it,
    -> {"level": int, "category": "B"}. Accepts {"level": 20, "category": "b"},
    {"level": "20", "category": " B "} and a cell string "20b". Anything that
    is not a valid grant raises CellError (a 422 from a pydantic model), never
    skipped. Non-dict/str values (an already-built model) pass through."""
    if isinstance(value, str):
        level, category = parse_cell(value)
        return {"level": level, "category": category}
    if isinstance(value, dict):
        return {"level": normalize_level(value.get("level")), "category": normalize_category(value.get("category"))}
    return value


def parse_cell_list(text: str) -> List[Tuple[int, str]]:
    """"20B,40A" -> [(20, "B"), (40, "A")]. Empty items are skipped (a
    trailing comma); any other bad item raises CellError naming it."""
    return [parse_cell(t.strip()) for t in (text or "").split(",") if t.strip()]


def sort_cells(pairs: Iterable[Tuple[int, str]]) -> List[Tuple[int, str]]:
    return sorted(set(pairs), key=lambda p: (p[0], p[1]))


def cell_tokens(pairs: Iterable[Tuple[int, str]]) -> List[str]:
    """Sorted by level, then category: ["10A", "20B", "100A"]."""
    return [cell_token(lv, cat) for lv, cat in sort_cells(pairs)]
