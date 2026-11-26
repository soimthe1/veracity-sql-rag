"""
Catalog engine entrypoint — the process that actually does discovery/
validation on a schedule. Run directly with:

    python -m engine.main

Deliberately a separate process/container from the API (see
DECISIONS.md's "why a separate process, not in-process" note) so a
runaway LLM call or a slow source never has any way to affect the
REST API other applications depend on.

Startup behavior mirrors the module's own "never a hard dependency"
principle: an unreachable catalog Postgres, zero configured sources, or
`catalog.enabled: false` are all handled by idling cleanly, never by
crashing the process or exiting non-zero (a crash-looping container is
worse than an idle one for an optional module).
"""
from __future__ import annotations

import logging
import signal
import time

from engine.scheduler import build_scheduler
from shared.config import load_config
from shared.db import init_db
from shared.logging_setup import configure_logging

logger = logging.getLogger("engine.main")

_shutdown = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _shutdown
    logger.info("Received signal %s, shutting down...", signum)
    _shutdown = True


def main() -> None:
    cfg = load_config()
    configure_logging(cfg.service, "engine")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not cfg.catalog.enabled:
        logger.info("catalog.enabled=false — engine idling, doing nothing (no-op).")
        while not _shutdown:
            time.sleep(5)
        return

    ok = init_db(cfg)
    if not ok:
        logger.warning(
            "Catalog Postgres was not reachable at startup — starting the scheduler anyway; "
            "each cycle will retry independently and the module will report DEGRADED until it connects."
        )
    else:
        # Refuses to start if any stored cell isn't level-then-category ("20B").
        from shared.access.cell_check import verify_cell_spelling
        from shared.db import get_session_factory
        with get_session_factory(cfg)() as session:
            verify_cell_spelling(session, cfg)

    scheduler = build_scheduler(cfg)
    if scheduler is None:
        while not _shutdown:
            time.sleep(5)
        return

    scheduler.start()
    logger.info(
        "Catalog engine started. discovery every %sm, validation every %sh. Sources configured: %s",
        cfg.catalog.discovery.interval_minutes, cfg.catalog.validation.interval_hours,
        [s.name for s in cfg.catalog.enabled_sources] or "(none)",
    )
    try:
        while not _shutdown:
            time.sleep(1)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Catalog engine stopped.")


if __name__ == "__main__":
    main()
