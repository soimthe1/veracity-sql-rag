"""
Layered guardrails for LLM-generated SQL, on top of (never instead of)
the source connector's own read-only statement-verb guard and the
source login's own DB-level read-only grant (see README.md). None
of these checks is a full SQL parser — each is a bounded, defensive
regex check, documented as such. The real backstop is still the
read-only login; this module's job is to fail LOUD and EARLY, and to
produce an audit trail, not to be provably complete.

Checked, in order (first failure wins):
1. exactly one statement (no second `;`-separated statement smuggled in)
2. starts with SELECT (optionally a WITH ... AS ( ... ) CTE prefix)
3. no deny-listed keyword appears as a whole token anywhere
4. every FROM/JOIN target is in the grounded whitelist (answer/grounding.py)
5. no column outside the caller's data access is referenced (column-level
   realm): SELECT * / t.* is refused on a table with restricted columns,
   a qualified reference (alias.col / table.col) to a restricted column is
   refused, and so is an unqualified one unless another table in the query
   has a permitted column of that name
6. a row cap is present or added: TOP <max_rows> on SQL Server, LIMIT on
   most other databases, FETCH FIRST on Oracle (the connector also stops
   reading after max_rows as a backstop)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

from shared.config import AnswerConfig

_READ_ONLY_VERB_RE = re.compile(r"^\s*(WITH\s+.+?\bAS\b\s*\(.*?\)\s*)?SELECT\b", re.IGNORECASE | re.DOTALL)
# Identifiers may be quoted [like this] (SQL Server), "like this" (standard
# SQL) or `like this` (MySQL): optional opening / closing quote.
_OQ = r"[\[\"`]?"
_CQ = r"[\]\"`]?"
_TABLE_REF_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+{_OQ}([A-Za-z_][\w]*){_CQ}\.{_OQ}([A-Za-z_][\w]*){_CQ}",
    re.IGNORECASE,
)
# schema.table followed by an optional alias ([AS] alias), for column checks
_TABLE_ALIAS_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+{_OQ}([A-Za-z_][\w]*){_CQ}\.{_OQ}([A-Za-z_][\w]*){_CQ}"
    rf"(?:\s+(?:AS\s+)?{_OQ}(?!(?:ON|WHERE|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|OUTER|GROUP|ORDER|HAVING|UNION|WITH"
    rf"|LIMIT|FETCH|OFFSET)\b)([A-Za-z_][\w]*){_CQ})?",
    re.IGNORECASE,
)
_STAR_RE = re.compile(rf"(?:^|[\s,(])(?:({_OQ}[A-Za-z_][\w]*{_CQ})\s*\.\s*)?\*(?=\s*(?:,|\bFROM\b|$))", re.IGNORECASE)
_COUNT_STAR_RE = re.compile(r"\bCOUNT\s*\(\s*(?:DISTINCT\s+)?\*\s*\)", re.IGNORECASE)
_STRING_RE = re.compile(r"N?'(?:[^']|'')*'")
_TOP_RE = re.compile(r"^\s*(WITH\s+.+?\bAS\b\s*\(.*?\)\s*)?SELECT\s+(DISTINCT\s+)?TOP\s*\(?\s*\d+", re.IGNORECASE | re.DOTALL)
_OFFSET_FETCH_RE = re.compile(r"\bOFFSET\s+\d+\s+ROWS\b", re.IGNORECASE)
_WITH_PREFIX_RE = re.compile(r"^\s*(WITH\s+.+?\bAS\b\s*\(.*?\)\s*)?", re.IGNORECASE | re.DOTALL)
_OUTER_SELECT_RE = re.compile(r"(SELECT)(\s+DISTINCT)?\b", re.IGNORECASE)
_LIMIT_TAIL_RE = re.compile(r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*$", re.IGNORECASE)
_FETCH_TAIL_RE = re.compile(r"\bFETCH\s+(?:FIRST|NEXT)\s+(\d+)\s+ROWS?\s+ONLY\s*$", re.IGNORECASE)


def _cap_tail(sql: str, max_rows: int, dialect: str) -> str:
    """Row cap for databases that put it at the end of the statement:
    FETCH FIRST n ROWS ONLY on Oracle, LIMIT n everywhere else. An
    existing smaller cap is kept; a larger one is lowered to max_rows."""
    if dialect == "oracle":
        tail_re, clause = _FETCH_TAIL_RE, f"FETCH FIRST {max_rows} ROWS ONLY"
    else:
        tail_re, clause = _LIMIT_TAIL_RE, f"LIMIT {max_rows}"
    m = tail_re.search(sql)
    if m is None:
        return f"{sql}\n{clause}"
    if int(m.group(1)) <= max_rows:
        return sql
    return sql[:m.start(1)] + str(max_rows) + sql[m.end(1):]


def _insert_top(sql: str, max_rows: int) -> Optional[str]:
    """Inserts `TOP <n>` right after the OUTER statement's `SELECT
    [DISTINCT]` — never the first SELECT found anywhere in the text,
    which for a `WITH cte AS (SELECT ...) SELECT ...` query would
    otherwise cap the CTE's inner row count instead of the final result
    set. Locates the outer SELECT by first skipping the same optional
    `WITH ... AS (...)` prefix the read-only verb check itself
    recognizes, so both checks agree on where the real statement
    begins."""
    prefix = _WITH_PREFIX_RE.match(sql)
    start = prefix.end() if prefix else 0
    m = _OUTER_SELECT_RE.match(sql[start:])
    if not m:
        return None
    insert_at = start + m.end()
    return sql[:insert_at] + f" TOP {max_rows}" + sql[insert_at:]


@dataclass
class GuardrailResult:
    ok: bool
    sql_to_execute: Optional[str] = None
    reason: Optional[str] = None


def _strip_trailing_semicolons(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _has_multiple_statements(sql: str) -> bool:
    """Bounded, not a real parser: a `;` inside a quoted string literal
    would false-positive here. Given this SQL is machine-generated
    against a narrow whitelist (never user-supplied text concatenated
    in), that trade-off favors refusing-when-unsure over parsing
    correctness."""
    return ";" in sql


def _strip_brackets(name: str) -> str:
    return name.strip('[]"`').lower()


