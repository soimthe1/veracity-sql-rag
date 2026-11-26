"""
LLM-assisted semantic interpretation (spec sections 8/14). The LLM
PROPOSES a name/description/domain/sensitivity guess for a
deep_analysis=True entity — it never sets confidence itself (that's
engine/confidence.py, from the small llm_semantic_interpretation
evidence weight alongside everything else). Every change this module
makes to an entity's semantic fields is recorded in CatalogHistory
first, so a later, better interpretation never silently erases the
trail (spec section 8: "the old interpretation should remain
historically traceable").

Fully optional: if `provider` is None (inference disabled/misconfigured/
unreachable — see shared/llm/factory.py), every function here is a
no-op that returns cleanly. Structural discovery and relationship
inference never depend on this module succeeding.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from shared.config import InferenceConfig
from shared.llm.base import LlmProvider
from shared.models import CatalogEntity, CatalogEvidence, CatalogField, CatalogHistory, CatalogRelationship

from shared.domains import canonical_domain, canonical_names

logger = logging.getLogger("engine.semantic")

_ALLOWED_SENSITIVITY = {"PII", "HEALTH", "FINANCIAL", "HR", "SECURITY", "CREDENTIALS", "DISCIPLINARY"}

_SYSTEM_PROMPT = """You are a careful data cataloging assistant. You are given the \
STRUCTURE of one database object (its columns, types, and any already-known \
relationships) - never its actual row data. Propose what the object most \
likely represents in plain business language.

