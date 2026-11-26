"""
SQLAlchemy engine/session + self-evolving schema bootstrap.

Schema changes are handled by Alembic, applied automatically on process
start via init_db() — never `Base.metadata.create_all()` and never a
hand-run SQL script (manual schema syncing is exactly the drift Alembic
prevents). Both the engine process and the api process call
init_db() at startup — since they can start within moments of each
other, the actual `alembic upgrade head` call is wrapped in a Postgres
session-level advisory lock so they can't race each other's DDL.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from shared.config import AppConfig, load_config

logger = logging.getLogger("shared.db")

_ADVISORY_LOCK_KEY = 913_224_001  # arbitrary fixed 32-bit key, unique to this app

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine(cfg: Optional[AppConfig] = None):
    global _engine
    if _engine is None:
        cfg = cfg or load_config()
        _engine = create_engine(
            cfg.catalog.database.sqlalchemy_url,
            pool_size=cfg.catalog.database.pool_size,
            max_overflow=cfg.catalog.database.pool_max_overflow,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory(cfg: Optional[AppConfig] = None) -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        # autoflush=False deliberately — any code that writes then immediately re-queries the
        # same rows in one transaction must call session.flush() explicitly.
        _SessionLocal = sessionmaker(bind=get_engine(cfg), autoflush=False, autocommit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope(cfg: Optional[AppConfig] = None) -> Iterator[Session]:
    session = get_session_factory(cfg)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_alembic_upgrade(cfg: AppConfig) -> None:
    """Lazy-imports alembic (kept out of the hot path / not needed by
    every caller) and runs `alembic upgrade head` against the catalog DB,
    wrapped in a Postgres advisory lock so two processes starting at once
    (engine + api) can't both attempt the same DDL concurrently."""
    from alembic import command
    from alembic.config import Config as AlembicConfig
    import os

    engine = get_engine(cfg)
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.dirname(here)
            alembic_ini = os.path.join(repo_root, "alembic.ini")
            alembic_cfg = AlembicConfig(alembic_ini)
            alembic_cfg.set_main_option("script_location", os.path.join(repo_root, "migrations"))
            alembic_cfg.attributes["configured_engine"] = engine
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations applied (or already at head).")
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
            conn.commit()


def init_db(cfg: Optional[AppConfig] = None, retry_backoff_seconds: Optional[list] = None) -> bool:
    """Bring the catalog schema to head. Tolerant of a not-yet-reachable
    catalog Postgres — retries on the configured backoff, then gives up
    and returns False so the caller can mark the module DEGRADED instead
    of crashing the whole process (per spec section 2: catalog DB
    unavailable must never take this app, or anything polling it, down
    with it)."""
    cfg = cfg or load_config()
    backoff = retry_backoff_seconds or cfg.catalog.health.catalog_db_retry_backoff_seconds
    last_exc: Optional[Exception] = None
    for i, delay in enumerate([0] + list(backoff)):
        if delay:
            logger.warning("Catalog DB not ready, retrying in %ss (attempt %s)...", delay, i)
            time.sleep(delay)
        try:
            _run_alembic_upgrade(cfg)
            return True
        except Exception as exc:  # noqa: BLE001 - intentionally broad, see docstring
            last_exc = exc
            logger.warning("init_db attempt failed: %s", exc)
    logger.error("Catalog DB unreachable after retries; module will run DEGRADED. Last error: %s", last_exc)
    return False


def healthcheck(cfg: Optional[AppConfig] = None) -> bool:
    try:
        engine = get_engine(cfg)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Catalog DB healthcheck failed: %s", exc)
        return False
