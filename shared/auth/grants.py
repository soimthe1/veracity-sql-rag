"""
(level, category) grant primitives for this project's own web UI —
deliberately strict:
exact-match pairs, no inheritance on either axis, "the only access
decision in the system" is GrantSet.allows(level, category).

This is a SEPARATE, parallel lattice from CatalogEntity.access_level/
access_category (the provisional classification shim in
shared/config.py's ClassificationConfig) — the two use the same
(int, str) shape on purpose (see docs/integration.md), so a principal's
resolved grants here can be handed straight to the answer/ process's
POST /v1/ask, or used to filter api/ entity endpoints (api/access.py),
with zero translation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Tuple

from shared.access.cells import cell_token, cell_tokens, parse_cell
from shared.config import AuthConfig


@dataclass(frozen=True)
class Grant:
    level: int
    category: str

    @classmethod
    def parse(cls, token: str) -> "Grant":
        """"20B" only (shared/access/cells.py) — raises on any other spelling."""
        level, category = parse_cell(token)
        return cls(level=level, category=category)

    def to_token(self) -> str:
        return cell_token(self.level, self.category)


@dataclass
class GrantSet:
    """An immutable set of (level, category) cells a principal is
    cleared for. Empty means "no data-tier access at all" — default-
    deny: "an empty
    filter matches nothing, never everything." """
    pairs: FrozenSet[Grant] = field(default_factory=frozenset)

    @classmethod
    def empty(cls) -> "GrantSet":
        return cls(pairs=frozenset())

    @classmethod
    def from_pairs(cls, pairs: Iterable[Tuple[int, str]]) -> "GrantSet":
        return cls(pairs=frozenset(Grant(level=lvl, category=(cat or "").upper()) for lvl, cat in pairs))

    @classmethod
    def from_tokens(cls, tokens: Iterable[str]) -> "GrantSet":
        return cls(pairs=frozenset(Grant.parse(t) for t in tokens))

    def to_tokens(self) -> List[str]:
        return cell_tokens((g.level, g.category) for g in self.pairs)

    def allows(self, level: int, category: str) -> bool:
        """The only access decision in the system: exact match, no
        inheritance on either axis."""
        return Grant(level=level, category=(category or "").upper()) in self.pairs

    def union(self, other: "GrantSet") -> "GrantSet":
        return GrantSet(pairs=self.pairs | other.pairs)

    def is_empty(self) -> bool:
        return len(self.pairs) == 0

    def levels(self) -> List[int]:
        return sorted({g.level for g in self.pairs})

    def categories_for(self, level: int) -> List[str]:
        return sorted({g.category for g in self.pairs if g.level == level})

    def as_level_category_list(self) -> List[Dict[str, Any]]:
        return [{"level": g.level, "category": g.category} for g in sorted(self.pairs, key=lambda g: (g.level, g.category))]


class UnrestrictedGrantSet(GrantSet):
    """The "auth disabled, or an unrestricted service caller" posture:
    allows() unconditionally. Never persisted, never sent as-is over the
    wire to answer/'s POST /v1/ask (which treats an empty/omitted grants
    list as default-deny) — api/access.py expands it to every configured
    cell first if it ever needs to hand grants to that endpoint."""

    def allows(self, level: int, category: str) -> bool:  # noqa: D102 - see class docstring
        return True

    def is_empty(self) -> bool:
        return False


def matrix_shape(cfg: AuthConfig) -> Dict[str, list]:
    """The (levels x categories) grid the Security tab's permission-
    matrix editor renders from — fed from this project's own config.yaml
    catalog.auth.levels/categories rather than a separate levels.yaml
    file, so an operator can point it at the exact same scheme
    another system uses if they choose to (see AuthConfig's docstring)."""
    return {
        "levels": sorted(cfg.levels, key=lambda level_row: level_row.get("value", 0)),
        "categories": [c for c in cfg.categories if c.get("enabled", True)],
    }
