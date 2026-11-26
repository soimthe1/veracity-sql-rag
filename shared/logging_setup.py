"""
Shared logging setup — call once at each process's start (engine, api,
answer, or a one-shot script like scripts/trigger_validation.py).

Every process logs to BOTH stdout (so dev.ps1's live console shows
everything exactly as before) and its own rotating file under
service.log_dir (default "logs/<process_name>.log") — so a problem is
still there to read afterward, even if nobody was watching the terminal
when it happened, without anyone having to reproduce it or paste the
console output by hand.

Rotation is size-based and capped low on purpose (log_max_bytes per
active file + log_backup_count rotated backups — 5 MB + 1 backup = 10 MB
total by default): this is meant to answer "what went wrong recently,"
not be a permanent audit trail. Anything that needs real permanence
already has its own table (catalog_ask_audit, catalog_history) and is
never subject to this rotation.

Level and content are deliberately left to the call sites, which
already log at the right granularity for what matters: logger.info for
cycle summaries/counts (e.g. engine/scheduler.py's per-cycle "N
entities, M changed" lines), logger.warning for a recoverable failure
with its message (e.g. a single profiling query that failed), and
logger.exception for anything unexpected with a full traceback. The
default level (INFO) is deliberately not DEBUG — turning it up only
hides real errors, turning it down to DEBUG would fill the 5 MB cap
with noise within minutes on a busy catalog.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.config import ServiceConfig

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(service_cfg: "ServiceConfig", process_name: str) -> None:
    """`process_name` names this process's own log file (e.g. "engine",
    "api", "answer", "trigger_validation") — each process gets its own
    file so a problem in one is never buried in another's volume."""
    level = getattr(logging, (service_cfg.log_level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    # Idempotent on purpose: api/main.py and answer/main.py both call
    # this at MODULE IMPORT time, and uvicorn --reload re-imports the
    # module on every reload — without clearing existing handlers first,
    # each reload would stack another pair of handlers and every log
    # line would print/write itself N times over.
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if service_cfg.log_to_file:
        try:
            os.makedirs(service_cfg.log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(service_cfg.log_dir, f"{process_name}.log"),
                maxBytes=service_cfg.log_max_bytes,
                backupCount=service_cfg.log_backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:
            # Logging itself must never be why a process fails to
            # start — fall back to console-only and say why.
            root.warning("Could not set up file logging in %s (%s) — continuing with console logging only.",
                         service_cfg.log_dir, exc)