def check_columns(sql: str, denied: Dict[Tuple[str, str], Set[str]],
                  allowed: Dict[Tuple[str, str], Set[str]]) -> Optional[str]:
    """Returns a refusal reason when `sql` touches a restricted column, else
    None. Bounded and deliberately conservative (refuses when unsure) —
    string literals are ignored, everything else is treated as code."""
    if not denied:
        return None
    code = _STRING_RE.sub("''", sql)
    in_query: Dict[str, Tuple[str, str]] = {}   # alias/table name -> (schema, table)
    tables: Set[Tuple[str, str]] = set()
    for schema, table, alias in _TABLE_ALIAS_RE.findall(code):
        key = (schema.lower(), table.lower())
        tables.add(key)
        in_query[table.lower()] = key
        if alias:
            in_query[alias.lower()] = key
    restricted_tables = {k for k in tables if denied.get(k)}
    if not restricted_tables:
        return None

    for m in _STAR_RE.finditer(_COUNT_STAR_RE.sub("COUNT(1)", code)):
        qual = m.group(1)
        target = in_query.get(_strip_brackets(qual)) if qual else None
        if (qual and target in restricted_tables) or (not qual and restricted_tables):
            return "SELECT * is not allowed on a table with columns outside your data access — name the columns"

    for key in restricted_tables:
        names_for_key = {n for n, k in in_query.items() if k == key}
        for col in denied[key]:
            esc = re.escape(col)
            for name in names_for_key:
                if re.search(rf"{_OQ}\b{re.escape(name)}{_CQ}\s*\.\s*{_OQ}{esc}{_CQ}(?![\w])", code, re.IGNORECASE):
                    return f"column {key[1]}.{col} is outside your data access"
            other_allowed = any(col in allowed.get(k, set()) for k in tables if k != key)
            if other_allowed:
                continue
            if re.search(rf"(?<![\w.\]\"`]){_OQ}{esc}{_CQ}(?![\w])", code, re.IGNORECASE):
                return f"column {key[1]}.{col} is outside your data access"
    return None


def validate_sql(sql: str, whitelist: Set[Tuple[str, str]], cfg: AnswerConfig,
                 denied_columns: Optional[Dict[Tuple[str, str], Set[str]]] = None,
                 allowed_columns: Optional[Dict[Tuple[str, str], Set[str]]] = None,
                 dialect: str = "mssql") -> GuardrailResult:
    candidate = _strip_trailing_semicolons(sql)
    if not candidate:
        return GuardrailResult(ok=False, reason="empty SQL")

    if _has_multiple_statements(candidate):
        return GuardrailResult(ok=False, reason="multiple statements are not permitted")

    if not _READ_ONLY_VERB_RE.match(candidate):
        return GuardrailResult(ok=False, reason="statement must be a single SELECT (optionally WITH ... AS (...) SELECT)")

    lowered = candidate.lower()
    for keyword in cfg.deny_keywords:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            return GuardrailResult(ok=False, reason=f"denied keyword used: {keyword!r}")

    referenced = {(s.lower(), t.lower()) for s, t in _TABLE_REF_RE.findall(candidate)}
    if not referenced:
        return GuardrailResult(ok=False, reason="no schema-qualified FROM/JOIN target found (tables must be schema.table)")
    outside = referenced - whitelist
    if outside:
        return GuardrailResult(
            ok=False,
            reason=f"references table(s) outside the grounded whitelist: {sorted(outside)}",
        )

    column_problem = check_columns(candidate, denied_columns or {}, allowed_columns or {})
    if column_problem:
        return GuardrailResult(ok=False, reason=column_problem)

    if dialect != "mssql":
        candidate = _cap_tail(candidate, cfg.max_rows, dialect)
    elif not _TOP_RE.match(candidate) and not _OFFSET_FETCH_RE.search(candidate):
        capped = _insert_top(candidate, cfg.max_rows)
        if capped is None:
            return GuardrailResult(ok=False, reason="could not apply the row cap")
        candidate = capped

    return GuardrailResult(ok=True, sql_to_execute=candidate)
