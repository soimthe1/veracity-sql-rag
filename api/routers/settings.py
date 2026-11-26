"""
GET/PUT the small set of knobs the web UI is meant to control live (see
shared/settings.py's module docstring for why these live in Postgres
rather than only in config.yaml). Today: just the Ask trust dial
(AnswerConfig.min_trust_level / answer/grounding.py's
min_confidence_for_level()) — a 0 (use everything) to 10 (require
confirmed TRUSTED-equivalent evidence) level, changeable from the
Security tab without editing YAML or restarting anything. Plus the
catalog-depth knob (catalog.coverage.deep_analysis_target_pct /
engine/coverage.py): how much of the catalog gets expensive deep
analysis — i.e. how "heavy" the catalog is.

Read is open to any authenticated principal (or the unrestricted
principal when catalog.auth is disabled) — it's just a number, and the
Ask tab itself may want to show it. Writing it is an admin action, same
bar as api/routers/auth.py's role management.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.access import get_current_principal, require_admin
from api.deps import get_config, get_db
from api.schemas import CoverageTargetIn, CoverageTargetOut, TrustLevelIn, TrustLevelOut
from shared.auth.login import Principal
from shared.config import AppConfig
from shared.models import CatalogSetting
from shared.settings import (
    COVERAGE_TARGET_PCT_SETTING_KEY,
    MIN_TRUST_LEVEL_SETTING_KEY,
    clamp_coverage_pct,
    get_float,
    get_int,
    min_confidence_for_level,
    set_setting,
)

router = APIRouter(prefix="/v1/settings", tags=["settings"])


@router.get("/trust-level", response_model=TrustLevelOut)
def get_trust_level(
    db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    _: Principal = Depends(get_current_principal),
) -> TrustLevelOut:
    row = db.query(CatalogSetting).filter_by(key=MIN_TRUST_LEVEL_SETTING_KEY).one_or_none()
    level = get_int(db, MIN_TRUST_LEVEL_SETTING_KEY, cfg.catalog.answer.min_trust_level)
    return TrustLevelOut(
        level=level,
        min_confidence=round(min_confidence_for_level(level, cfg.catalog.confidence), 3),
        trusted_threshold=cfg.catalog.confidence.trusted_threshold,
        source="override" if row is not None else "yaml_default",
    )


@router.put("/trust-level", response_model=TrustLevelOut)
def set_trust_level(
    body: TrustLevelIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    _: Principal = Depends(require_admin),
) -> TrustLevelOut:
    level = max(0, min(10, body.level))
    set_setting(db, MIN_TRUST_LEVEL_SETTING_KEY, level)
    db.commit()
    return TrustLevelOut(
        level=level,
        min_confidence=round(min_confidence_for_level(level, cfg.catalog.confidence), 3),
        trusted_threshold=cfg.catalog.confidence.trusted_threshold,
        source="override",
    )


def _coverage_out(cfg: AppConfig, pct: float, source: str) -> CoverageTargetOut:
    return CoverageTargetOut(
        target_pct=round(pct, 1),
        min_deep_objects_per_source=cfg.catalog.coverage.min_deep_objects_per_source,
        max_deep_objects_per_source=cfg.catalog.coverage.max_deep_objects_per_source,
        source=source,
    )


@router.get("/coverage-target", response_model=CoverageTargetOut)
def get_coverage_target(
    db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    _: Principal = Depends(get_current_principal),
) -> CoverageTargetOut:
    row = db.query(CatalogSetting).filter_by(key=COVERAGE_TARGET_PCT_SETTING_KEY).one_or_none()
    pct = clamp_coverage_pct(get_float(db, COVERAGE_TARGET_PCT_SETTING_KEY, cfg.catalog.coverage.deep_analysis_target_pct))
    return _coverage_out(cfg, pct, "override" if row is not None else "yaml_default")


@router.put("/coverage-target", response_model=CoverageTargetOut)
def set_coverage_target(
    body: CoverageTargetIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    _: Principal = Depends(require_admin),
) -> CoverageTargetOut:
    pct = clamp_coverage_pct(body.target_pct)
    set_setting(db, COVERAGE_TARGET_PCT_SETTING_KEY, pct)
    db.commit()
    return _coverage_out(cfg, pct, "override")
