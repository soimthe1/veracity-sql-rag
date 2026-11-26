"""
The demand-driven ("self-correcting") coverage signal — the requirement's
own definition, verbatim: "the system should learn if system
was not able to answer mark it, and too many queries come for the same
thing, the system will decide to sweep it if its not available."

answer/routers/ask.py calls record_unanswered() only when a question
ends up UNANSWERED (no rows from any database tried), and only for the
few best-matching not-yet-TRUSTED tables each grounding relied on (up to
5 per database — GroundingResult.demand_candidate_ids), regardless of
the caller's access grants: whether an object needs deeper analysis is
a data-completeness question, orthogonal to any one caller's clearance.
(It used to be called with every search hit of every question, answered
or not — up to ~48 tables per question — which flagged nearly the whole
catalog; migration 0006 reset those inflated counts.)
engine/coverage.py reads unanswered_hit_count back on its next cycle to
rank/force those entities into deep analysis — see CoverageConfig's
demand_* knobs and coverage.py's own docstring.

Best-effort, like CatalogEvidence/CatalogObservation (never
CatalogAskAudit-strict) — a failure here must never affect the /v1/ask
response itself.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from shared.models import CatalogEntity

logger = logging.getLogger("answer.demand")


def record_unanswered(session: Session, entity_ids: Iterable[UUID]) -> None:
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return
    try:
        now = datetime.now(timezone.utc)
        (
            session.query(CatalogEntity)
            .filter(CatalogEntity.id.in_(ids))
            .update(
                {
                    CatalogEntity.unanswered_hit_count: CatalogEntity.unanswered_hit_count + 1,
                    CatalogEntity.last_unanswered_at: now,
                },
                synchronize_session=False,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - a demand-tracking hiccup must never affect the /v1/ask response
        logger.exception("Failed to record demand signal for %d entit(y/ies) (question continued normally)", len(ids))
        session.rollback()