Rules:
- Base your answer only on the structural evidence given. If the evidence is \
too thin or ambiguous, say so honestly via a low confidence_hint rather than \
guessing confidently.
- Never invent a meaning that isn't supported by the column/relationship shape.
- Flag possible sensitivity (PII, HEALTH, FINANCIAL, HR, SECURITY, \
CREDENTIALS, DISCIPLINARY) conservatively - when uncertain, include the flag \
rather than omit it.
- Respond with ONLY a single JSON object, no other text, matching exactly:
{"semantic_name": string, "description": string, "domain": string, \
"synonyms": [string], "sensitivity_flags": [string], "confidence_hint": number}
confidence_hint is YOUR OWN rough self-assessment from 0.0 to 1.0 - it is \
used only as a small nudge alongside deterministic evidence, never trusted \
alone, so be honest rather than optimistic."""


def _build_prompt(entity: CatalogEntity, fields: List[CatalogField],
                   relationships: List[CatalogRelationship]) -> str:
    lines = [
        f"Object: {entity.schema_name}.{entity.object_name} ({entity.object_type})",
        f"Structural classification (computed, not from the LLM): {entity.classification}",
        "Columns:",
    ]
    for f in fields[:60]:  # bounded - a very wide table doesn't need every column to get the gist
        bits = [f"  - {f.column_name} ({f.data_type})"]
        if f.is_primary_key:
            bits.append("[primary key]")
        if f.null_ratio is not None:
            bits.append(f"[null_ratio={f.null_ratio}]")
        if f.top_values:
            sample = ", ".join(f.top_values[:5])
            bits.append(f"[example values: {sample}]")
        lines.append(" ".join(bits))

    if relationships:
        lines.append("Known relationships:")
        for r in relationships[:20]:
            lines.append(f"  - {r.relationship_label} (confidence={r.confidence}, status={r.status})")

    return "\n".join(lines)


def _parse_response(text: str) -> Optional[dict]:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        logger.warning("LLM semantic response was not valid JSON, discarding: %s", text[:200])
        return None

    if not isinstance(data.get("semantic_name"), str) or not data["semantic_name"].strip():
        return None

    sensitivity = data.get("sensitivity_flags") or []
    if not isinstance(sensitivity, list):
        sensitivity = []
    sensitivity = [s for s in sensitivity if s in _ALLOWED_SENSITIVITY]

    synonyms = data.get("synonyms") or []
    if not isinstance(synonyms, list):
        synonyms = []

    try:
        confidence_hint = max(0.0, min(1.0, float(data.get("confidence_hint", 0.5))))
    except (TypeError, ValueError):
        confidence_hint = 0.5

    return {
        "semantic_name": data["semantic_name"].strip()[:256],
        "description": str(data.get("description", "")).strip()[:2000],
        "domain": str(data.get("domain", "")).strip()[:128] or None,
        "synonyms": [str(s)[:64] for s in synonyms][:10],
        "sensitivity_flags": sensitivity,
        "confidence_hint": confidence_hint,
    }


def _record_change(session: Session, entity: CatalogEntity, field: str, old, new, reason: str) -> None:
    if old == new:
        return
    session.add(CatalogHistory(
        subject_type="entity", subject_id=entity.id, field_changed=field,
        old_value=str(old) if old is not None else None,
        new_value=str(new) if new is not None else None,
        reason=reason,
    ))


def infer_entity_semantics(session: Session, provider: Optional[LlmProvider], entity: CatalogEntity,
                            cfg: InferenceConfig, domain_rules: Optional[list] = None) -> bool:
    """Returns True if a proposal was applied. False (never raises) if
    the LLM is unavailable, times out, or returns something unusable —
    the entity simply stays at whatever semantic state it already had,
    picked up again on the next validation cycle."""
    if provider is None:
        return False
    domain_names = canonical_names(domain_rules or [])

    fields = session.query(CatalogField).filter_by(entity_id=entity.id).order_by(CatalogField.ordinal_position).all()
    relationships = session.query(CatalogRelationship).filter(
        (CatalogRelationship.from_entity_id == entity.id) | (CatalogRelationship.to_entity_id == entity.id)
    ).all()

    prompt = _build_prompt(entity, fields, relationships)
    if domain_names:
        prompt += (
            "\n\nFor \"domain\", use exactly one of these catalog domains if any fits: "
            + ", ".join(domain_names)
            + ". Only propose a new domain name if none of them fits at all."
        )
    result = provider.complete(prompt, system=_SYSTEM_PROMPT, json_mode=True)
    if not result.ok:
        logger.info("Semantic inference skipped for %s.%s: %s", entity.schema_name, entity.object_name, result.error)
        return False

    parsed = _parse_response(result.text)
    if parsed is None:
        return False

    if domain_rules and parsed["domain"]:
        raw_domain = parsed["domain"]
        parsed["domain"] = canonical_domain(raw_domain, domain_rules)
        entity.properties = {**(entity.properties or {}), "domain_raw": raw_domain}
    _record_change(session, entity, "semantic_name", entity.semantic_name, parsed["semantic_name"], "llm_proposal")
    _record_change(session, entity, "domain", entity.domain, parsed["domain"], "llm_proposal")

    entity.semantic_name = parsed["semantic_name"]
    entity.description = parsed["description"] or entity.description
    entity.domain = parsed["domain"] or entity.domain
    entity.synonyms = parsed["synonyms"] or entity.synonyms
    # Sensitivity flags only ever grow more conservative (union), never
    # shrink from an LLM pass alone (spec section 15: "if uncertain,
    # prefer more restrictive classification").
    existing_flags = set(entity.sensitivity_flags or [])
    entity.sensitivity_flags = sorted(existing_flags | set(parsed["sensitivity_flags"]))

    existing_ev = session.query(CatalogEvidence).filter_by(
        subject_type="entity", subject_id=entity.id, evidence_type="llm_semantic_interpretation"
    ).one_or_none()
    detail = {"llm_confidence_hint": parsed["confidence_hint"], "proposed_name": parsed["semantic_name"]}
    if existing_ev is None:
        session.add(CatalogEvidence(
            subject_type="entity", subject_id=entity.id,
            evidence_type="llm_semantic_interpretation", detail=detail,
        ))
    else:
        existing_ev.detail = detail
        existing_ev.observed_at = datetime.now(timezone.utc)

    return True


def run_semantic_pass(session: Session, provider: Optional[LlmProvider], database_id, cfg: InferenceConfig,
                       limit: Optional[int] = None, domain_rules: Optional[list] = None) -> int:
    """Runs semantic inference over deep_analysis=True entities that
    don't have a semantic_name yet, or whose status was demoted back to
    LEARNING by a structural change (see engine/discovery.py's
    TRUSTED->LEARNING demotion on fingerprint change) — a schema change
    invalidates the old interpretation's context, so it's worth
    reconsidering, not just re-trusting the same old text.

    `limit` bounds this to a fixed number of LLM calls per cycle (cost
    control — see engine/scheduler.py's MAX_SEMANTIC_CALLS_PER_DATABASE_
    PER_CYCLE), so when the backlog of not-yet-analyzed entities exceeds
    it, WHICH ones get picked this cycle matters. Ordered by
    unanswered_hit_count first so this is actually where
    engine/coverage.py's demand loop closes: coverage.py deciding an
    entity deserves deep_analysis=True is meaningless to the person who
    asked about it if the entity then sits behind an arbitrary DB-order
    backlog for cycle after cycle before its LLM pass actually runs —
    relevance_score is the fallback for everything with no demand yet."""
    if provider is None:
        return 0

    q = (
        session.query(CatalogEntity)
        .filter_by(database_id=database_id, deep_analysis=True)
        .filter(CatalogEntity.object_type.in_(("TABLE", "VIEW")))
        .filter((CatalogEntity.semantic_name.is_(None)) | (CatalogEntity.status == "LEARNING"))
        .order_by(CatalogEntity.unanswered_hit_count.desc(), CatalogEntity.relevance_score.desc())
    )
    if limit:
        q = q.limit(limit)

    applied = 0
    for entity in q.all():
        if infer_entity_semantics(session, provider, entity, cfg, domain_rules=domain_rules):
            applied += 1
    return applied


def normalize_domains(session: Session, domain_rules: list) -> int:
    """Maps every entity's domain onto the canonical list (shared/
    domains.py). Idempotent and cheap (no LLM, no source I/O); keeps the
    first-seen raw wording in properties["domain_raw"]. Returns how many
    entities changed."""
    changed = 0
    for entity in session.query(CatalogEntity).filter(CatalogEntity.domain.isnot(None)).all():
        props = entity.properties or {}
        raw = props.get("domain_raw") or entity.domain
        target = canonical_domain(raw, domain_rules)
        if target != entity.domain:
            _record_change(session, entity, "domain", entity.domain, target, "domain_normalized")
            entity.domain = target
            changed += 1
        if "domain_raw" not in props:
            entity.properties = {**props, "domain_raw": raw}
    if changed:
        logger.info("Normalized %d entity domain(s) onto the canonical domain list", changed)
    return changed
