"""
The classification lattice — levels x categories — read from the same
levels.yaml format (config/levels.yaml; see the
comments in that file). Display metadata only: the access decision itself is
always exact-match on the (level, category) pair (shared/auth/grants.py).

A cell is written level then category, "20B", "40A" — the knowledge
platform's spelling, strictly (shared/access/cells.py). Internally it is
always the (int level, str category) pair.

Loaded lazily and re-read when the file's modification time changes, so an
operator can rename a level, add a cell label or enable a category without
restarting anything.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from shared.access.cells import CellError, cell_token, parse_cell  # noqa: F401 - re-exported

logger = logging.getLogger("shared.access.lattice")

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Level:
    value: int
    key: str
    name: str
    name_ar: str
    color: str


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    name_ar: str
    enabled: bool
    reserved: bool


@dataclass
class Lattice:
    levels: List[Level]
    categories: List[Category]
    default_level: int
    default_category: str
    cell_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # "auto" naming conventions, from the lattice file's category_folders /
    # folder_conventions — normalized keys (see shared/access/naming.py).
    category_names: Dict[str, str] = field(default_factory=dict)
    level_names: Dict[str, int] = field(default_factory=dict)
    source_path: Optional[str] = None

    # ---------------------------------------------------------------- lookups
    @property
    def level_values(self) -> List[int]:
        return [lv.value for lv in self.levels]

    @property
    def enabled_categories(self) -> List[Category]:
        return [c for c in self.categories if c.enabled]

    @property
    def grantable_categories(self) -> List[Category]:
        """Categories a role may be granted: enabled and not reserved (the
        quarantine category is never grantable — that's what makes
        default-deny structural rather than conventional)."""
        return [c for c in self.categories if c.enabled and not c.reserved]

    def level(self, value: int) -> Optional[Level]:
        return next((lv for lv in self.levels if lv.value == value), None)

    def category(self, key: str) -> Optional[Category]:
        key = (key or "").upper()
        return next((c for c in self.categories if c.key == key), None)

    def is_valid_cell(self, level: int, category: str, grantable: bool = False) -> bool:
        cat = self.category(category)
        if self.level(level) is None or cat is None:
            return False
        if grantable:
            return cat.enabled and not cat.reserved
        return True

    def snap_level(self, value: Optional[int]) -> Optional[int]:
        """Snap a level UP to the nearest configured tier (over-classify when
        a value falls between two), capped at the highest tier."""
        if value is None:
            return None
        for lv in self.levels:
            if lv.value >= value:
                return lv.value
        return self.levels[-1].value if self.levels else value

    def is_quarantine(self, category: Optional[str]) -> bool:
        cat = self.category(category or "")
        return bool(cat and cat.reserved)

    def cell_label(self, level: int, category: str) -> Optional[Dict[str, str]]:
        return self.cell_labels.get(cell_token(level, category))

    # ---------------------------------------------------------------- output
    def to_dict(self) -> Dict[str, Any]:
        return {
            "levels": [lv.__dict__ for lv in self.levels],
            "categories": [c.__dict__ for c in self.categories],
            "default_level": self.default_level,
            "default_category": self.default_category,
            "cell_labels": self.cell_labels,
        }


def _resolve_path(path: str) -> Optional[Path]:
    p = Path(path)
    if p.is_absolute():
        return p if p.exists() else None
    for base in (Path.cwd(), _REPO_ROOT):
        candidate = base / p
        if candidate.exists():
            return candidate
    return None


def _from_auth_config(auth_cfg) -> Lattice:
    levels = [
        Level(value=int(r["value"]), key=str(r.get("key", r["value"])), name=str(r.get("name", r["value"])),
              name_ar=str(r.get("name_ar", r.get("name", r["value"]))), color=str(r.get("color", "#6B7C93")))
        for r in sorted(auth_cfg.levels, key=lambda r: r.get("value", 0))
    ]
    # de-duplicate values (an old default config listed 40 twice)
    seen, uniq = set(), []
    for lv in levels:
        if lv.value not in seen:
            seen.add(lv.value)
            uniq.append(lv)
    cats = [
        Category(key=str(c["key"]).upper(), name=str(c.get("name", c["key"])), name_ar=str(c.get("name_ar", c.get("name", c["key"]))),
                 enabled=bool(c.get("enabled", True)), reserved=bool(c.get("reserved", False)))
        for c in auth_cfg.categories
    ]
    return Lattice(levels=uniq, categories=cats,
                   default_level=uniq[-1].value if uniq else 100,
                   default_category=cats[-1].key if cats else "Z")


def _parse(raw: Dict[str, Any], source_path: str) -> Lattice:
    from shared.access.naming import normalize_phrase

    levels = []
    for r in raw.get("levels") or []:
        levels.append(Level(
            value=int(r["value"]), key=str(r.get("key", r["value"])), name=str(r.get("name", r["value"])),
            name_ar=str(r.get("name_ar") or r.get("name", r["value"])), color=str(r.get("color") or "#6B7C93"),
        ))
    levels.sort(key=lambda lv: lv.value)
    cats = []
    for c in raw.get("categories") or []:
        cats.append(Category(
            key=str(c["key"]).upper(), name=str(c.get("name", c["key"])),
            name_ar=str(c.get("name_ar") or c.get("name", c["key"])),
            enabled=bool(c.get("enabled", True)), reserved=bool(c.get("reserved", False)),
        ))
    if not levels or not cats:
        raise ValueError("lattice file needs at least one level and one category")
    labels: Dict[str, Dict[str, str]] = {}
    for token, v in (raw.get("cell_labels") or {}).items():
        try:
            lvl, cat = parse_cell(str(token))
        except CellError as exc:
            # Not skipped: a label silently missing is how a wrong spelling hides.
            raise ValueError(f"cell_labels in {source_path}: {exc}") from exc
        if isinstance(v, str):
            v = {"name": v}
        labels[cell_token(lvl, cat)] = {"name": str(v.get("name", "")), "name_ar": str(v.get("name_ar") or v.get("name", ""))}
    category_names = {normalize_phrase(k): str(v).upper() for k, v in (raw.get("category_folders") or {}).items()}
    level_names = {normalize_phrase(k): int(v) for k, v in (raw.get("folder_conventions") or {}).items()}
    return Lattice(
        levels=levels, categories=cats,
        default_level=int(raw.get("default_level", levels[-1].value)),
        default_category=str(raw.get("default_category", cats[-1].key)).upper(),
        cell_labels=labels, category_names=category_names, level_names=level_names,
        source_path=source_path,
    )


_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Lattice]] = {}


def load_lattice(cfg) -> Lattice:
    """`cfg`: the AppConfig. Cached per file, re-read when its mtime changes."""
    path = _resolve_path(cfg.catalog.access.lattice_file)
    if path is None:
        key = "__auth_config__"
        with _lock:
            if key not in _cache:
                logger.warning("Lattice file %s not found — using catalog.auth.levels/categories instead.",
                               cfg.catalog.access.lattice_file)
                _cache[key] = (0.0, _from_auth_config(cfg.catalog.auth))
            return _cache[key][1]
    key = str(path)
    mtime = os.path.getmtime(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            with open(path, "r", encoding="utf-8") as f:
                lattice = _parse(yaml.safe_load(f) or {}, key)
        except Exception as exc:  # noqa: BLE001 - keep serving the last good copy
            if hit:
                logger.error("Could not re-read %s (%s) — keeping the previous version.", path, exc)
                return hit[1]
            raise
        _cache[key] = (mtime, lattice)
        return lattice
