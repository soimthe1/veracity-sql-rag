"""
Alembic environment. Builds the real DB URL from shared/config.py at
runtime (same source of truth every collector/analyzer/API route uses —
never a second copy of connection logic), so `alembic.ini`'s
sqlalchemy.url can stay blank.

Two ways to point this at a database:
1. Normal app usage (shared/db.py's init_db()) — passes a live `engine`
   through alembic_cfg.attributes["configured_engine"]; this file reuses
   it instead of building a new one.
2. Manual CLI usage (`alembic revision --autogenerate`, `alembic upgrade
   head` from a shell) — set CONFIG_PATH (+ ENV_FILE if not `.env`) to a
   real config, or set ALEMBIC_DATABASE_URL directly to a scratch DB URL
   for generating a migration without touching a real environment.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False is deliberate: fileConfig() defaults
    # to True, which would silently disable every logger NOT listed in
    # alembic.ini's [loggers] section (shared.db, api.main, engine.main,
    # uvicorn's own loggers, ...) the moment this runs. Since this module
    # is called from inside long-running app processes (engine/api call
    # this via shared.db.init_db() at startup, possibly retrying several
    # times on a slow/unreachable catalog Postgres), disabling those
    # loggers on the FIRST attempt would silence every subsequent retry
    # warning and the final error too - exactly the "same two Alembic
    # lines repeat forever with no visible error" symptom this caused.
    #
    # And even with disable_existing_loggers=False, fileConfig() still
    # REPLACES the root logger's handlers with alembic.ini's (stderr
    # only, level WARN) - which silently removed shared/logging_setup.py's
    # stdout + logs/<process>.log handlers the moment init_db() ran at
    # startup. Result: every engine/api/answer log file stayed at 0
    # bytes forever and INFO lines vanished from the console. So
    # alembic.ini's logging is applied ONLY for the standalone `alembic`
    # CLI; when shared.db.init_db() runs migrations in-process (it passes
    # configured_engine), the host process's own logging is left alone.
    if "configured_engine" not in config.attributes:
        fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_url() -> str:
    override = os.environ.get("ALEMBIC_DATABASE_URL")
    if override:
        return override
    from shared.config import load_config
    return load_config().catalog.database.sqlalchemy_url


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configured_engine = config.attributes.get("configured_engine")
    if configured_engine is not None:
        connectable = configured_engine
    else:
        cfg_section = config.get_section(config.config_ini_section) or {}
        cfg_section["sqlalchemy.url"] = _resolve_url()
        connectable = engine_from_config(cfg_section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
