"""
One-shot manual validation cycle — runs exactly what
engine/scheduler.py's validation_cycle() does on its normal 6h-by-
default schedule (catalog.validation.interval_hours), right now, then
exits. Safe to run:

  - Whether or not `python -m engine.main` / `dev.ps1` is already
    running — this is a separate, short-lived process against the same
    catalog Postgres, and every write validation_cycle() makes is
    already scoped per-database with its own commits (see
    engine/scheduler.py's own fault-isolation), so it interleaves
    safely with a concurrently running discovery cycle.
  - To re-run any time you want fresh confidence/coverage/relationship
    inference sooner than the next scheduled cycle — e.g. right after a
    first discovery run, when every entity is still sitting at its
    just-discovered defaults (confidence 0, deep_analysis unset) because
    only discovery has ever run.

Usage:
    python -m scripts.trigger_validation
"""
from __future__ import annotations

import logging
import time

from engine.scheduler import validation_cycle
from shared.config import load_config
from shared.db import init_db
from shared.logging_setup import configure_logging

logger = logging.getLogger("scripts.trigger_validation")


def main() -> None:
    cfg = load_config()
    configure_logging(cfg.service, "trigger_validation")

    if not cfg.catalog.enabled:
        logger.warning("catalog.enabled=false — nothing to validate.")
        return

    sources = cfg.catalog.enabled_sources
    if not sources:
        logger.warning("No enabled+configured sources — nothing to validate.")
        return

    if not init_db(cfg):
        logger.error("Catalog Postgres is not reachable right now — cannot run validation. "
                      "Check CATALOG_DB_* in .env and that Postgres is up.")
        return

    logger.info("Running a manual validation cycle for source(s): %s ...", [s.name for s in sources])
    started = time.monotonic()
    validation_cycle(cfg)
    logger.info("Manual validation cycle finished in %.1fs. Check the web UI's Overview/Entities/Health "
                "tabs — confidence, deep_analysis, and TRUSTED counts should now reflect it.",
                time.monotonic() - started)


if __name__ == "__main__":
    main()
