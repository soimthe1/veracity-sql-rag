"""
Catalog Answerer REST API. Run with:

    uvicorn answer.main:app --reload --port 8200

A third, separate process/container alongside engine and api (see
DECISIONS.md's fault-isolation pattern) — it does everything engine and
api each do a piece of (reads the catalog DB like api, talks to a SQL
Server source and an LLM like engine), which is exactly why it is its
own process: a slow LLM call or a stuck source query on THIS endpoint
must never have any way to affect the stable /v1/resolve /v1/search
contract other applications already depend on.

catalog.answer.enabled: false (the default) keeps every route here
alive but inert — see answer/routers/ask.py's own docstring. Nothing
in this project ever wires itself into another system on its own;
turning this on and pointing something at it is a deliberate operator
step (see docs/integration.md).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from answer.routers import ask, create, health, suggest
from shared.config import load_config
from shared.db import init_db
from shared.brand import brand
from shared.logging_setup import configure_logging

cfg = load_config()
configure_logging(cfg.service, "answer")
logger = logging.getLogger("answer.main")

app = FastAPI(
    title=f"{brand()['name']} Answerer",
    description="Optional, loosely-coupled natural-language-to-SQL fallback data source for a "
                "retrieval pipeline or other server caller. See docs/integration.md.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.catalog.answer.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Create downloads: the browser needs the file name.
    expose_headers=["Content-Disposition"],
)

app.include_router(health.router)
app.include_router(ask.router)
app.include_router(suggest.router)
app.include_router(create.router)


@app.on_event("startup")
def on_startup() -> None:
    if not cfg.catalog.enabled or not cfg.catalog.answer.enabled:
        logger.info("catalog.answer.enabled=false — answerer is up but every /v1/ask call returns empty, by design.")
        return
    ok = init_db(cfg)
    if not ok:
        logger.warning("Catalog Postgres unreachable at answerer startup — /health will report DEGRADED "
                        "until it connects; the process itself stays up.")
        return
    # Refuses to start if any stored cell isn't level-then-category ("20B").
    from shared.access.cell_check import verify_cell_spelling
    from shared.db import get_session_factory as _sf
    with _sf(cfg)() as session:
        verify_cell_spelling(session, cfg)
    # catalog.answer.audit_retention_days (0 = keep forever): a daily prune.
    from answer.audit import start_retention_thread
    from shared.db import get_session_factory
    start_retention_thread(get_session_factory(cfg), cfg.catalog.answer.audit_retention_days)
