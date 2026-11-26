"""
Read-only SQL Server connector. Every source-safety rule
is enforced here, in one place, so no
caller can accidentally bypass it:

- read-only: a defensive statement-verb guard rejects anything that
  isn't SELECT (no INSERT/UPDATE/DELETE/DDL/EXEC-of-arbitrary-proc).
- metadata-first: callers are expected to prefer sys.* catalog views/
  DMVs; this module doesn't stop a caller from querying a user table,
  but engine/profiling.py is the only caller that ever does, and only
  in the bounded ways described there.
- bounded statement timeout, per-source concurrency cap (semaphore).
- never crashes a discovery cycle: every query method catches and wraps
  failures in SourceUnavailableError / QueryFailedError rather than
  letting a raw pyodbc exception propagate, so one bad source/table
  never stops processing of everything else (spec section 13).

Two entry points: query_instance() for genuinely instance-wide
catalog views/DMVs, query_in_database() for anything scoped to "whatever
database the connection is currently in" (sys.tables, sys.columns,
sys.foreign_keys, sys.views, sys.procedures, sys.indexes, and similar).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from engine.connectors.base import (  # noqa: F401 - re-exported for older imports
    NotReadOnlyError,
    QueryFailedError,
    SourceConnector,
    SourceUnavailableError,
    _assert_read_only,
)
from engine import sql_queries as q
from shared.config import SourceConfig

logger = logging.getLogger("engine.connectors.mssql")

class MssqlSourceConnector(SourceConnector):
    """One instance per configured SQL Server source. Not thread-safe for a
    single connection — callers needing concurrency should use the semaphore
    from `concurrency_guard()` around whole query operations, matching
    `catalog.profiling.max_concurrent_queries_per_source`."""

    kind = "mssql"

    def _connect(self, database: Optional[str] = None):
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - engine-only dependency
            raise SourceUnavailableError(
                "pyodbc is not installed in this environment"
            ) from exc
        conn_str = self.cfg.odbc_connection_string(database)
        try:
            conn = pyodbc.connect(conn_str, timeout=self.cfg.connect_timeout_seconds, autocommit=True)
            # SQL_ATTR_QUERY_TIMEOUT — bounds every statement on this
            # connection, not just the login. This is the enforcement
            # point for catalog.profiling.statement_timeout_seconds.
            conn.timeout = self.statement_timeout_seconds
            return conn
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailableError(f"Cannot reach {self.cfg.name} ({self.cfg.host}): {exc}") from exc

    def _rows_as_dicts(self, cursor, max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchmany(max_rows) if max_rows else cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def query_instance(self, sql: str, params: Optional[tuple] = None,
                        database: Optional[str] = None, max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
        """Instance-wide catalog views/DMVs (sys.databases,
        sys.dm_exec_*), or anything explicitly scoped by `database` when
        the view itself needs a USE context but isn't per-database
        business logic (rare — prefer query_in_database for that)."""
        _assert_read_only(sql)
        with self.concurrency_guard():
            conn = self._connect(database)
            try:
                cur = conn.cursor()
                cur.execute(sql, params or ())
                return self._rows_as_dicts(cur, max_rows)
            except SourceUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise QueryFailedError(f"[{self.cfg.name}] query failed: {exc}\nSQL: {sql[:200]}") from exc
            finally:
                conn.close()

    def query_in_database(self, database: str, sql: str,
                           params: Optional[tuple] = None, max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
        """For catalog views scoped to 'whatever database the connection
        is currently in' — sys.tables, sys.columns, sys.foreign_keys,
        sys.views, sys.procedures, sys.indexes, sys.dm_db_partition_stats,
        and similar. Always connects with that database as context."""
        return self.query_instance(sql, params, database=database, max_rows=max_rows)

    def load_metadata(self, database: str) -> Dict[str, List[dict]]:
        """Every catalog row discovery needs for one database, from the
        sys.* catalog views in engine/sql_queries.py."""
        run = lambda sql: self.query_in_database(database, sql)  # noqa: E731
        return {
            "tables": run(q.SQL_TABLES), "views": run(q.SQL_VIEWS),
            "procedures": run(q.SQL_PROCEDURES), "functions": run(q.SQL_FUNCTIONS),
            "synonyms": run(q.SQL_SYNONYMS), "columns": run(q.SQL_COLUMNS),
            "primary_keys": run(q.SQL_PRIMARY_KEYS), "foreign_keys": run(q.SQL_FOREIGN_KEYS),
            "indexes": run(q.SQL_INDEXES), "dependencies": run(q.SQL_OBJECT_DEPENDENCIES),
        }

    def resolve_target_databases(self) -> List[str]:
        """`databases_to_catalog: []` (empty, the default) means
        autodiscover every ONLINE, non-system database, so a newly
        created database gets cataloged automatically with zero config
        changes. System databases (master/model/msdb/tempdb) are always
        excluded from autodiscovery since they never hold business
        data; list them explicitly in databases_to_catalog if you
        genuinely want them cataloged."""
        if self.cfg.databases_to_catalog:
            return list(self.cfg.databases_to_catalog)
        rows = self.query_instance(
            "SELECT name FROM sys.databases WHERE state = 0 "
            "AND database_id > 4 AND name NOT IN ('master','model','msdb','tempdb') "
            "ORDER BY name"
        )
        return [r["name"] for r in rows]

    def ping(self) -> bool:
        try:
            self.query_instance("SELECT 1 AS ok")
            return True
        except Exception:  # noqa: BLE001
            return False

