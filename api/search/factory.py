from __future__ import annotations

from api.search.base import SearchProvider
from api.search.postgres_search import PostgresSearchProvider
from shared.config import SearchConfig


def build_search_provider(cfg: SearchConfig) -> SearchProvider:  # noqa: ARG001 - cfg kept for call-site stability
    """Postgres is the only search backend (see DECISIONS.md, "Search:
    Postgres only"). A second backend would be another SearchProvider
    returned here; the call sites (api/routers/search.py,
    api/routers/resolve.py, answer/grounding.py) would not change."""
    return PostgresSearchProvider()
