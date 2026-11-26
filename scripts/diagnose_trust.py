"""
Read-only diagnostic: prints exactly why Ask keeps saying "found N
related entities, none TRUSTED" — status/evidence counts, per-source
validation timestamps, and a full evidence breakdown for a sample of
entities, so the actual stuck stage is visible instead of guessed at.

Makes no writes. Safe to run any time, against the same catalog Postgres
your engine/api/answer processes use (reads the same config/.env they do).

Usage:
    python -m scripts.diagnose_trust
    python -m scripts.diagnose_trust --match employee     # filter the sample by name
    python -m scripts.diagnose_trust --limit 10            # sample size (default 8)
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone

from shared.config import load_config
from shared.db import get_session_factory, init_db
from shared.models import (
    CatalogEntity, CatalogEvidence, CatalogRelationship, CatalogSource,
)


def _age(dt) -> str:
    if dt is None:
        return "never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", default=None, help="only show per-entity detail for object names containing this")
    parser.add_argument("--limit", type=int, default=8, help="how many entities to show full evidence for")
    args = parser.parse_args()

    cfg = load_config()
    if not init_db(cfg):
        print("Catalog Postgres is not reachable — check CATALOG_DB_* in .env.")
        return

    Session = get_session_factory(cfg)
    with Session() as session:
        print("=" * 72)
        print("1. Are validation cycles actually running?")
        print("=" * 72)
        sources = session.query(CatalogSource).all()
        if not sources:
            print("  No sources configured at all — discovery has never run.")
        for s in sources:
            print(f"  {s.name}: status={s.status}  last_discovery={_age(s.last_discovery_at)}  "
                  f"last_validation={_age(s.last_validation_at)}  consecutive_failures={s.consecutive_failures}")
            if s.last_error:
                print(f"      last_error: {s.last_error}")

        print()
        print("=" * 72)
        print("2. Entity trust distribution (catalog-wide)")
        print("=" * 72)
        entities = session.query(CatalogEntity).all()
        status_counts = Counter(e.status for e in entities)
        deep_counts = Counter(e.deep_analysis for e in entities)
        print(f"  Total entities: {len(entities)}")
        for status in ("TRUSTED", "LEARNING", "SUSPECT", "UNKNOWN", "DISCOVERED", "STALE"):
            if status_counts.get(status):
                print(f"    {status}: {status_counts[status]}")
        print(f"  deep_analysis=True: {deep_counts.get(True, 0)}   deep_analysis=False: {deep_counts.get(False, 0)}")
        if len(entities) and deep_counts.get(True, 0) == 0:
            print("  *** No entity has ever been deep-analyzed. Relationship inference (naming/value-overlap) ")
            print("      never runs for ANY entity until deep_analysis=True — this alone would explain zero TRUSTED.")

        print()
        print("=" * 72)
        print("3. Relationship trust distribution + declared vs inferred")
        print("=" * 72)
        rels = session.query(CatalogRelationship).all()
        rel_status_counts = Counter(r.status for r in rels)
        declared = sum(1 for r in rels if r.is_declared_fk)
        inferred = len(rels) - declared
        print(f"  Total relationships: {len(rels)}  (declared_fk={declared}, inferred={inferred})")
        for status in ("TRUSTED", "LEARNING", "SUSPECT", "UNKNOWN", "DISCOVERED", "STALE"):
            if rel_status_counts.get(status):
                print(f"    {status}: {rel_status_counts[status]}")
        if len(rels) == 0:
            print("  *** Zero relationships exist at all. Entities can only reach TRUSTED via 1+ TRUSTED")
            print("      relationship OR (rarely) structural_completeness alone — with none, every entity is")
            print("      capped well below trusted_threshold regardless of the confidence-scoring code.")
        elif declared == 0:
            print("  *** No declared (real DB foreign-key) relationships were found — every relationship here")
            print("      depends on inferred evidence (naming/value-overlap/join-mining) reaching 0.90 on its")
            print("      own, which is a much higher bar than an explicit_fk relationship clears instantly.")

        print()
        print("=" * 72)
        print("4. Evidence rows that actually exist, by type (catalog-wide)")
        print("=" * 72)
        evidence = session.query(CatalogEvidence).all()
        ev_counts = Counter(e.evidence_type for e in evidence)
        if not evidence:
            print("  *** Zero evidence rows exist anywhere. Nothing has ever been scored — validation has")
            print("      likely never completed a full pass yet.")
        for et, count in sorted(ev_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {et}: {count}")
        for required in ("structural_completeness", "relationship_participation"):
            if required not in ev_counts:
                print(f"  *** '{required}' evidence has NEVER been written — this is what a fixed confidence.py")
                print(f"      relies on; if it's missing, the engine process serving right now is still running")
                print(f"      OLD code (see the dev.ps1 stale-process issue), not the delivered fix.")

        print()
        print("=" * 72)
        print(f"5. Per-entity detail" + (f" (matching '{args.match}')" if args.match else " (sample)"))
        print("=" * 72)
        pool = [e for e in entities if (args.match.lower() in e.object_name.lower()) if args.match] \
            if args.match else sorted(entities, key=lambda e: -e.evidence_count)
        for e in pool[: args.limit]:
            print(f"\n  {e.schema_name}.{e.object_name}  [{e.object_type}]  status={e.status}  "
                  f"confidence={e.confidence:.3f}  deep_analysis={e.deep_analysis}  classification={e.classification}")
            rows = session.query(CatalogEvidence).filter_by(subject_type="entity", subject_id=e.id).all()
            if not rows:
                print("      no evidence rows for this entity")
            for r in rows:
                print(f"      {r.evidence_type}: weight_applied={r.weight_applied:.3f}  detail={r.detail}")
            touching = session.query(CatalogRelationship).filter(
                (CatalogRelationship.from_entity_id == e.id) | (CatalogRelationship.to_entity_id == e.id)
            ).all()
            trusted_touching = sum(1 for r in touching if r.status == "TRUSTED")
            print(f"      relationships touching this entity: {len(touching)} total, {trusted_touching} TRUSTED")

        print()
        print("=" * 72)
        print("Done. Paste this whole output back — it pinpoints exactly which stage is stuck.")
        print("=" * 72)


if __name__ == "__main__":
    main()
