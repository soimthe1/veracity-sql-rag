"""Source connectors: native SQL Server (mssql.py) and every other
database through SQLAlchemy (generic.py). make_connector() picks one
from the source's `type`."""
from __future__ import annotations

from typing import Dict

from engine.connectors.base import (  # noqa: F401 - public names
    NotReadOnlyError,
    QueryFailedError,
    SourceConnector,
    SourceUnavailableError,
)


def make_connector(src, statement_timeout_seconds: int = 30, max_concurrent_queries: int = 1) -> SourceConnector:
    if src.type == "mssql" and not src.url:
        from engine.connectors.mssql import MssqlSourceConnector
        return MssqlSourceConnector(src, statement_timeout_seconds, max_concurrent_queries)
    from engine.connectors.generic import GenericSourceConnector
    return GenericSourceConnector(src, statement_timeout_seconds, max_concurrent_queries)


def build_connectors(sources, statement_timeout_seconds: int,
                     max_concurrent_queries: int) -> Dict[str, SourceConnector]:
    """One connector per enabled+configured source. A source with no
    host/url (e.g. the shipped example config) is silently skipped — an
    unconfigured optional module is not a startup error."""
    out: Dict[str, SourceConnector] = {}
    for src in sources:
        if not src.enabled or not src.is_configured:
            continue
        out[src.name] = make_connector(src, statement_timeout_seconds, max_concurrent_queries)
    return out
