"""
THE data-volume knob, applied. Turns
catalog.coverage.deep_analysis_target_pct into an actual selection of
which entities get Level-2 deep analysis (bounded profiling, relationship
value-overlap testing, LLM semantic inference) versus staying at cheap
Level-1 (name/columns/types/fingerprint only).

The percentage itself is UI-controllable, same live-settings pattern as
answer/grounding.py's trust dial (see shared/settings.py's module
docstring): config.yaml's deep_analysis_target_pct is only the DEFAULT;
the value this function actually uses is read fresh from
catalog_settings on every call, so raising or lowering it from the
Security tab's "Catalog depth" control takes effect on the engine's
very next validation cycle, no restart required.

Design (see DECISIONS.md for the full reasoning):
- always_deep_categories (BUSINESS_ENTITY, RELATIONSHIP_TABLE by default)
  are ALWAYS deep-analyzed, regardless of the percentage — these are
  where the business meaning lives.
- never_deep_categories (SYSTEM_TECHNICAL, TEMPORARY, STAGING by default)
  are NEVER deep-analyzed, regardless of the percentage OR demand below
  — never worth the spend, this is the one hard override.
- Everything else is ranked by relevance_score (plus a demand boost, see
  below) and the top `deep_analysis_target_pct`% is selected, clamped to
  [min_deep_objects_per_source, max_deep_objects_per_source].
- Raising the knob only ADDS to the selection — objects already deep
  keep accumulating evidence uninterrupted. Lowering it only REMOVES
  objects from *future* deep passes; their last-known-good confidence/
  status/history is never touched here (spec section 11).

DEMAND-DRIVEN ("self-correcting") coverage — the requirement's own
words: "too many queries come for the same thing, the system will
decide to sweep it if its not available." answer/grounding.py bumps
CatalogEntity.unanswered_hit_count every time a question's search
matches an entity that isn't TRUSTED yet (answer/demand.py). This
module closes the loop, every cycle:
  1. An entity whose unanswered_hit_count has reached
     coverage_cfg.demand_promote_threshold is forced into deep analysis
     outright — same treatment as always_deep_categories — UNLESS it's
     in never_deep_categories, which still wins (a demand signal never
     overrides a hard "never worth analyzing" classification).
  2. Below that threshold, unanswered_hit_count still nudges the pool
     ranking (demand_rank_boost per hit, capped at
     demand_rank_boost_cap) — frequently-asked-about-but-shallow
     objects rise toward the top of the percentage cutoff sooner, even
     without being force-promoted.
  3. Once an entity reaches TRUSTED, its demand counter is reset to 0 —
     the demand was the signal that it needed more coverage; reaching
     TRUSTED satisfies it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List
from uuid import UUID

from sqlalchemy.orm import Session

from shared.config import CoverageConfig
from shared.models import CatalogEntity
from shared.settings import COVERAGE_TARGET_PCT_SETTING_KEY, clamp_coverage_pct, get_float


@dataclass
class CoverageResult:
    database_id: UUID
    total_entities: int
    always_deep: int
    never_deep: int
    pool_size: int
    selected_from_pool: int
    newly_added_ids: List[UUID]
    newly_removed_ids: List[UUID]
    demand_forced: int = 0


def compute_deep_analysis_selection(session: Session, database_id: UUID,
                                     coverage_cfg: CoverageConfig) -> CoverageResult:
    entities: List[CatalogEntity] = (
        session.query(CatalogEntity).filter_by(database_id=database_id).all()
    )
    always_categories = set(coverage_cfg.always_deep_categories)
    never_categories = set(coverage_cfg.never_deep_categories)

    # Demand loop, step 3: a TRUSTED entity's outstanding demand has been
    # satisfied — clear it so a stale count doesn't keep boosting/forcing
    # an object that's already fully covered.
    for e in entities:
        if e.status == "TRUSTED" and e.unanswered_hit_count:
            e.unanswered_hit_count = 0
            e.last_unanswered_at = None

    # Demand loop, step 1: force-promote anything past the threshold,
    # same treatment as always_deep — never_deep_categories still wins.
    demand_forced_ids = {
        e.id for e in entities
        if e.classification not in never_categories
        and e.unanswered_hit_count >= coverage_cfg.demand_promote_threshold
    }

    always_deep = [
        e for e in entities
        if e.classification in always_categories or e.id in demand_forced_ids
    ]
    never_deep = [e for e in entities if e.classification in never_categories]
    pool = [
        e for e in entities
        if e.classification not in always_categories
        and e.classification not in never_categories
        and e.id not in demand_forced_ids
    ]

    def _pool_key(e: CatalogEntity):
        # Demand loop, step 2: a small, capped ranking boost per
        # unanswered hit — nudges frequently-asked-about objects toward
        # the top of the percentage cutoff without needing to cross the
        # force-promote threshold.
        demand_boost = min(e.unanswered_hit_count * coverage_cfg.demand_rank_boost, coverage_cfg.demand_rank_boost_cap)
        return (e.relevance_score + demand_boost, e.last_seen_at or e.first_seen_at)

    pool_sorted = sorted(pool, key=_pool_key, reverse=True)

    target_pct = clamp_coverage_pct(
        get_float(session, COVERAGE_TARGET_PCT_SETTING_KEY, coverage_cfg.deep_analysis_target_pct)
    )
    target = math.ceil(len(pool_sorted) * (target_pct / 100.0))
    target = max(target, min(coverage_cfg.min_deep_objects_per_source, len(pool_sorted)))
    target = min(target, coverage_cfg.max_deep_objects_per_source, len(pool_sorted))

    selected_from_pool = set(e.id for e in pool_sorted[:target])

    newly_added: List[UUID] = []
    newly_removed: List[UUID] = []

    for e in always_deep:
        if not e.deep_analysis:
            newly_added.append(e.id)
        e.deep_analysis = True

    for e in never_deep:
        if e.deep_analysis:
            newly_removed.append(e.id)
        e.deep_analysis = False

    for e in pool_sorted:
        should_be_deep = e.id in selected_from_pool
        if should_be_deep and not e.deep_analysis:
            newly_added.append(e.id)
        elif not should_be_deep and e.deep_analysis:
            newly_removed.append(e.id)
        e.deep_analysis = should_be_deep

    return CoverageResult(
        database_id=database_id,
        total_entities=len(entities),
        always_deep=len(always_deep),
        never_deep=len(never_deep),
        pool_size=len(pool_sorted),
        selected_from_pool=len(selected_from_pool),
        newly_added_ids=newly_added,
        newly_removed_ids=newly_removed,
        demand_forced=len(demand_forced_ids),
    )
