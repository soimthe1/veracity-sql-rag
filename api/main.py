"""
Catalog REST API. Run with:

    uvicorn api.main:app --reload --port 8100

Deliberately a separate process/container from the engine (see
DECISIONS.md) — nothing here ever blocks on a source SQL Server or an
LLM call; it only ever reads the catalog Postgres (already-computed
data) and, for /v1/search and /v1/resolve, optionally reaches a
configured search backend.
"""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.access import require_screen
from api.routers import access, audit, auth, entities, health, resolve, schema_graph, search, settings, sources, status, suggestions
from shared.config import load_config
from shared.db import init_db
from shared.brand import brand
from shared.logging_setup import configure_logging

cfg = load_config()
configure_logging(cfg.service, "api")
logger = logging.getLogger("api.main")

app = FastAPI(
    title=f"{brand()['name']} API",
    description="Small, stable read contract for the autonomous semantic catalog. "
                "See CATALOG_CONSUMER_GUIDE.md for how to use it.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.catalog.api.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public / self-gated: liveness, sign-in (+ its own admin checks), and the
# data access routes (scope for everyone, everything else admin-only).
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(access.router)
app.include_router(audit.router)  # admin-only
# Catalog browsing: only for admins, or for non-admins when the matching
# screen is opened in catalog.access.non_admin_screens (see api/access.py).
_CATALOG = ("entities", "relationships", "glossary")
app.include_router(sources.router, dependencies=[Depends(require_screen("sources", "health"))])
app.include_router(entities.router, dependencies=[Depends(require_screen(*_CATALOG))])
app.include_router(search.router, dependencies=[Depends(require_screen("search", *_CATALOG))])
app.include_router(resolve.router, dependencies=[Depends(require_screen(*_CATALOG))])
app.include_router(status.router, dependencies=[Depends(require_screen("overview", "integrity", "health", "sources"))])
app.include_router(suggestions.router, dependencies=[Depends(require_screen("suggestions"))])
app.include_router(schema_graph.router, dependencies=[Depends(require_screen("relationships"))])
app.include_router(settings.router, dependencies=[Depends(require_screen("sources"))])


@app.on_event("startup")
def on_startup() -> None:
    if not cfg.catalog.enabled:
        logger.info("catalog.enabled=false — API is up but reports DISABLED everywhere.")
        return
    ok = init_db(cfg)
    if not ok:
        logger.warning("Catalog Postgres unreachable at API startup — /health will report DEGRADED "
                        "until it connects; the API process itself stays up.")
        return
    _verify_cell_spelling()
    _announce_setup_code()
    _initial_labeling()


def _announce_setup_code() -> None:
    """Local sign-in with no administrator yet: print the one-time code the
    web UI's setup screen asks for (shared/auth/login.py)."""
    from shared.auth.login import ensure_setup_code, setup_needed
    from shared.db import get_session_factory
    try:
        with get_session_factory(cfg)() as session:
            if setup_needed(session, cfg.catalog.auth):
                logger.warning("FIRST-TIME SETUP: open the web UI and enter setup code %s to create the "
                               "first administrator.", ensure_setup_code(session))
    except Exception:  # noqa: BLE001 - never block startup on this
        logger.exception("Could not prepare the first-time setup code.")


def _verify_cell_spelling() -> None:
    """Refuses to start if any stored cell isn't level-then-category ("20B")."""
    from shared.access.cell_check import verify_cell_spelling
    from shared.db import get_session_factory
    with get_session_factory(cfg)() as session:
        verify_cell_spelling(session, cfg)


def _initial_labeling() -> None:
    """First start after the data-access upgrade (or on a fresh catalog):
    label every table and column under the current rules in a background
    thread, so startup isn't held up on a large catalog. Afterwards labels
    are kept current by every edit and by each engine cycle."""
    import threading

    from shared.access import labeling
    from shared.access.lattice import load_lattice
    from shared.db import get_session_factory

    def run() -> None:
        try:
            with get_session_factory(cfg)() as session:
                lat = load_lattice(cfg)
                if labeling.needs_initial_labeling(session):
                    stats = labeling.recompute(session, cfg, lat, reason="initial labeling")
                    logger.info("Initial data-access labeling done: %s", stats.as_dict())
        except Exception:  # noqa: BLE001 - never takes the API down
            logger.exception("Initial data-access labeling failed (labels are recomputed on the next engine cycle).")

    threading.Thread(target=run, name="initial-labeling", daemon=True).start()
