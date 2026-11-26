"""
What every source connector shares, whatever the database: the error
types callers catch, the read-only statement guard, and the per-source
concurrency cap. engine/connectors/mssql.py (native SQL Server) and
engine/connectors/generic.py (everything else, via SQLAlchemy) both
build on this, so no caller can bypass the guard for any database.
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager

from shared.config import SourceConfig

_READ_ONLY_VERB_RE = re.compile(r"^\s*(WITH\s+.+?\bAS\b\s*\(.*?\)\s*)?SELECT\b", re.IGNORECASE | re.DOTALL)
# A query constant is allowed to open with an explanatory `--` line comment
# or `/* ... */` block comment above its actual SELECT (see
# engine/sql_queries.py's SQL_OBJECT_DEPENDENCIES for an example) - strip
# any leading run of those (and surrounding whitespace) before checking
# for the statement verb, so a documented query isn't mistaken for a
# non-SELECT statement. Only ever strips from the very start of the
# string (count=1 below), so this can't be used to hide a real
# non-SELECT statement behind a fake leading comment - whatever verb
# comes right after the last leading comment is still what gets checked.
_LEADING_COMMENT_RE = re.compile(r"^(?:\s*--[^\n]*\n|\s*/\*.*?\*/)*", re.DOTALL)


class SourceUnavailableError(Exception):
    """The source (or the specific database within it) could not be
    reached at all — connection refused, login failed, timeout. Callers
    should mark the source/database DEGRADED and continue with other
    sources, never raise this further up into a process crash."""


class QueryFailedError(Exception):
    """The connection succeeded but a specific query failed (permission
    denied, object doesn't exist, statement timeout). Callers should
    mark the specific object DEGRADED and continue."""


class NotReadOnlyError(Exception):
    """Defensive guard: a caller attempted a non-SELECT statement."""


def _assert_read_only(sql: str) -> None:
    body = _LEADING_COMMENT_RE.sub("", sql, count=1)
    if not _READ_ONLY_VERB_RE.match(body):
        raise NotReadOnlyError(
            "Catalog connector only ever issues SELECT statements. "
            f"Rejected: {sql[:80]!r}..."
        )


class SourceConnector:
    """Base for one connector per configured source. Subclasses provide
    query_in_database(), load_metadata(), resolve_target_databases(),
    ping(), and set `kind` (the SQL dialect name: mssql, postgresql, ...)."""

    kind = "generic"

    def __init__(self, cfg: SourceConfig, statement_timeout_seconds: int = 30,
                 max_concurrent_queries: int = 1):
        self.cfg = cfg
        self.statement_timeout_seconds = statement_timeout_seconds
        self._semaphore = threading.Semaphore(max(1, max_concurrent_queries))

    @contextmanager
    def concurrency_guard(self):
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()
