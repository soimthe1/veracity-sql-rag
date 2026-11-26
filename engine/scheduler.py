"""
Two independent cycles, per spec section 7's SCHEMA REFRESH vs SEMANTIC
REVALIDATION distinction:

- discovery_cycle (catalog.discovery.interval_minutes): structural sweep
  only — tables/columns/views/procs/functions/declared FKs, fingerprint-
  gated so an unchanged object costs almost nothing. Join-predicate
  mining also happens here (engine/discovery.py calls into
  engine/relationships.py while a changed view/proc's definition text
  is in hand).
- validation_cycle (catalog.validation.interval_hours): coverage
  selection (the percentage knob), naming-similarity relationship
  inference, bounded profiling + value-overlap testing, LLM semantic
  inference, and confidence rescoring — everything that either costs
  more (LLM, live-data sampling) or depends on structural discovery
  having already run at least once.

Both cycles are fully fault-isolated per source and per database (spec
section 13) — see engine/discovery.py's own per-database commit/error
handling, which this scheduler relies on rather than re-implementing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func
from sqlalchemy.orm import Session

from engine import access_classification as access_classification_mod
from engine import confidence as confidence_mod
from engine import coverage as coverage_mod
from engine import discovery as discovery_mod
from engine import health as health_mod
from engine import profiling as profiling_mod
from engine import relationships as relationships_mod
from engine import semantic as semantic_mod
from engine.connectors import build_connectors
from shared.config import AppConfig
from shared.db import get_session_factory
from shared.llm.factory import build_llm_provider
from shared.models import CatalogDatabase, CatalogEntity, CatalogField, CatalogRelationship

logger = logging.getLogger("engine.scheduler")

# Per-validation-cycle caps so one slow source/database can't monopolize
# an entire cycle — the next cycle simply picks up where evidence is
# still missing (nothing here is ever "lost", just deferred).
MAX_SEMANTIC_CALLS_PER_DATABASE_PER_CYCLE = 25
MAX_PROFILING_COLUMNS_PER_DATABASE_PER_CYCLE = 100
MAX_OVERLAP_TESTS_PER_DATABASE_PER_CYCLE = 100

# How long after startup the FIRST validation cycle fires, instead of
# waiting a full catalog.validation.interval_hours (6h by default) —
# without this, a fresh dev.ps1 start would show confidence=0 and
# deep_analysis empty everywhere until either that full interval
# elapsed or someone ran scripts/trigger_validation.py by hand, which
# is surprising for something that should just work out of the box.
# Offset (not simultaneous with discovery_cycle's own next_run_time=now())
# so the cheap structural sweep gets a head start — validation depends on
# discovery having run at least once (see this module's docstring) and
# handles a still-incomplete catalog gracefully either way, so this is a
# best-effort ordering, not a hard dependency.
VALIDATION_INITIAL_DELAY_MINUTES = 1


def discovery_cycle(cfg: AppConfig) -> None:
    if not cfg.catalog.enabled:
        return
    sources = cfg.catalog.enabled_sources
    if not sources:
        logger.info("Catalog enabled but no configured sources — idling.")
        return

    Session = get_session_factory(cfg)
    connectors = build_connectors(
        sources, cfg.catalog.profiling.statement_timeout_seconds,
        cfg.catalog.profiling.max_concurrent_queries_per_source,
    )
    for src_cfg in sources:
        connector = connectors.get(src_cfg.name)
        if connector is None:
            continue
        with Session() as session:
            try:
                result = discovery_mod.discover_source(
                    session, src_cfg, connector, cfg.catalog.discovery.fingerprint_enabled
                )
                logger.info("Discovery complete for %s: %s", src_cfg.name, result)
                # Provisional access classification —
                # cheap, pure recomputation from sensitivity_flags, so a
                # freshly discovered object gets a (level, category)
                # immediately rather than sitting NULL until the next
                # validation cycle hours later.
                from shared.models import CatalogSource
                src_row = session.query(CatalogSource).filter_by(name=src_cfg.name).one_or_none()
                if src_row is not None:
                    for db_row in session.query(CatalogDatabase).filter_by(source_id=src_row.id).all():
                        access_classification_mod.apply_for_database(session, db_row.id, cfg.catalog.classification, cfg)
                session.commit()
                # discover_source() catches per-source/per-database failures
                # internally and reports them in the result dict rather than
                # raising — check that explicitly rather than assuming a
                # non-throwing call means success.
                databases = result.get("databases", [])
                if result.get("error") or (databases and all(d.get("error") for d in databases)):
                    status = "UNAVAILABLE"
                elif any(d.get("error") for d in databases):
                    status = "DEGRADED"
                else:
                    status = "HEALTHY"
                health_mod.set_component_health(session, f"source:{src_cfg.name}", status, result)
                session.commit()
            except Exception as exc:  # noqa: BLE001 - one source's crash must never stop the others
                logger.exception("Unhandled error during discovery for source %s", src_cfg.name)
                session.rollback()
                with Session() as err_session:
                    health_mod.set_component_health(err_session, f"source:{src_cfg.name}", "DEGRADED",
                                                      {"error": str(exc)})
                    err_session.commit()

    # Merge free-text LLM domains onto the canonical list (config.yaml
    # catalog.domains) — cheap and idempotent, so it runs every discovery
    # cycle (including the one at engine startup) and an edit to the list
    # takes effect within the hour.
    if cfg.catalog.domains.enabled:
        with Session() as session:
            try:
                semantic_mod.normalize_domains(session, cfg.catalog.domains.canonical)
                session.commit()
            except Exception:  # noqa: BLE001 - never let this stop discovery
                logger.exception("Domain normalization failed (discovery continued normally)")
                session.rollback()


def _validate_database(session: Session, database: CatalogDatabase, connector, cfg: AppConfig,
                        llm_provider) -> None:
    coverage_result = coverage_mod.compute_deep_analysis_selection(session, database.id, cfg.catalog.coverage)
    session.commit()
    logger.info("Coverage selection for %s: %s", database.database_name, coverage_result)

    relationships_mod.retire_implausible_naming_relationships(session, database.id)
    naming_candidates = relationships_mod.find_naming_candidates(session, database.id)
    relationships_mod.record_naming_candidates(session, naming_candidates)
    session.commit()

    relationships_mod.refine_entity_relevance(session, database.id)
    session.commit()

    if connector is not None and cfg.catalog.profiling.enabled:
        _run_profiling(session, database, connector, cfg)

    if llm_provider is not None:
        applied = semantic_mod.run_semantic_pass(
            session, llm_provider, database.id, cfg.catalog.inference,
            limit=MAX_SEMANTIC_CALLS_PER_DATABASE_PER_CYCLE,
            domain_rules=cfg.catalog.domains.canonical if cfg.catalog.domains.enabled else None,
        )
        session.commit()
        logger.info("Semantic inference applied to %s entities in %s", applied, database.database_name)

    # Recompute the provisional access classification now that this
    # cycle's semantic pass may have grown sensitivity_flags — see
    # engine/access_classification.py.
    access_result = access_classification_mod.apply_for_database(session, database.id, cfg.catalog.classification, cfg)
    session.commit()
    if access_result["entities_changed"] or access_result["fields_changed"]:
        logger.info("Access classification updated for %s: %s", database.database_name, access_result)

    # Structural entity evidence (primary key / typed columns / TRUSTED
    # relationship participation) — see
    # confidence_mod.record_entity_structural_evidence's docstring for
    # why this exists: llm_semantic_interpretation was the ONLY evidence
    # type ever written for an entity, capped at weight 0.08, so no
    # table or view could ever cross trusted_threshold (0.90) or even
    # suspect_threshold (0.55) no matter how many cycles ran. Scoped to
    # deep_analysis=True, same as the semantic pass, so the coverage
    # knob still controls which entities are evaluated at all.
    deep_entities = (
        session.query(CatalogEntity)
        .filter_by(database_id=database.id, deep_analysis=True)
        .filter(CatalogEntity.object_type.in_(("TABLE", "VIEW")))
        .all()
    )
    for entity in deep_entities:
        entity_fields = session.query(CatalogField).filter_by(entity_id=entity.id).all()
        entity_rels = session.query(CatalogRelationship).filter(
            (CatalogRelationship.from_entity_id == entity.id) | (CatalogRelationship.to_entity_id == entity.id)
        ).all()
        confidence_mod.record_entity_structural_evidence(session, entity, entity_fields, entity_rels)
    session.commit()
    logger.info("Structural entity evidence recorded for %s entities in %s", len(deep_entities), database.database_name)

    results = confidence_mod.rescore_all(session, cfg.catalog.confidence, database_id=database.id)
    session.commit()
    changed = sum(1 for r in results if r.changed)
    logger.info("Confidence rescored for %s: %s subjects, %s changed", database.database_name, len(results), changed)


def _run_profiling(session: Session, database: CatalogDatabase, connector, cfg: AppConfig) -> None:
    from shared.models import CatalogEntity

    # Never-attempted columns first, then the ones whose last attempt is
    # oldest. Without this ordering, a column that can never produce a
    # profile (empty table, a permission/type error) kept null_ratio NULL,
    # got re-picked every cycle, and could occupy every one of the
    # per-cycle slots forever — starving every other column. Binary/xml/
    # spatial columns are excluded outright (profiling_mod.is_profilable).
    last_attempt = CatalogField.properties["last_profile_attempt"].astext
    fields = (
        session.query(CatalogField)
        .join(CatalogEntity, CatalogField.entity_id == CatalogEntity.id)
        .filter(CatalogEntity.database_id == database.id, CatalogEntity.deep_analysis.is_(True))
        .filter(CatalogField.null_ratio.is_(None))
        .filter(~func.lower(CatalogField.data_type).in_(profiling_mod.NON_PROFILABLE_TYPES))
        .order_by(last_attempt.asc().nullsfirst())
        .limit(MAX_PROFILING_COLUMNS_PER_DATABASE_PER_CYCLE)
        .all()
    )
    for field in fields:
        entity = session.get(CatalogEntity, field.entity_id)
        profile = profiling_mod.profile_column(
            connector, database.database_name, entity.schema_name, entity.object_name,
            field.column_name, cfg.catalog.profiling, data_type=field.data_type,
        )
        if profile.ok and profile.sampled_rows:
            field.null_ratio = profile.null_ratio
            field.distinct_estimate = profile.distinct_estimate
            field.sample_min = profile.sample_min
            field.sample_max = profile.sample_max
            field.top_values = profile.top_values
        else:
            # Back of the queue; retried once everything else has had a turn.
            props = dict(field.properties or {})
            props["last_profile_attempt"] = datetime.now(timezone.utc).isoformat()
            props["last_profile_result"] = profile.error or "no rows sampled"
            field.properties = props
    session.commit()

    rels = (
        session.query(CatalogRelationship)
        .join(CatalogEntity, CatalogRelationship.from_entity_id == CatalogEntity.id)
        .filter(CatalogEntity.database_id == database.id, CatalogRelationship.is_declared_fk.is_(False))
        .filter(CatalogRelationship.from_field_id.isnot(None), CatalogRelationship.to_field_id.isnot(None))
        .limit(MAX_OVERLAP_TESTS_PER_DATABASE_PER_CYCLE)
        .all()
    )
    for rel in rels:
        from_entity = session.get(CatalogEntity, rel.from_entity_id)
        to_entity = session.get(CatalogEntity, rel.to_entity_id)
        from_field = session.get(CatalogField, rel.from_field_id)
        to_field = session.get(CatalogField, rel.to_field_id)
        if not (profiling_mod.is_profilable(from_field.data_type) and profiling_mod.is_profilable(to_field.data_type)):
            continue
        overlap = profiling_mod.test_value_overlap(
            connector, database.database_name,
            from_entity.schema_name, from_entity.object_name, from_field.column_name,
            to_entity.schema_name, to_entity.object_name, to_field.column_name,
            cfg.catalog.profiling,
        )
        if overlap.ok and overlap.sampled:
            existing = session.query(relationships_mod.CatalogEvidence).filter_by(
                subject_type="relationship", subject_id=rel.id, evidence_type="value_overlap"
            ).one_or_none()
            detail = {"overlap_ratio": overlap.overlap_ratio, "sampled": overlap.sampled, "matched": overlap.matched}
            if existing is None:
                session.add(relationships_mod.CatalogEvidence(
                    subject_type="relationship", subject_id=rel.id,
                    evidence_type="value_overlap", detail=detail,
                ))
            else:
                existing.detail = detail
    session.commit()


def _log_trust_summary(session: Session) -> None:
    """Self-monitoring summary written to THIS process's own rotating log
    (logs/engine.log by default — see shared/logging_setup.py) at the end
    of every validation cycle.

    This is the same catalog-wide picture scripts/diagnose_trust.py prints
    (entity/relationship status distribution + evidence-type counts), but
    on autopilot: nobody — person or AI assistant — has to remember to run
    a script and paste its output back just to see whether trust is
    actually moving. Read/tail the log file directly instead. Kept cheap
    (three COUNT-shaped queries) since it runs every validation_cycle."""
    from collections import Counter
    from shared.models import CatalogEntity, CatalogEvidence, CatalogRelationship

    status_counts = Counter(row[0] for row in session.query(CatalogEntity.status).all())
    rel_status_counts = Counter(row[0] for row in session.query(CatalogRelationship.status).all())
    evidence_counts = Counter(row[0] for row in session.query(CatalogEvidence.evidence_type).all())
    logger.info(
        "Trust summary — entities: %s | relationships: %s | evidence: %s",
        dict(status_counts), dict(rel_status_counts), dict(evidence_counts),
    )
    for required in ("structural_completeness", "relationship_participation"):
        if evidence_counts.get(required, 0) == 0:
            logger.warning(
                "Trust summary — '%s' evidence still has zero rows after this cycle; "
                "entities relying on it cannot move toward TRUSTED yet.", required,
            )


def validation_cycle(cfg: AppConfig) -> None:
    if not cfg.catalog.enabled:
        return
    sources = cfg.catalog.enabled_sources
    if not sources:
        return

    Session = get_session_factory(cfg)
    connectors = build_connectors(
        sources, cfg.catalog.profiling.statement_timeout_seconds,
        cfg.catalog.profiling.max_concurrent_queries_per_source,
    )
    llm_provider = build_llm_provider(cfg.catalog.inference)

    with Session() as session:
        health_mod.set_component_health(session, "llm", "HEALTHY" if llm_provider else "DISABLED", {})
        session.commit()

        for src_cfg in sources:
            from shared.models import CatalogSource
            source = session.query(CatalogSource).filter_by(name=src_cfg.name).one_or_none()
            if source is None:
                continue
            connector = connectors.get(src_cfg.name)
            for database in session.query(CatalogDatabase).filter_by(source_id=source.id).all():
                # Captured before the try, not read from `database` in the
                # except block below: a failed flush/commit inside
                # _validate_database leaves this Session needing a
                # rollback before ANY attribute access (even a plain
                # scalar column already loaded in memory can be marked
                # expired and trigger a re-fetch) — reading
                # database.database_name after the failure, before
                # rollback, raised its own PendingRollbackError and
                # masked the real one, turning a single database's
                # failure into a crash of the whole validation cycle.
                database_name = database.database_name
                try:
                    _validate_database(session, database, connector, cfg, llm_provider)
                except Exception:  # noqa: BLE001 - one database's failure must not stop the others
                    session.rollback()
                    logger.exception("Validation failed for %s/%s", src_cfg.name, database_name)
            source.last_validation_at = datetime.now(timezone.utc)
            session.commit()

        # Catalog-wide self-monitoring line — see _log_trust_summary's
        # docstring. Runs once per validation_cycle, after every
        # source/database has been processed (or skipped on its own
        # error), so it always reflects this cycle's actual end state.
        _log_trust_summary(session)


def build_scheduler(cfg: AppConfig) -> Optional[BackgroundScheduler]:
    if not cfg.catalog.enabled:
        logger.info("Catalog module disabled — scheduler not started.")
        return None

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        discovery_cycle, "interval", minutes=cfg.catalog.discovery.interval_minutes,
        args=[cfg], id="catalog_discovery", next_run_time=datetime.now(),
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        validation_cycle, "interval", hours=cfg.catalog.validation.interval_hours,
        args=[cfg], id="catalog_validation",
        next_run_time=datetime.now() + timedelta(minutes=VALIDATION_INITIAL_DELAY_MINUTES),
        max_instances=1, coalesce=True,
    )
    return scheduler
