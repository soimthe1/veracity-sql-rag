"""
On-the-fly trusted join-path discovery for POST /v1/resolve (spec:
"Autonomous join-path discovery"). BFS over TRUSTED
catalog_relationships only, bounded to a small candidate set (the
entities a search/resolve call already found) and a small hop count —
cheap enough to compute per-request rather than needing a separate
scheduled precomputation pass.

catalog_join_paths (shared/models.py) exists as a cache slot for a
future optimization if this ever needs to serve a much larger
candidate set or a high request rate — see DECISIONS.md. Not needed
for this build's scale.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from api.schemas import JoinPathOut, JoinPathStepOut
from shared.models import CatalogEntity, CatalogRelationship

MAX_HOPS = 3


def _trusted_adjacency(session: Session, entity_ids: List[UUID]) -> Dict[UUID, List[tuple]]:
    """Builds an undirected adjacency list restricted to TRUSTED
    relationships touching the given entities, expanded one extra hop
    so a path THROUGH an intermediate (non-candidate) entity is still
    findable — e.g. Employee -> Assignment -> Project even if only
    Employee and Project were in the original candidate set."""
    rels = session.query(CatalogRelationship).filter(
        CatalogRelationship.status == "TRUSTED",
        (CatalogRelationship.from_entity_id.in_(entity_ids)) | (CatalogRelationship.to_entity_id.in_(entity_ids)),
    ).all()
    frontier = {r.from_entity_id for r in rels} | {r.to_entity_id for r in rels}
    rels += session.query(CatalogRelationship).filter(
        CatalogRelationship.status == "TRUSTED",
        (CatalogRelationship.from_entity_id.in_(frontier)) | (CatalogRelationship.to_entity_id.in_(frontier)),
    ).all()

    adjacency: Dict[UUID, List[tuple]] = {}
    seen_rel_ids = set()
    for r in rels:
        if r.id in seen_rel_ids:
            continue
        seen_rel_ids.add(r.id)
        adjacency.setdefault(r.from_entity_id, []).append((r.to_entity_id, r))
        adjacency.setdefault(r.to_entity_id, []).append((r.from_entity_id, r))
    return adjacency


def _shortest_path(adjacency: Dict[UUID, List[tuple]], start: UUID, goal: UUID,
                    max_hops: int) -> Optional[List[tuple]]:
    if start == goal:
        return []
    visited = {start}
    queue = deque([(start, [])])
    while queue:
        node, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for neighbor, rel in adjacency.get(node, []):
            if neighbor in visited:
                continue
            new_path = path + [(neighbor, rel)]
            if neighbor == goal:
                return new_path
            visited.add(neighbor)
            queue.append((neighbor, new_path))
    return None


def find_join_paths(session: Session, entity_ids: List[UUID]) -> List[JoinPathOut]:
    entity_ids = list(dict.fromkeys(entity_ids))  # de-dupe, preserve order
    if len(entity_ids) < 2:
        return []

    adjacency = _trusted_adjacency(session, entity_ids)
    entity_cache: Dict[UUID, CatalogEntity] = {}

    def _entity(eid: UUID) -> CatalogEntity:
        if eid not in entity_cache:
            entity_cache[eid] = session.get(CatalogEntity, eid)
        return entity_cache[eid]

    results: List[JoinPathOut] = []
    for i, a in enumerate(entity_ids):
        for b in entity_ids[i + 1:]:
            path = _shortest_path(adjacency, a, b, MAX_HOPS)
            if not path:
                continue
            steps = [JoinPathStepOut(entity_id=a, entity_name=_entity(a).object_name)]
            min_confidence = 1.0
            for node_id, rel in path:
                steps.append(JoinPathStepOut(entity_id=node_id, entity_name=_entity(node_id).object_name,
                                              relationship_id=rel.id))
                min_confidence = min(min_confidence, rel.confidence)
            results.append(JoinPathOut(
                from_entity=_entity(a).object_name, to_entity=_entity(b).object_name,
                hop_count=len(path), confidence=round(min_confidence, 4), status="TRUSTED", steps=steps,
            ))
    return results
