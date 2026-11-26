"""
Evidence-driven confidence engine (spec section 9). The LLM and every
heuristic in engine/relationships.py only ever PROPOSE evidence — this
module is the only place a confidence number or a trust-state
transition is actually decided, and it decides purely from weighted
CatalogEvidence rows, never from a value an LLM reports about itself.

Status transitions (spec sections 8/10/11, reasoning spelled out
because the spec doesn't give an exact formula): `suspect_threshold`
is NOT a second static confidence band under `trusted_threshold` — a
brand-new relationship that has never been trusted stays LEARNING (or
UNKNOWN if evidence is weak) no matter how low its first confidence
is. SUSPECT specifically means "this used to be trusted and evidence
has since turned against it" (spec section 11's own 0.98 -> 0.61
worked example) — so it only ever applies to something that WAS
TRUSTED (or already SUSPECT) and has since dropped below
trusted_threshold. This preserves last-known-good semantics: a
regression is flagged distinctly from an item that simply hasn't
earned trust yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from sqlalchemy.orm import Session

from shared.config import ConfidenceConfig
from shared.models import CatalogEntity, CatalogEvidence, CatalogField, CatalogHistory, CatalogRelationship

Subject = Union[CatalogEntity, CatalogField, CatalogRelationship]

_SUBJECT_TYPE_BY_CLASS = {
    CatalogEntity: "entity",
    CatalogField: "field",
    CatalogRelationship: "relationship",
}


def _utcnow():
    return datetime.now(timezone.utc)


def _evidence_weight(evidence_type: str, detail: dict, weights: Dict[str, float]) -> float:
    """Maps one evidence row's raw detail into a weighted contribution.
    Centralizing this (rather than trusting a weight stored at evidence-
    creation time) means re-running validation after a config weight
    change picks up the new weights immediately, with no evidence
    rows needing to be regenerated."""
    w = weights

    if evidence_type == "explicit_fk":
        return w.get("explicit_fk", 0.0)

    if evidence_type == "pk_fk_datatype_compatible":
        return w.get("pk_fk_datatype_compatible", 0.0) if detail.get("compatible") else 0.0

    if evidence_type == "value_overlap":
        ratio = float(detail.get("overlap_ratio", 0.0))
        high = w.get("value_overlap_high", 0.0)
        low_penalty = w.get("value_overlap_low_penalty", 0.0)
        if ratio >= 0.8:
            return high * min(1.0, ratio)
        if ratio < 0.3:
            return low_penalty
        # linear interpolation between the two bands
        return high * (ratio - 0.3) / 0.5

    if evidence_type == "uniqueness_cardinality":
        return w.get("uniqueness_cardinality_match", 0.0) if detail.get("matches") else 0.0

    if evidence_type == "view_definition_confirms":
        # A single confirming view is already strong, standalone evidence
        # (spec section 4's own example counts it as such) — full weight
        # at count>=1, not requiring multiple confirmations.
        count = int(detail.get("count", 1))
        return w.get("view_definition_confirms", 0.0) * min(1.0, count)

    if evidence_type == "procedure_usage_confirms":
        count = int(detail.get("count", 1))
        return w.get("procedure_usage_confirms", 0.0) * min(1.0, count)

    if evidence_type == "executed_join_evidence":
        # "Repeated" is the operative word here (spec: "repeated successful
        # join -> strong positive") so this one DOES scale up with count,
        # reaching full weight at 3+ observed executions.
        count = int(detail.get("count", 1))
        return w.get("executed_join_evidence", 0.0) * min(1.0, count / 3.0)

    if evidence_type == "naming_similarity":
        similarity = float(detail.get("similarity", 0.0))
        return w.get("naming_similarity", 0.0) * similarity

    if evidence_type == "llm_semantic_interpretation":
        hint = float(detail.get("llm_confidence_hint", 0.5))
        return w.get("llm_semantic_interpretation", 0.0) * max(0.0, min(1.0, hint))

    if evidence_type == "structural_completeness":
        return w.get("structural_completeness", 0.0) * max(0.0, min(1.0, float(detail.get("score", 0.0))))

    if evidence_type == "relationship_participation":
        # Full weight at 1+ TRUSTED relationship touching this entity.
        # This used to require 2+ ("independent corroboration"), but in
        # practice that made TRUSTED nearly unreachable for perfectly
        # well-understood entities that simply don't happen to have a
        # second relationship — e.g. a lookup/reference table with
        # exactly one real foreign key pointing at it (Department, hit
        # only by Employee.DepartmentId) stayed capped at LEARNING
        # forever no matter how many validation cycles ran, which is
        # what was actually driving "Ask never has anything TRUSTED to
        # query" — the demand loop (engine/coverage.py) can force an
        # entity into deep analysis, and it can still gather every other
        # kind of evidence, but this was the one gate no amount of
        # cycles could open. A single relationship that itself reached
        # TRUSTED (explicit_fk, or accumulated inferred evidence — see
        # engine/relationships.py) is still real, independent
        # corroboration; it just doesn't need to be duplicated.
        count = int(detail.get("trusted_relationship_count", 0))
        return w.get("relationship_participation", 0.0) * min(1.0, count)

    if evidence_type == "contradictory_evidence":
        count = int(detail.get("count", 1))
        return w.get("contradictory_evidence_penalty", 0.0) * count

    return 0.0


def compute_confidence(session: Session, subject_type: str, subject_id, weights: Dict[str, float]) -> tuple[float, int, List[CatalogEvidence]]:
    rows = session.query(CatalogEvidence).filter_by(subject_type=subject_type, subject_id=subject_id).all()
    total = 0.0
    for row in rows:
        applied = _evidence_weight(row.evidence_type, row.detail or {}, weights)
        row.weight_applied = applied
        total += applied
    confidence = max(0.0, min(1.0, round(total, 4)))
    return confidence, len(rows), rows


def next_status(prev_status: str, new_confidence: float, trusted_threshold: float, suspect_threshold: float) -> str:
    if new_confidence >= trusted_threshold:
        return "TRUSTED"
    if prev_status in ("TRUSTED", "SUSPECT"):
        return "SUSPECT"
    if new_confidence < suspect_threshold:
        return "UNKNOWN"
    return "LEARNING"


@dataclass
class ScoringResult:
    subject_type: str
    subject_id: object
    old_confidence: float
    new_confidence: float
    old_status: str
    new_status: str
    evidence_count: int
    changed: bool


def rescore_subject(session: Session, subject: Subject, cfg: ConfidenceConfig,
                     reason: str = "revalidation") -> ScoringResult:
    subject_type = _SUBJECT_TYPE_BY_CLASS[type(subject)]
    old_confidence, old_status = subject.confidence, subject.status

    new_confidence, evidence_count, _rows = compute_confidence(session, subject_type, subject.id, cfg.weights)
    new_status = next_status(old_status, new_confidence, cfg.trusted_threshold, cfg.suspect_threshold)

    changed = (new_confidence != old_confidence) or (new_status != old_status)
    if changed:
        session.add(CatalogHistory(
            subject_type=subject_type, subject_id=subject.id, field_changed="confidence",
            old_value=str(old_status), new_value=str(new_status),
            old_confidence=old_confidence, new_confidence=new_confidence, reason=reason,
        ))
    subject.confidence = new_confidence
    subject.status = new_status
    subject.evidence_count = evidence_count
    subject.last_verified_at = _utcnow()

    return ScoringResult(subject_type, subject.id, old_confidence, new_confidence,
                          old_status, new_status, evidence_count, changed)


def _upsert_entity_evidence(session: Session, entity_id, evidence_type: str, detail: dict) -> None:
    existing = session.query(CatalogEvidence).filter_by(
        subject_type="entity", subject_id=entity_id, evidence_type=evidence_type
    ).one_or_none()
    if existing is None:
        session.add(CatalogEvidence(subject_type="entity", subject_id=entity_id,
                                     evidence_type=evidence_type, detail=detail))
    else:
        existing.detail = detail
        existing.observed_at = _utcnow()


def record_entity_structural_evidence(session: Session, entity: CatalogEntity,
                                       fields: List[CatalogField],
                                       relationships: List[CatalogRelationship]) -> None:
    """Writes the two deterministic, non-LLM evidence rows an entity
    needs to ever earn TRUSTED at all. Before this existed,
    llm_semantic_interpretation (weight 0.08) was the ONLY evidence type
    ever written for subject_type="entity" anywhere in this codebase —
    with trusted_threshold at 0.90 (and suspect_threshold at 0.55), that
    meant no table or view could EVER cross even suspect_threshold, no
    matter how many validation cycles ran or how confident the LLM was.
    Ask (answer/grounding.py) only ever queries TRUSTED entities, so
    this was silently blocking every question from the start.

    structural_completeness: does this object look like a properly
    understood table — a primary key, and its columns are genuinely
    typed. Computed purely from already-discovered field data, no new
    source query.

    relationship_participation: how many of this entity's relationships
    have independently reached TRUSTED via the (already-working)
    relationship evidence pipeline — the entity-side equivalent of
    explicit_fk being treated as near-ground-truth on the relationship
    side. An entity with no relationships at all (a genuinely standalone
    reference table) can still reach LEARNING from structural_completeness
    alone, but reaching TRUSTED requires this independent corroboration —
    one TRUSTED relationship is enough (see _evidence_weight's
    "relationship_participation" branch; this used to require two,
    which left ordinary single-reference lookup tables permanently
    stuck below trusted_threshold).
    """
    has_pk = any(f.is_primary_key for f in fields)
    typed_ratio = (sum(1 for f in fields if f.data_type) / len(fields)) if fields else 0.0
    structural_score = (0.6 if has_pk else 0.0) + 0.4 * typed_ratio

    trusted_rel_count = sum(
        1 for r in relationships
        if r.status == "TRUSTED" and (r.from_entity_id == entity.id or r.to_entity_id == entity.id)
    )

    _upsert_entity_evidence(session, entity.id, "structural_completeness", {
        "has_primary_key": has_pk,
        "typed_field_ratio": round(typed_ratio, 4),
        "score": round(structural_score, 4),
    })
    _upsert_entity_evidence(session, entity.id, "relationship_participation", {
        "trusted_relationship_count": trusted_rel_count,
    })


def rescore_all(session: Session, cfg: ConfidenceConfig, database_id: Optional[object] = None) -> List[ScoringResult]:
    """Rescores every entity/field/relationship with at least one evidence
    row. Cheap to run every validation cycle — it's pure arithmetic over
    already-collected evidence, no source I/O."""
    results: List[ScoringResult] = []

    entity_q = session.query(CatalogEntity)
    field_q = session.query(CatalogField).join(CatalogEntity)
    rel_q = session.query(CatalogRelationship).join(
        CatalogEntity, CatalogRelationship.from_entity_id == CatalogEntity.id
    )
    if database_id is not None:
        entity_q = entity_q.filter(CatalogEntity.database_id == database_id)
        field_q = field_q.filter(CatalogEntity.database_id == database_id)
        rel_q = rel_q.filter(CatalogEntity.database_id == database_id)

    for entity in entity_q.all():
        results.append(rescore_subject(session, entity, cfg))
    for field in field_q.all():
        results.append(rescore_subject(session, field, cfg))
    for rel in rel_q.all():
        results.append(rescore_subject(session, rel, cfg))

    return results
