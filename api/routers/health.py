from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_config
from api.schemas import HealthOut
from shared.config import AppConfig
from shared.db import healthcheck

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(cfg: AppConfig = Depends(get_config)) -> HealthOut:
    """Always returns 200 — this endpoint's JOB is to report status, not
    to fail when the catalog is unhealthy. A consumer checks the body,
    per the graceful-consumer pattern in CATALOG_CONSUMER_GUIDE.md."""
    if not cfg.catalog.enabled:
        return HealthOut(status="DISABLED", catalog_enabled=False, catalog_db_connected=False)
    connected = healthcheck(cfg)
    return HealthOut(
        status="HEALTHY" if connected else "DEGRADED",
        catalog_enabled=True,
        catalog_db_connected=connected,
    )
