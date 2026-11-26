"""
Read-only connector for any database SQLAlchemy can reach. PostgreSQL,
MySQL/MariaDB, Oracle and SQLite work out of the box (their drivers are
in the requirements). Any other database works once its SQLAlchemy
dialect/driver is pip-installed and the source is given a `url`.

Metadata comes from SQLAlchemy's Inspector (tables, views, columns,
primary/foreign keys, indexes) and is returned in the same row shapes as
the native SQL Server queries (engine/sql_queries.py), so discovery does
not care which database it is looking at. Stored procedures and
functions are read from the database's own catalog where it has one
(PostgreSQL, MySQL/MariaDB, Oracle); which tables each view/routine uses
is worked out from its definition text, the same way for every database.

Same safety rules as the SQL Server connector: SELECT-only guard,
bounded statement time, per-source concurrency cap, and on PostgreSQL,
MySQL and SQLite the session itself is put in read-only mode as well.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.engine.reflection import ObjectKind
from sqlalchemy.pool import NullPool

from engine.connectors.base import QueryFailedError, SourceConnector, SourceUnavailableError, _assert_read_only

logger = logging.getLogger("engine.connectors.generic")

# Servers that hold several databases, one picked by the URL: autodiscovery
# lists them, and queries for a database switch the URL to it.
_LIST_DATABASES = {
    "postgresql": "SELECT datname AS name FROM pg_database WHERE NOT datistemplate AND datallowconn "
                  "AND datname NOT IN ('rdsadmin', 'azure_maintenance', 'azure_sys', 'cloudsqladmin') "
                  "ORDER BY datname",
    "mysql": "SELECT schema_name AS name FROM information_schema.schemata WHERE schema_name NOT IN "
             "('mysql', 'information_schema', 'performance_schema', 'sys') ORDER BY schema_name",
}

# Cheap row-count estimates from the database's own statistics (no table scans).
_ROW_COUNTS = {
    "postgresql": "SELECT n.nspname AS schema_name, c.relname AS object_name, "
                  "GREATEST(c.reltuples, 0)::bigint AS row_count FROM pg_class c "
                  "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p')",
    "mysql": "SELECT table_schema AS schema_name, table_name AS object_name, table_rows AS row_count "
             "FROM information_schema.tables WHERE table_type = 'BASE TABLE'",
}

# Stored procedures and functions: schema_name, object_name, routine_type
# (PROCEDURE/FUNCTION), definition (NULL where the database can't give it cheaply).
_ROUTINES = {
    "postgresql": "SELECT n.nspname AS schema_name, p.proname AS object_name, "
                  "CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS routine_type, "
                  "pg_get_functiondef(p.oid) AS definition FROM pg_proc p "
                  "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE p.prokind IN ('f', 'p') "
                  "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
                  "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')",
    "mysql": "SELECT routine_schema AS schema_name, routine_name AS object_name, routine_type, "
             "routine_definition AS definition FROM information_schema.routines WHERE routine_schema = DATABASE()",
    "oracle": "SELECT owner AS schema_name, object_name, object_type AS routine_type, NULL AS definition "
              "FROM all_objects WHERE object_type IN ('PROCEDURE', 'FUNCTION') "
              "AND owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')",
}
_FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\s+((?:[\[\"`]?\w+[\]\"`]?\.)?[\[\"`]?\w+[\]\"`]?)", re.IGNORECASE)

# Dialects whose databases usually spread tables over several schemas.
_MULTI_SCHEMA = {"postgresql", "mssql"}
_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "sys", "guest"}
_SYSTEM_SCHEMA_PREFIXES = ("pg_temp", "pg_toast_temp", "db_")


def _is_system_schema(name: str) -> bool:
    n = (name or "").lower()
    return n in _SYSTEM_SCHEMAS or n.startswith(_SYSTEM_SCHEMA_PREFIXES)


def dialect_of(url) -> str:
    """Normalized dialect name for a SQLAlchemy URL (mariadb counts as mysql)."""
    name = make_url(url).get_backend_name()
    return "mysql" if name == "mariadb" else name


class GenericSourceConnector(SourceConnector):
    def __init__(self, cfg, statement_timeout_seconds: int = 30, max_concurrent_queries: int = 1):
        super().__init__(cfg, statement_timeout_seconds, max_concurrent_queries)
        self._base_url = cfg.sqlalchemy_url()
        self.kind = dialect_of(self._base_url)
        self._engines: Dict[Optional[str], Engine] = {}

    # ---- connections -------------------------------------------------
    def _connect_args(self) -> Dict[str, Any]:
        driver = self._base_url.get_driver_name()
        timeout = self.cfg.connect_timeout_seconds
        if self.kind == "postgresql" and driver == "psycopg2":
            ms = self.statement_timeout_seconds * 1000
            return {"connect_timeout": timeout,
                    "options": f"-c default_transaction_read_only=on -c statement_timeout={ms}"}
        if self.kind == "mysql" and driver == "pymysql":
            return {"connect_timeout": timeout, "read_timeout": self.statement_timeout_seconds,
                    "init_command": "SET SESSION TRANSACTION READ ONLY"}
        return {}

    def _engine(self, database: Optional[str]) -> Engine:
        key = database if self.kind in _LIST_DATABASES else None
        engine = self._engines.get(key)
        if engine is None:
            url = self._base_url.set(database=key) if key else self._base_url
            if key is None and self.kind == "postgresql" and not url.database:
                url = url.set(database="postgres")
            engine = create_engine(url, poolclass=NullPool, connect_args=self._connect_args())
            ms = self.statement_timeout_seconds * 1000

            @event.listens_for(engine, "connect")
            def _session_limits(dbapi_conn, _record):  # noqa: ANN001
                if self.kind == "sqlite":
                    dbapi_conn.execute("PRAGMA query_only = ON")
                elif self.kind == "oracle" and hasattr(dbapi_conn, "call_timeout"):
                    dbapi_conn.call_timeout = ms

            self._engines[key] = engine
        return engine

    def _open(self, database: Optional[str]):
        try:
            return self._engine(database).connect()
        except Exception as exc:  # noqa: BLE001
            where = self._base_url.host or self._base_url.database
            raise SourceUnavailableError(f"Cannot reach {self.cfg.name} ({where}): {exc}") from exc

    @property
    def dialect(self):
        return self._engine(None).dialect

    def compile(self, statement) -> str:
        """SQLAlchemy Core statement -> this database's SQL text."""
        return str(statement.compile(dialect=self.dialect, compile_kwargs={"literal_binds": True}))

    # ---- queries -----------------------------------------------------
    def _run(self, conn, sql: str, params: Optional[tuple] = None, max_rows: Optional[int] = None) -> List[dict]:
        """Runs SQL on the raw DB-API cursor. With no parameters the driver
        leaves the text alone, so generated SQL can contain LIKE '%x%',
        `::casts` or '10:30' (SQLAlchemy's own execute paths would treat
        `%` or `:name` as placeholders on some drivers)."""
        cur = conn.connection.cursor()
        try:
            cur.execute(sql, params) if params else cur.execute(sql)
            names = [d[0] for d in cur.description or []]
            if self.kind == "oracle":  # Oracle upper-cases unquoted aliases
                names = [n.lower() if n.isupper() else n for n in names]
            rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
            return [dict(zip(names, r)) for r in rows]
        finally:
            cur.close()

    def query_in_database(self, database: Optional[str], sql: str, params: Optional[tuple] = None,
                          max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
        _assert_read_only(sql)
        with self.concurrency_guard():
            conn = self._open(database)
            try:
                return self._run(conn, sql, params, max_rows)
            except Exception as exc:  # noqa: BLE001
                raise QueryFailedError(f"[{self.cfg.name}] query failed: {exc}\nSQL: {sql[:200]}") from exc
            finally:
                conn.close()

    def query_instance(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None,
                       max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.query_in_database(database, sql, params, max_rows)

    def resolve_target_databases(self) -> List[str]:
        if self.cfg.databases_to_catalog:
            return list(self.cfg.databases_to_catalog)
        if self.kind in _LIST_DATABASES:
            return [r["name"] for r in self.query_in_database(None, _LIST_DATABASES[self.kind])]
        db = self._base_url.database
        if self.kind == "sqlite" and db:
            return [os.path.splitext(os.path.basename(db))[0]]
        return [db or self.kind]

    def ping(self) -> bool:
        try:
            self._open(None).close()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- metadata ----------------------------------------------------
    def load_metadata(self, database: str) -> Dict[str, List[dict]]:
        with self.concurrency_guard():
            conn = self._open(database)
            try:
                return self._reflect(conn)
            except Exception as exc:  # noqa: BLE001
                raise QueryFailedError(f"[{self.cfg.name}] metadata read failed for {database}: {exc}") from exc
            finally:
                conn.close()

    def _row_counts(self, conn) -> Dict[tuple, int]:
        sql = _ROW_COUNTS.get(self.kind)
        if not sql:
            return {}
        try:
            return {(r["schema_name"], r["object_name"]): int(r["row_count"] or 0)
                    for r in self._run(conn, sql)}
        except Exception as exc:  # noqa: BLE001 - estimates are optional
            logger.debug("Row-count estimates unavailable for %s: %s", self.cfg.name, exc)
            conn.connection.rollback()
            return {}

    def _routines(self, conn, schemas) -> List[dict]:
        sql = _ROUTINES.get(self.kind)
        if not sql:
            return []
        try:
            rows = self._run(conn, sql)
        except Exception as exc:  # noqa: BLE001 - routines are optional evidence
            logger.debug("Routines unavailable for %s: %s", self.cfg.name, exc)
            conn.connection.rollback()
            return []
        out, seen = [], set()
        for r in rows:
            # Oracle reports names upper-case; the Inspector (and so the
            # rest of the catalog) uses SQLAlchemy's lower-case form.
            schema = next((x for x in schemas if str(x).lower() == str(r["schema_name"]).lower()), None)
            key = (schema, str(r["object_name"]).lower())
            if schema is None or key in seen:   # other schema, or an overload of one already seen
                continue
            seen.add(key)
            name = r["object_name"].lower() if self.kind == "oracle" else r["object_name"]
            out.append({**r, "schema_name": schema, "object_name": name})
        return out

    @staticmethod
    def _dependencies(code_objects: List[dict], ids: Dict[tuple, int]) -> List[dict]:
        """Which tables/views each view or routine reads, from the FROM/JOIN
        targets in its definition text. Database-neutral; unqualified names
        resolve within the object's own schema."""
        by_name = {(s.lower() if s else None, n.lower()): oid for (s, n), oid in ids.items()}
        out = set()
        for obj in code_objects:
            for ref in _FROM_JOIN_RE.findall(obj.get("definition") or ""):
                parts = [x.strip('[]"`').lower() for x in ref.split(".")]
                schema = parts[0] if len(parts) == 2 else str(obj["schema_name"]).lower()
                target = by_name.get((schema, parts[-1]))
                if target and target != obj["object_id"]:
                    out.add((obj["object_id"], target))
        return [{"referencing_object_id": a, "referenced_object_id": b} for a, b in sorted(out)]

    def _type_name(self, type_, dialect) -> str:
        try:
            name = str(type_.compile(dialect=dialect))
        except Exception:  # noqa: BLE001 - unknown/unsupported types
            name = type(type_).__name__
        return name.split("(")[0].strip().lower()

    def _reflect(self, conn) -> Dict[str, List[dict]]:
        insp = inspect(conn)
        if self.cfg.schemas:
            schemas = list(self.cfg.schemas)
        elif self.kind in _MULTI_SCHEMA:
            schemas = [s for s in insp.get_schema_names() if not _is_system_schema(s)]
        else:
            schemas = [insp.default_schema_name]

        ids: Dict[tuple, int] = {}

        def oid(schema, name) -> int:
            return ids.setdefault((schema, name), len(ids) + 1)

        counts = self._row_counts(conn)
        out: Dict[str, List[dict]] = {k: [] for k in (
            "tables", "views", "procedures", "functions", "synonyms", "columns",
            "primary_keys", "foreign_keys", "indexes", "dependencies")}

        for schema in schemas:
            for name in insp.get_table_names(schema=schema):
                out["tables"].append({
                    "schema_name": schema, "object_name": name, "object_id": oid(schema, name),
                    "create_date": None, "modify_date": None,
                    "row_count_estimate": counts.get((schema, name), 0),
                })
            for name in insp.get_view_names(schema=schema):
                try:
                    definition = insp.get_view_definition(name, schema=schema)
                except Exception:  # noqa: BLE001
                    definition = None
                out["views"].append({
                    "schema_name": schema, "object_name": name, "object_id": oid(schema, name),
                    "create_date": None, "modify_date": None, "definition": definition,
                })

            for (_, name), cols in insp.get_multi_columns(schema=schema, kind=ObjectKind.ANY).items():
                for pos, c in enumerate(cols, 1):
                    t = c["type"]
                    out["columns"].append({
                        "object_id": oid(schema, name), "ordinal_position": pos, "column_name": c["name"],
                        "data_type": self._type_name(t, conn.dialect),
                        "max_length": getattr(t, "length", None),
                        "precision": getattr(t, "precision", None), "scale": getattr(t, "scale", None),
                        "is_nullable": bool(c.get("nullable", True)),
                        "is_computed": c.get("computed") is not None,
                        "is_identity": c.get("identity") is not None or c.get("autoincrement") is True,
                    })

            for (_, name), pk in insp.get_multi_pk_constraint(schema=schema).items():
                for pos, col in enumerate(pk.get("constrained_columns") or [], 1):
                    out["primary_keys"].append({"object_id": oid(schema, name), "column_name": col, "key_ordinal": pos})

            for (_, name), fks in insp.get_multi_foreign_keys(schema=schema).items():
                for fk in fks:
                    target = oid(fk.get("referred_schema") or schema, fk["referred_table"])
                    pairs = zip(fk["constrained_columns"], fk["referred_columns"])
                    for pos, (child, parent) in enumerate(pairs, 1):
                        out["foreign_keys"].append({
                            "fk_name": fk.get("name"), "parent_object_id": oid(schema, name),
                            "parent_column": child, "referenced_object_id": target,
                            "referenced_column": parent, "ordinal": pos,
                        })

            for (_, name), idxs in insp.get_multi_indexes(schema=schema).items():
                for ix in idxs:
                    if not ix.get("name"):
                        continue
                    out["indexes"].append({
                        "object_id": oid(schema, name), "index_name": ix["name"],
                        "is_unique": bool(ix.get("unique")), "index_type": "INDEX",
                        "key_columns": ",".join(c for c in ix.get("column_names") or [] if c),
                    })

        for r in self._routines(conn, schemas):
            kind = "procedures" if r["routine_type"].upper() == "PROCEDURE" else "functions"
            out[kind].append({
                "schema_name": r["schema_name"], "object_name": r["object_name"],
                "object_id": oid(r["schema_name"], r["object_name"]),
                "create_date": None, "modify_date": None, "definition": r.get("definition"),
            })
        out["dependencies"] = self._dependencies(out["views"] + out["procedures"] + out["functions"], ids)
        return out
