from __future__ import annotations

from fastapi import APIRouter, Depends

from answer.deps import get_config
from answer.schemas import AnswerHealthOut
from shared.config import AppConfig
from shared.db import healthcheck
from shared.llm.factory import build_llm_provider

router = APIRouter(tags=["health"])


@router.get("/health", response_model=AnswerHealthOut)
def health(cfg: AppConfig = Depends(get_config)) -> AnswerHealthOut:
    """Always 200 — same graceful-consumer pattern as the catalog API's
    own /health (CATALOG_CONSUMER_GUIDE.md): report status in the body,
    never fail the endpoint itself."""
    if not cfg.catalog.enabled or not cfg.catalog.answer.enabled:
        return AnswerHealthOut(
            status="DISABLED", answer_enabled=False, catalog_db_connected=False, llm_enabled=False,
            require_grants=cfg.catalog.answer.require_grants,
        )
    connected = healthcheck(cfg)
    llm_enabled = build_llm_provider(cfg.catalog.inference) is not None
    return AnswerHealthOut(
        status="HEALTHY" if connected else "DEGRADED",
        answer_enabled=True,
        catalog_db_connected=connected,
        llm_enabled=llm_enabled,
        require_grants=cfg.catalog.answer.require_grants,
    )
