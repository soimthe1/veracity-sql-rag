"""
Runs guardrail-approved SQL against the real source database. Reuses the
engine's connectors (engine.connectors.make_connector) as-is — the answer
process never opens a second connection path; the same read-only
statement-verb guard, bounded statement timeout, and per-source
concurrency semaphore that protect the engine protect this too.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from engine.connectors import (
    NotReadOnlyError,
    QueryFailedError,
    SourceConnector,
    SourceUnavailableError,
    make_connector,
)
from shared.config import AnswerConfig, SourceConfig

logger = logging.getLogger("answer.executor")

_connectors: Dict[str, SourceConnector] = {}


def get_connector(source_cfg: SourceConfig, answer_cfg: AnswerConfig) -> SourceConnector:
    """One cached connector per source name for the lifetime of this
    process — same one-per-source convention as
    engine.connectors.build_connectors(), just built lazily here
    since the answer process doesn't sweep every configured source up
    front, only the one a given question happens to ground on."""
    conn = _connectors.get(source_cfg.name)
    if conn is None:
        conn = make_connector(
            source_cfg, answer_cfg.statement_timeout_seconds, answer_cfg.max_concurrent_queries_per_source,
        )
        _connectors[source_cfg.name] = conn
    return conn


@dataclass
class ExecutionResult:
    ok: bool
    rows: List[Dict[str, Any]]
    error: Optional[str] = None
    duration_ms: float = 0.0


def execute(source_cfg: SourceConfig, database_name: str, sql: str, answer_cfg: AnswerConfig) -> ExecutionResult:
    """Never raises — a source outage or a bad query becomes
    ExecutionResult(ok=False, ...) so the caller can audit and degrade
    gracefully, same contract as every other engine connector call."""
    connector = get_connector(source_cfg, answer_cfg)
    started = time.perf_counter()
    try:
        rows = connector.query_in_database(database_name, sql, max_rows=answer_cfg.max_rows)
        return ExecutionResult(ok=True, rows=rows, duration_ms=(time.perf_counter() - started) * 1000)
    except (SourceUnavailableError, QueryFailedError, NotReadOnlyError) as exc:
        logger.warning("Ask query execution failed: %s", exc)
        return ExecutionResult(ok=False, rows=[], error=str(exc), duration_ms=(time.perf_counter() - started) * 1000)
