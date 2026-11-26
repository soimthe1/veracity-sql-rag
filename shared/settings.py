"""Small helper for the handful of knobs the web UI controls live,
backed by catalog_settings (shared/models.py's CatalogSetting) —
deliberately just a few upserted rows, never an events table, same shape
as engine/health.py's set_component_health for catalog_health.

Why a DB table and not just "edit config.yaml": three separate processes
(engine, api, answer) each load config.yaml once at their own startup and
never re-read it — a YAML edit needs every process restarted to take
effect, which is exactly the friction this project's dev.ps1 work has
been removing everywhere else. A row in the catalog's own Postgres is
visible to every process on its very next read, with no restart, so a
control the user is meant to change from the web UI belongs here, not in
a file on disk. config.yaml still defines each knob's DEFAULT (used
until someone actually touches the control, or if this row is ever
missing/deleted) — see get_int()'s docstring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from shared.config import ConfidenceConfig
from shared.models import CatalogSetting

# The setting key both api/ (GET/PUT /v1/settings/trust-level) and
# answer/ (grounding.py's ground_question) read/write — defined once
# here, in shared/, rather than in either process's own package, so
# neither has to import from the other just to agree on a string.
MIN_TRUST_LEVEL_SETTING_KEY = "min_trust_level"

# The second UI-controllable knob, same pattern: how much of the catalog
# gets EXPENSIVE Level-2 deep analysis (bounded profiling, value-overlap
# testing, LLM semantic inference) versus staying at cheap Level-1
# (name/columns/types only) — engine/coverage.py's "how heavy the
# catalog is". config.yaml's catalog.coverage.deep_analysis_target_pct
# is the default; api/ (GET/PUT /v1/settings/coverage-target) and
# engine/ (coverage.py's compute_deep_analysis_selection) read/write
# this key so an operator can raise or lower it from the Security tab
# without editing YAML or restarting the engine — it takes effect on
# the engine's very next validation cycle.
COVERAGE_TARGET_PCT_SETTING_KEY = "coverage_deep_analysis_target_pct"


def min_confidence_for_level(level: int, confidence_cfg: ConfidenceConfig) -> float:
    """The Ask trust dial (0-10, Security tab) turned into an actual
    confidence cutoff: 0 -> 0.0 (use everything), 10 -> trusted_threshold
    (equivalent to the old hard TRUSTED-only gate), linear in between.
    Clamped so a corrupt/out-of-range stored value can't produce a
    nonsensical or negative threshold."""
    level = max(0, min(10, level))
    return (level / 10.0) * confidence_cfg.trusted_threshold


def get_setting(session: Session, key: str, default):
    row = session.query(CatalogSetting).filter_by(key=key).one_or_none()
    if row is None or row.value is None:
        return default
    return row.value


def get_int(session: Session, key: str, default: int) -> int:
    """Same as get_setting(), coerced to int and defensively clamped-
    free — a corrupt/hand-edited row should degrade to the YAML default
    rather than crash a live Ask request."""
    value = get_setting(session, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float(session: Session, key: str, default: float) -> float:
    """Same contract as get_int() but for a float-valued knob (e.g. the
    coverage depth percentage) — a corrupt/hand-edited row degrades to
    the YAML default rather than crashing a live read."""
    value = get_setting(session, key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_coverage_pct(pct: float) -> float:
    """Same clamp shared/config.py's CoverageConfig applies to the YAML
    default — applied again here because a value read back from
    catalog_settings (hand-edited or from an older/looser UI) bypasses
    that pydantic validator entirely."""
    return max(0.0, min(100.0, pct))


def set_setting(session: Session, key: str, value) -> None:
    row = session.query(CatalogSetting).filter_by(key=key).one_or_none()
    if row is None:
        row = CatalogSetting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
