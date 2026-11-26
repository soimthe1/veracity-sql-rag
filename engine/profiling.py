"""
Lightweight, BOUNDED data profiling (spec section 5D/6). Only ever runs
against entities/fields with deep_analysis=True (the coverage knob's
selection) and only when catalog.profiling.enabled is true. Every query
here is TOP-N bounded, reads WITH (NOLOCK) (spec: "no locking
operations" — this is the sanctioned way to satisfy that on SQL Server),
explicitly column-listed (never SELECT *), and runs through the source
connector, which already enforces a statement timeout and a read-only
guard. SQL Server uses the hand-written T-SQL below; every other database
gets the same queries built with SQLAlchemy Core and compiled for its own
dialect (row caps become LIMIT / FETCH FIRST, quoting follows the database).

Two things this module does, both bounded:
1. Column profiling — null ratio, distinct estimate, min/max, bounded
   top values. Informational only; never used to fabricate business
   answers (spec's "Important limitation").
2. Value-overlap testing — the strongest deterministic signal for an
   INFERRED (non-declared) relationship: what fraction of a bounded
   sample of the child column's values actually exist in the candidate
   parent column. Feeds engine/confidence.py's "value_overlap" evidence
   type.

Honesty note: like engine/sql_queries.py, this hasn't been run against
a live SQL Server in this build environment — verify the shape of a
bounded overlap query against your real target before fully trusting
its numbers, same caveat as the discovery queries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import String, case, cast, column, distinct, exists, func, select, table

from engine.connectors import QueryFailedError, SourceConnector, SourceUnavailableError
from shared.config import ProfilingConfig

logger = logging.getLogger("engine.profiling")


def _qualify(schema: str, name: str) -> str:
    return f"[{schema}].[{name}]"


def _qcol(name: str) -> str:
    return f"[{name}]"


# Column types that can't be meaningfully profiled as text: CONVERT to
# nvarchar turns binary bytes into invalid UTF-16 (pyodbc then fails to
# decode the result), and SQL Server refuses COUNT(DISTINCT)/GROUP BY on
# image/xml/spatial/sql_variant outright. They're skipped entirely —
# they never carry the join keys or human-readable values profiling is for.
NON_PROFILABLE_TYPES = frozenset({
    "binary", "varbinary", "image", "timestamp", "rowversion", "xml",
    "geography", "geometry", "hierarchyid", "sql_variant",
    # other databases: binary/LOB, JSON, array and geometric types
    "bytea", "blob", "tinyblob", "mediumblob", "longblob", "raw", "long raw", "bfile",
    "clob", "nclob", "json", "jsonb", "array", "tsvector",
    "point", "line", "lseg", "box", "path", "polygon", "circle",
})
# Legacy LOB text types: can't be DISTINCT'd/GROUP BY'd directly, but
# can be once converted, so they're profiled through a CONVERT.
_LOB_TEXT_TYPES = frozenset({"text", "ntext"})


def is_profilable(data_type: Optional[str]) -> bool:
    return (data_type or "").lower() not in NON_PROFILABLE_TYPES


def _value_expr(column: str, data_type: Optional[str]) -> str:
    if (data_type or "").lower() in _LOB_TEXT_TYPES:
        return f"CONVERT(nvarchar(256), {_qcol(column)})"
    return _qcol(column)


@dataclass
class ColumnProfile:
    ok: bool
    sampled_rows: int = 0
    null_ratio: Optional[float] = None
    distinct_estimate: Optional[int] = None
    sample_min: Optional[str] = None
    sample_max: Optional[str] = None
    top_values: Optional[List[str]] = None
    error: Optional[str] = None


def profile_column(connector: SourceConnector, database: str, schema: str, table: str,
                    column: str, cfg: ProfilingConfig, data_type: Optional[str] = None) -> ColumnProfile:
    if not cfg.enabled:
        return ColumnProfile(ok=False, error="profiling_disabled")
    if not is_profilable(data_type):
        return ColumnProfile(ok=False, error=f"not profilable ({data_type})")
    v = _value_expr(column, data_type)

    n = cfg.max_sample_rows
    generic = connector.kind != "mssql"
    if generic:
        sql, top_sql = _generic_profile_sql(connector, schema, table, column, n)
    else:
        sql = f"""
        SELECT
            COUNT(*) AS sampled,
            SUM(CASE WHEN s.v IS NULL THEN 1 ELSE 0 END) AS null_count,
            COUNT(DISTINCT s.v) AS distinct_count,
            MIN(CONVERT(nvarchar(256), s.v)) AS min_v,
            MAX(CONVERT(nvarchar(256), s.v)) AS max_v
        FROM (
            SELECT TOP ({n}) {v} AS v
            FROM {_qualify(schema, table)} WITH (NOLOCK)
        ) s
    """
    try:
        rows = connector.query_in_database(database, sql)
    except (SourceUnavailableError, QueryFailedError) as exc:
        logger.warning("Profiling failed for %s.%s.%s: %s", schema, table, column, exc)
        return ColumnProfile(ok=False, error=str(exc))

    if not rows or rows[0]["sampled"] in (None, 0):
        return ColumnProfile(ok=True, sampled_rows=0)

    r = rows[0]
    sampled = int(r["sampled"] or 0)
    null_count = int(r["null_count"] or 0)

    if not generic:
        top_sql = f"""
        SELECT TOP (10) CONVERT(nvarchar(256), v) AS v, COUNT(*) AS c
        FROM (SELECT TOP ({n}) {v} AS v FROM {_qualify(schema, table)} WITH (NOLOCK)) s
        WHERE v IS NOT NULL
        GROUP BY v
        ORDER BY COUNT(*) DESC
    """
    top_values: List[str] = []
    try:
        for row in connector.query_in_database(database, top_sql):
            top_values.append(str(row["v"]))
    except (SourceUnavailableError, QueryFailedError) as exc:
        logger.debug("Top-values query failed for %s.%s.%s (non-fatal): %s", schema, table, column, exc)

    return ColumnProfile(
        ok=True,
        sampled_rows=sampled,
        null_ratio=round(null_count / sampled, 4) if sampled else None,
        distinct_estimate=int(r["distinct_count"] or 0),
        sample_min=r["min_v"],
        sample_max=r["max_v"],
        top_values=top_values,
    )


@dataclass
class OverlapResult:
    ok: bool
    sampled: int = 0
    matched: int = 0
    overlap_ratio: Optional[float] = None
    error: Optional[str] = None


def test_value_overlap(connector: SourceConnector, database: str,
                        child_schema: str, child_table: str, child_column: str,
                        parent_schema: str, parent_table: str, parent_column: str,
                        cfg: ProfilingConfig) -> OverlapResult:
    """Bounded: samples up to `bounded_value_overlap_sample_rows` non-null
    values from the child column, then checks (via EXISTS, expected to
    hit an index/seek since parent_column is a PK/candidate-key column)
    what fraction exist in the parent column. Same-database only —
    cross-database relationship inference is out of scope for this
    build (see DECISIONS.md)."""
    if not cfg.enabled:
        return OverlapResult(ok=False, error="profiling_disabled")

    n = cfg.bounded_value_overlap_sample_rows
    # The EXISTS is evaluated per sampled row INSIDE the derived table and
    # only the resulting 0/1 flag is aggregated outside it. SQL Server
    # rejects the more obvious SUM(CASE WHEN EXISTS (...) ...) form with
    # error 130 ("Cannot perform an aggregate function on an expression
    # containing an aggregate or a subquery") — which made every overlap
    # test fail and meant value_overlap evidence was never recorded.
    if connector.kind != "mssql":
        sql = _generic_overlap_sql(connector, child_schema, child_table, child_column,
                                   parent_schema, parent_table, parent_column, n)
    else:
        sql = f"""
        SELECT
            COUNT(*) AS sampled,
            SUM(s.hit) AS matched
        FROM (
            SELECT TOP ({n})
                CASE WHEN EXISTS (
                    SELECT 1 FROM {_qualify(parent_schema, parent_table)} p WITH (NOLOCK)
                    WHERE p.{_qcol(parent_column)} = c.{_qcol(child_column)}
                ) THEN 1 ELSE 0 END AS hit
            FROM {_qualify(child_schema, child_table)} c WITH (NOLOCK)
            WHERE c.{_qcol(child_column)} IS NOT NULL
        ) s
    """
    try:
        rows = connector.query_in_database(database, sql)
    except (SourceUnavailableError, QueryFailedError) as exc:
        logger.warning("Value-overlap test failed for %s.%s -> %s.%s: %s",
                        child_table, child_column, parent_table, parent_column, exc)
        return OverlapResult(ok=False, error=str(exc))

    if not rows or not rows[0]["sampled"]:
        return OverlapResult(ok=True, sampled=0)

    sampled = int(rows[0]["sampled"] or 0)
    matched = int(rows[0]["matched"] or 0)
    return OverlapResult(ok=True, sampled=sampled, matched=matched,
                          overlap_ratio=round(matched / sampled, 4) if sampled else None)


# ---- every non-SQL Server database: SQLAlchemy Core, compiled per dialect ----

def _generic_profile_sql(connector, schema: str, table_name: str, col: str, n: int):
    t = table(table_name, column(col), schema=schema)
    s = select(t.c[col].label("v")).limit(n).subquery("s")
    text_v = cast(s.c.v, String(256))
    stats = select(
        func.count().label("sampled"),
        func.sum(case((s.c.v.is_(None), 1), else_=0)).label("null_count"),
        func.count(distinct(s.c.v)).label("distinct_count"),
        func.min(text_v).label("min_v"),
        func.max(text_v).label("max_v"),
    )
    top = (select(text_v.label("v"), func.count().label("c"))
           .where(s.c.v.is_not(None)).group_by(s.c.v)
           .order_by(func.count().desc()).limit(10))
    return connector.compile(stats), connector.compile(top)


def _generic_overlap_sql(connector, child_schema, child_table, child_column,
                         parent_schema, parent_table, parent_column, n: int) -> str:
    p = table(parent_table, column(parent_column), schema=parent_schema).alias("p")
    c = table(child_table, column(child_column), schema=child_schema).alias("c")
    hit = case((exists().where(p.c[parent_column] == c.c[child_column]), 1), else_=0)
    s = select(hit.label("hit")).where(c.c[child_column].is_not(None)).limit(n).subquery("s")
    return connector.compile(select(func.count().label("sampled"), func.sum(s.c.hit).label("matched")))
