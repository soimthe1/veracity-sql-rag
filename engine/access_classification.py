"""
Provisional (level, category) access-classification shim.

NOT a permission system. The requirement, in its own words: "right now
infer it from sensitivity flag, later on we can put a proper permission
system in place... but yes level, category is the ultimate end goal so
keep the room open for this." This module is exactly that room: it
derives a best-guess (access_level, access_category) for every entity
and field from the sensitivity_flags engine/semantic.py already
computes, and writes them via the same schema fields a future
governance-driven system will use — so that system's arrival is a
config/data change, never a migration.

Deliberately mirrors engine/confidence.py's own "cheap, pure
recomputation over already-collected data, safe to run every cycle"
shape — no source I/O, just derives new columns from sensitivity_flags
already sitting on the row.

Rule: the HIGHEST configured level among an object's sensitivity_flags
wins, and its paired category is used — "if uncertain, prefer more
restrictive classification" (the same rule engine/semantic.py already
applies when a flag itself is uncertain). An object with no flags gets
the config's default_level/default_category, never NULL — a NULL here
would either break every downstream (level, category) join or, worse,
silently be treated as "no restriction" by some future consumer.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from shared.config import ClassificationConfig
from shared.models import CatalogEntity, CatalogField, CatalogHistory

logger = logging.getLogger("engine.access_classification")


def infer_access_classification(
    sensitivity_flags: Optional[List[str]], cfg: ClassificationConfig
) -> Tuple[int, str]:
    """Pure function: sensitivity_flags -> (access_level, access_category).
    Never raises, never returns None — always a usable pair."""
    level = cfg.default_level
    category = cfg.default_category
    for flag in sensitivity_flags or []:
        flag_level = cfg.level_by_sensitivity_flag.get(flag)
        if flag_level is not None and flag_level > level:
            level = flag_level
            category = cfg.category_by_sensitivity_flag.get(flag, category)
    return level, category


def _apply_one(session: Session, subject_type: str, subject, cfg: ClassificationConfig) -> bool:
    new_level, new_category = infer_access_classification(subject.sensitivity_flags, cfg)
    changed = (subject.access_level != new_level) or (subject.access_category != new_category)
    if changed:
        session.add(CatalogHistory(
            subject_type=subject_type, subject_id=subject.id, field_changed="access_classification",
            old_value=f"{subject.access_level}{subject.access_category or ''}" if subject.access_level else None,
            new_value=f"{new_level}{new_category}",
            reason="classification_shim",
        ))
    subject.access_level = new_level
    subject.access_category = new_category
    # Always provisional in this build — there is no governance-driven
    # source of truth yet for this to defer to. A future system flips
    # this to False itself when it takes over a given object; this shim
    # must never claim authority it doesn't have by setting False here.
    subject.access_classification_provisional = True
    return changed


def apply_for_database(session: Session, database_id, cfg: Optional[ClassificationConfig], app_cfg=None) -> dict:
    """Recomputes access_level/access_category for every entity and
    field in one database. Safe and cheap to call every discovery and
    validation cycle — see engine/scheduler.py. A no-op (returns
    immediately) if classification is disabled or cfg is None, so a
    caller that hasn't wired this up yet costs nothing."""
    if cfg is None or not cfg.enabled:
        return {"entities_changed": 0, "fields_changed": 0}
    if app_cfg is not None:
        # The data-access realm (shared/access/labeling.py): manual
        # placements, rules, inheritance, AI proposal, naming conventions,
        # default quarantine — this shim's flag-only logic below is kept
        # only for callers that don't pass the full config.
        from shared.access import labeling
        from shared.access.lattice import load_lattice
        stats = labeling.recompute(session, app_cfg, load_lattice(app_cfg), database_ids=[database_id],
                                   reason="engine cycle")
        return {"entities_changed": stats.entities_changed, "fields_changed": stats.fields_changed}

    entities_changed = 0
    for entity in session.query(CatalogEntity).filter_by(database_id=database_id).all():
        if _apply_one(session, "entity", entity, cfg):
            entities_changed += 1

    fields_changed = 0
    fields = (
        session.query(CatalogField)
        .join(CatalogEntity, CatalogField.entity_id == CatalogEntity.id)
        .filter(CatalogEntity.database_id == database_id)
        .all()
    )
    for field in fields:
        if _apply_one(session, "field", field, cfg):
            fields_changed += 1

    return {"entities_changed": entities_changed, "fields_changed": fields_changed}
