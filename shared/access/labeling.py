"""
Data bucketing: decides which (level, category) cell every database,
schema, table and column sits in, and records WHY.

Each axis (level, category) is decided independently by the strongest
layer that has an opinion:

  1. manual     a person placed this object in a cell (catalog_access_labels)
  2. rule       an admin-written rule matches it (catalog_access_rules)
  3. inherited  a human decision (1 or 2) on its parent:
                database -> schema -> table -> column
  4. machine    the object's own evidence, when no human decided:
                 - ai    : sensitivity flags / inferred business domain
                 - auto  : the lattice file's naming conventions matched
                           against the database, schema or table name
                 - inherited machine value from the parent
                for LEVEL the highest (most restrictive) of these wins;
                for CATEGORY the first of ai, auto, parent.
  5. default    the lattice default (normally the reserved quarantine
                cell, e.g. 120Z, which no role can be granted) — nothing
                is ever left unlabelled, and nothing is visible by accident.

The AI never silently overrides a human decision. When its proposal is
more sensitive (a higher level) or points to a different category than a
human-decided cell, the object is flagged for review
(access_review_pending) instead; a reviewer either accepts (which becomes
a manual placement) or keeps the current cell (remembered until the AI's
proposal changes).

Results are materialized onto catalog_entities / catalog_fields
(access_level, access_category + provenance columns), so enforcement at
query time is a plain indexed column comparison — nothing here runs on
the request path of a question.
"""
from __future__ import annotations

import fnmatch
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from shared.access.cells import cell_text
from shared.access.lattice import Lattice
from shared.access.naming import match_category, match_level
from shared.models import (
    CatalogAccessLabel, CatalogAccessRule, CatalogDatabase, CatalogEntity, CatalogField, CatalogHistory,
    CatalogSource,
)

logger = logging.getLogger("shared.access.labeling")

HUMAN_LAYERS = ("manual", "rule")
LAYER_ORDER = ("manual", "rule", "inherited", "ai", "auto", "default")


# ---------------------------------------------------------------------------
# Candidates and resolutions
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    layer: str          # manual | rule | inherited | ai | auto | default
    value: Any
    via: str            # human-readable reason
    human: bool = False
    ref: Optional[str] = None  # e.g. rule id
    root: Optional[str] = None  # for inherited values: the original reason up the chain


@dataclass
class Axis:
    value: Any
    source: str
    via: str
    human: bool
    candidates: List[Candidate] = field(default_factory=list)
    root: str = ""
    chosen: Optional[Candidate] = None

    def to_detail(self) -> Dict[str, Any]:
        return {"src": self.source, "via": self.via, "human": self.human}


@dataclass
class Resolution:
    level: Axis
    category: Axis
    ai_level: Optional[int] = None
    ai_category: Optional[str] = None
    ai_via: Optional[str] = None
    review: bool = False
    review_reason: Optional[str] = None

    @property
    def cell(self) -> Tuple[Optional[int], Optional[str]]:
        return self.level.value, self.category.value

    @property
    def provisional(self) -> bool:
        return not (self.level.human or self.category.human)

    def detail(self) -> Dict[str, Any]:
        cached = self.__dict__.get("_detail")
        if cached is not None:
            return cached
        d: Dict[str, Any] = {"level": self.level.to_detail(), "category": self.category.to_detail()}
        if self.ai_level is not None or self.ai_category is not None:
            d["ai"] = {"level": self.ai_level, "category": self.ai_category, "via": self.ai_via}
        if self.review_reason:
            d["review"] = self.review_reason
        self.__dict__["_detail"] = d
        return d


def _pick(candidates: List[Candidate], is_level: bool) -> Axis:
    """Tiers in LAYER_ORDER; inside the machine tier the level takes the
    maximum, the category the first candidate."""
    tiers: List[List[Candidate]] = [[], [], [], [], []]  # manual, rule, inherited-human, machine, default
    for c in candidates:
        if c.value is None:
            continue
        if c.layer == "manual":
            tiers[0].append(c)
        elif c.layer == "rule":
            tiers[1].append(c)
        elif c.layer == "inherited" and c.human:
            tiers[2].append(c)
        elif c.layer == "default":
            tiers[4].append(c)
        else:
            tiers[3].append(c)
    for i, tier in enumerate(tiers):
        if not tier:
            continue
        chosen = max(tier, key=lambda c: c.value) if (is_level and i == 3) else tier[0]
        return Axis(value=chosen.value, source=chosen.layer, via=chosen.via, human=chosen.human,
                    candidates=candidates, root=chosen.root or chosen.via, chosen=chosen)
    return Axis(value=None, source="none", via="", human=False, candidates=candidates)


def _inherit(parent: Optional[Axis], parent_label: str) -> Optional[Candidate]:
    if parent is None or parent.value is None:
        return None
    layer = "default" if parent.source == "default" else "inherited"
    root = parent.root or parent.via
    return Candidate(layer=layer, value=parent.value, human=parent.human, root=root,
                     via=f"from {parent_label} — {root}" if root else f"from {parent_label}")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _compile(pattern: Optional[str]) -> Optional[re.Pattern]:
    if pattern is None or not pattern.strip():
        return None
    parts = [p.strip() for p in pattern.split(",") if p.strip()]
    return re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in parts), re.IGNORECASE)


# Which patterns apply at which scope (others are rejected when saving).
SCOPE_FIELDS = {
    "database": ("source_pattern", "database_pattern"),
    "schema": ("source_pattern", "database_pattern", "schema_pattern"),
    "table": ("source_pattern", "database_pattern", "schema_pattern", "table_pattern", "object_type",
              "domain_pattern", "classification", "sensitivity_flag"),
    "column": ("source_pattern", "database_pattern", "schema_pattern", "table_pattern", "column_pattern",
               "sensitivity_flag"),
}


@dataclass
class CompiledRule:
    id: str
    name: str
    scope: str
    priority: int
    specificity: int
    set_level: Optional[int]
    set_category: Optional[str]
    source: Optional[re.Pattern]
    database: Optional[re.Pattern]
    schema: Optional[re.Pattern]
    table: Optional[re.Pattern]
    column: Optional[re.Pattern]
    domain: Optional[re.Pattern]
    object_type: Optional[str]
    classification: Optional[str]
    sensitivity_flag: Optional[str]

    @classmethod
    def from_row(cls, r) -> "CompiledRule":
        allowed = SCOPE_FIELDS[r.scope]

        def pat(name):
            return _compile(getattr(r, name)) if name in allowed else None

        def val(name):
            v = getattr(r, name)
            return (v.strip().upper() if isinstance(v, str) and v.strip() else None) if name in allowed else None

        spec = sum(1 for n in allowed if getattr(r, n) not in (None, ""))
        return cls(
            id=str(r.id), name=r.name, scope=r.scope, priority=r.priority or 0, specificity=spec,
            set_level=r.set_level, set_category=(r.set_category or None) and r.set_category.upper(),
            source=pat("source_pattern"), database=pat("database_pattern"), schema=pat("schema_pattern"),
            table=pat("table_pattern"), column=pat("column_pattern"), domain=pat("domain_pattern"),
            object_type=val("object_type"), classification=val("classification"),
            sensitivity_flag=val("sensitivity_flag"),
        )

    def matches(self, *, source="", database="", schema="", table="", column="", object_type=None,
                domain=None, classification=None, flags: Sequence[str] = ()) -> bool:
        if self.source and not self.source.fullmatch(source or ""):
            return False
        if self.database and not self.database.fullmatch(database or ""):
            return False
        if self.schema and not self.schema.fullmatch(schema or ""):
            return False
        if self.table and not self.table.fullmatch(table or ""):
            return False
        if self.column and not self.column.fullmatch(column or ""):
            return False
        if self.domain and not self.domain.fullmatch(domain or ""):
            return False
        if self.object_type and (object_type or "").upper() != self.object_type:
            return False
        if self.classification and (classification or "").upper() != self.classification:
            return False
        if self.sensitivity_flag and self.sensitivity_flag not in {f.upper() for f in flags or ()}:
            return False
        return True


def compile_rules(rows: Iterable) -> Dict[str, List[CompiledRule]]:
    by_scope: Dict[str, List[CompiledRule]] = {s: [] for s in SCOPE_FIELDS}
    ordered = sorted(
        (r for r in rows if r.enabled),
        key=lambda r: (-(r.priority or 0), -sum(1 for n in SCOPE_FIELDS[r.scope] if getattr(r, n) not in (None, "")),
                       str(getattr(r, "created_at", "") or "")),
    )
    for r in ordered:
        by_scope[r.scope].append(CompiledRule.from_row(r))
    return by_scope


def _rule_candidates(rules: List[CompiledRule], **attrs) -> Tuple[List[Candidate], List[Candidate]]:
    lv, cat = [], []
    for rule in rules:
        if not rule.matches(**attrs):
            continue
        via = f'rule "{rule.name}"'
        if rule.set_level is not None:
            lv.append(Candidate("rule", rule.set_level, via, human=True, ref=rule.id))
        if rule.set_category:
            cat.append(Candidate("rule", rule.set_category, via, human=True, ref=rule.id))
    return lv, cat


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

@dataclass
class Pin:
    level: Optional[int]
    category: Optional[str]
    reason: Optional[str]
    set_by: Optional[str]

    def via(self, what: str) -> str:
        who = f" by {self.set_by}" if self.set_by else ""
        why = f" — {self.reason}" if self.reason else ""
        return f"placed manually on this {what}{who}{why}"


class Resolver:
    def __init__(self, lattice: Lattice, classification_cfg, access_cfg, rules: Iterable,
                 pins: Iterable[CatalogAccessLabel]):
        self.lattice = lattice
        self.cls_cfg = classification_cfg
        self.access_cfg = access_cfg
        self.rules = compile_rules(rules)
        self.db_pins: Dict[Any, Pin] = {}
        self.schema_pins: Dict[Tuple[Any, str], Pin] = {}
        self.entity_pins: Dict[Any, Pin] = {}
        self.field_pins: Dict[Any, Pin] = {}
        for p in pins:
            pin = Pin(p.level, (p.category or None) and p.category.upper(), p.reason, p.set_by)
            if p.target_type == "database":
                self.db_pins[p.database_id] = pin
            elif p.target_type == "schema":
                self.schema_pins[(p.database_id, (p.schema_name or "").lower())] = pin
            elif p.target_type == "entity":
                self.entity_pins[p.entity_id] = pin
            elif p.target_type == "field":
                self.field_pins[p.field_id] = pin
        self.level_flags = {k.upper(): v for k, v in (classification_cfg.level_by_sensitivity_flag or {}).items()}
        self.category_flags = {k.upper(): v.upper() for k, v in (classification_cfg.category_by_sensitivity_flag or {}).items()}
        self.category_domains = {k.lower(): v.upper() for k, v in (access_cfg.category_by_domain or {}).items()}
        self.naming = bool(access_cfg.naming_conventions)
        self._ai_cache: Dict[Any, Tuple[Optional[int], Optional[str], Optional[str]]] = {}

    # -- layers ---------------------------------------------------------
    @staticmethod
    def _pin_candidates(pin: Optional[Pin], what: str) -> Tuple[List[Candidate], List[Candidate]]:
        if pin is None:
            return [], []
        lv = [Candidate("manual", pin.level, pin.via(what), human=True)] if pin.level is not None else []
        cat = [Candidate("manual", pin.category, pin.via(what), human=True)] if pin.category else []
        return lv, cat

    def _auto_candidates(self, name: str, what: str) -> Tuple[List[Candidate], List[Candidate]]:
        if not self.naming:
            return [], []
        lv, cat = [], []
        m = match_level(name, self.lattice.level_names)
        if m:
            lv.append(Candidate("auto", self.lattice.snap_level(m[0]), f'{what} name contains "{m[1]}"'))
        c = match_category(name, self.lattice.category_names)
        if c:
            cat.append(Candidate("auto", c[0], f'{what} name contains "{c[1]}"'))
        return lv, cat

    def ai_proposal(self, flags: Sequence[str], domain: Optional[str] = None) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        if not flags and (not domain or domain.lower() not in self.category_domains):
            return None, None, None
        key = (tuple(flags or ()), domain)
        hit = self._ai_cache.get(key)
        if hit is None:
            hit = self._ai_cache[key] = self._ai_proposal(flags, domain)
        return hit

    def _ai_proposal(self, flags: Sequence[str], domain: Optional[str]) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        best_level, best_flag = None, None
        cat, cat_flag, cat_flag_level = None, None, -1
        for flag in flags or ():
            f = flag.upper()
            lv = self.level_flags.get(f)
            if lv is not None and (best_level is None or lv > best_level):
                best_level, best_flag = lv, f
            fc = self.category_flags.get(f)
            if fc and (lv or 0) > cat_flag_level:
                cat, cat_flag, cat_flag_level = fc, f, lv or 0
        reasons = []
        if best_level is not None:
            reasons.append(f"sensitivity flag {best_flag}")
        if cat is None and domain:
            cat = self.category_domains.get(domain.lower())
            if cat:
                reasons.append(f'business domain "{domain}"')
        elif cat is not None and cat_flag != best_flag:
            reasons.append(f"flag {cat_flag} → category {cat}")
        return self.lattice.snap_level(best_level), cat, ", ".join(dict.fromkeys(reasons)) or None

    def _default_candidates(self) -> Tuple[List[Candidate], List[Candidate]]:
        lat = self.lattice
        tag = " (quarantine)" if lat.is_quarantine(lat.default_category) else ""
        lvia = f"nothing decided the level — default level {lat.default_level}"
        cvia = f"nothing decided the category — default category {lat.default_category}{tag}"
        return [Candidate("default", lat.default_level, lvia)], [Candidate("default", lat.default_category, cvia)]

    # -- objects ----------------------------------------------------------
    def resolve_database(self, db_id, source: str, name: str) -> Resolution:
        pl, pc = self._pin_candidates(self.db_pins.get(db_id), "database")
        rl, rc = _rule_candidates(self.rules["database"], source=source, database=name)
        al, ac = self._auto_candidates(name, "database")
        return Resolution(level=_pick(pl + rl + al, True), category=_pick(pc + rc + ac, False))

    def resolve_schema(self, db_id, source: str, database: str, schema: str, parent: Resolution) -> Resolution:
        pl, pc = self._pin_candidates(self.schema_pins.get((db_id, schema.lower())), "schema")
        rl, rc = _rule_candidates(self.rules["schema"], source=source, database=database, schema=schema)
        al, ac = self._auto_candidates(schema, "schema")
        il = [c for c in [_inherit(parent.level, f"database {database}")] if c]
        ic = [c for c in [_inherit(parent.category, f"database {database}")] if c]
        return Resolution(level=_pick(pl + rl + il + al, True), category=_pick(pc + rc + ic + ac, False))

    def resolve_entity(self, e, source: str, database: str, parent: Resolution) -> Resolution:
        flags = list(e.sensitivity_flags or [])
        pl, pc = self._pin_candidates(self.entity_pins.get(e.id), "table")
        rl, rc = _rule_candidates(
            self.rules["table"], source=source, database=database, schema=e.schema_name, table=e.object_name,
            object_type=e.object_type, domain=e.domain, classification=e.classification, flags=flags,
        )
        il = [c for c in [_inherit(parent.level, f"schema {e.schema_name}")] if c]
        ic = [c for c in [_inherit(parent.category, f"schema {e.schema_name}")] if c]
        ai_level, ai_cat, ai_via = self.ai_proposal(flags, e.domain)
        aml = [Candidate("ai", ai_level, f"AI: {ai_via}")] if ai_level is not None else []
        amc = [Candidate("ai", ai_cat, f"AI: {ai_via}")] if ai_cat else []
        al, ac = self._auto_candidates(e.object_name, "table")
        dl, dc = self._default_candidates()
        res = Resolution(level=_pick(pl + rl + il + aml + al + dl, True),
                         category=_pick(pc + rc + ic + amc + ac + dc, False),
                         ai_level=ai_level, ai_category=ai_cat, ai_via=ai_via)
        return self._review(res, e.access_review_dismissed)

    def inherited_field_resolution(self, entity_label: str, parent: Resolution) -> Resolution:
        """What a column with no placement, no matching rule and no flags of
        its own gets: exactly its table's cell. Computed once per table and
        shared by all such columns."""
        il = [c for c in [_inherit(parent.level, f"table {entity_label}")] if c]
        ic = [c for c in [_inherit(parent.category, f"table {entity_label}")] if c]
        return Resolution(level=_pick(il, True), category=_pick(ic, False))

    def resolve_field(self, f, ctx: Tuple[str, str, str, str], entity_label: str, parent: Resolution,
                      plain: Optional[Resolution] = None) -> Resolution:
        """ctx = (source, database, schema, table)."""
        flags = list(f.sensitivity_flags or [])
        field_pin = self.field_pins.get(f.id)
        if self.rules["column"]:
            rl, rc = _rule_candidates(self.rules["column"], source=ctx[0], database=ctx[1], schema=ctx[2],
                                      table=ctx[3], column=f.column_name, flags=flags)
        else:
            rl, rc = [], []
        if plain is not None and field_pin is None and not rl and not rc and not flags:
            return plain
        pl, pc = self._pin_candidates(field_pin, "column")
        il = [c for c in [_inherit(parent.level, f"table {entity_label}")] if c]
        ic = [c for c in [_inherit(parent.category, f"table {entity_label}")] if c]
        ai_level, ai_cat, ai_via = self.ai_proposal(flags)
        aml = [Candidate("ai", ai_level, f"AI: {ai_via}")] if ai_level is not None else []
        amc = [Candidate("ai", ai_cat, f"AI: {ai_via}")] if ai_cat else []
        res = Resolution(level=_pick(pl + rl + il + aml, True), category=_pick(pc + rc + ic + amc, False),
                         ai_level=ai_level, ai_category=ai_cat, ai_via=ai_via)
        return self._review(res, f.access_review_dismissed)

    def _review(self, res: Resolution, dismissed: Optional[str]) -> Resolution:
        reasons = []
        if res.ai_level is not None and res.level.human and res.level.value is not None and res.ai_level > res.level.value:
            reasons.append(f"AI suggests level {res.ai_level} (more sensitive than {res.level.value})")
        if res.ai_category and res.category.human and res.ai_category != res.category.value:
            reasons.append(f"AI suggests category {res.ai_category} instead of {res.category.value}")
        if reasons:
            res.review_reason = "; ".join(reasons)
            res.review = dismissed != ai_token(res.ai_level, res.ai_category)
        return res


def ai_token(level: Optional[int], category: Optional[str]) -> str:
    """The AI proposal a reviewer dismissed, level then category like a
    cell: "20B", or with "-" for an axis the AI didn't propose ("20-", "-B")."""
    return f"{level if level is not None else '-'}{(category or '-').upper()}"


# ---------------------------------------------------------------------------
# Loading, computing, applying
# ---------------------------------------------------------------------------

def build_resolver(session: Session, cfg, lattice: Lattice, rules_override: Optional[List] = None) -> Resolver:
    rules = rules_override if rules_override is not None else session.query(CatalogAccessRule).all()
    pins = session.query(CatalogAccessLabel).all()
    return Resolver(lattice, cfg.catalog.classification, cfg.catalog.access, rules, pins)


class _Row:
    """Plain attribute bag for a Core row (cheaper than ORM objects)."""
    __slots__ = ("__dict__",)

    def __init__(self, mapping):
        self.__dict__.update(mapping)


@dataclass
class Snapshot:
    databases: List[_Row]
    entities: List[_Row]
    fields_by_entity: Dict[Any, List[_Row]]


_ENTITY_COLS = (CatalogEntity.id, CatalogEntity.database_id, CatalogEntity.schema_name, CatalogEntity.object_name,
                CatalogEntity.object_type, CatalogEntity.domain, CatalogEntity.classification,
                CatalogEntity.sensitivity_flags, CatalogEntity.access_level, CatalogEntity.access_category,
                CatalogEntity.access_level_source, CatalogEntity.access_review_dismissed, CatalogEntity.access_signature)
_FIELD_COLS = (CatalogField.id, CatalogField.entity_id, CatalogField.column_name, CatalogField.sensitivity_flags,
               CatalogField.access_level, CatalogField.access_category, CatalogField.access_level_source,
               CatalogField.access_review_dismissed, CatalogField.access_signature)


def load_snapshot(session: Session, database_ids: Optional[Iterable] = None) -> Snapshot:
    q = (select(CatalogDatabase.id, CatalogDatabase.database_name, CatalogSource.name.label("source_name"))
         .join(CatalogSource, CatalogSource.id == CatalogDatabase.source_id))
    ids = list(database_ids) if database_ids is not None else None
    if ids is not None:
        q = q.where(CatalogDatabase.id.in_(ids))
    dbs = list(session.execute(q))
    db_ids = [d.id for d in dbs]
    if not db_ids:
        return Snapshot([], [], {})
    ents = list(session.execute(select(*_ENTITY_COLS).where(CatalogEntity.database_id.in_(db_ids))))
    ent_ids = [e.id for e in ents]
    fields_by_entity: Dict[Any, List[_Row]] = {}
    # chunked IN() so a very large catalog never builds one enormous statement
    for i in range(0, len(ent_ids), 5000):
        chunk = ent_ids[i:i + 5000]
        for r in session.execute(select(*_FIELD_COLS).where(CatalogField.entity_id.in_(chunk))):
            fields_by_entity.setdefault(r.entity_id, []).append(r)
    return Snapshot(dbs, ents, fields_by_entity)


@dataclass
class Computed:
    """Resolutions for one snapshot, keyed by id."""
    databases: Dict[Any, Resolution] = field(default_factory=dict)
    schemas: Dict[Tuple[Any, str], Resolution] = field(default_factory=dict)
    entities: Dict[Any, Resolution] = field(default_factory=dict)
    fields: Dict[Any, Resolution] = field(default_factory=dict)


def compute(resolver: Resolver, snap: Snapshot) -> Computed:
    out = Computed()
    db_by_id = {d.id: d for d in snap.databases}
    for d in snap.databases:
        out.databases[d.id] = resolver.resolve_database(d.id, d.source_name, d.database_name)
    for e in snap.entities:
        d = db_by_id[e.database_id]
        skey = (e.database_id, e.schema_name.lower())
        schema_res = out.schemas.get(skey)
        if schema_res is None:
            schema_res = resolver.resolve_schema(d.id, d.source_name, d.database_name, e.schema_name, out.databases[d.id])
            out.schemas[skey] = schema_res
        ent_res = resolver.resolve_entity(e, d.source_name, d.database_name, schema_res)
        out.entities[e.id] = ent_res
        fields = snap.fields_by_entity.get(e.id)
        if not fields:
            continue
        label = f"{e.schema_name}.{e.object_name}"
        ctx = (d.source_name, d.database_name, e.schema_name, e.object_name)
        plain = resolver.inherited_field_resolution(label, ent_res)
        for f in fields:
            out.fields[f.id] = resolver.resolve_field(f, ctx, label, ent_res, plain)
    return out


def _new_values(res: Resolution) -> Dict[str, Any]:
    cached = res.__dict__.get("_values")
    if cached is not None:
        return cached
    import hashlib
    import json

    vals = {
        "access_level": res.level.value,
        "access_category": res.category.value,
        "access_level_source": res.level.source,
        "access_category_source": res.category.source,
        "access_detail": res.detail(),
        "access_ai_level": res.ai_level,
        "access_ai_category": res.ai_category,
        "access_review_pending": bool(res.review),
        "access_classification_provisional": res.provisional,
    }
    vals["access_signature"] = hashlib.md5(json.dumps(vals, sort_keys=True, default=str).encode()).hexdigest()
    res.__dict__["_values"] = vals
    return vals


def _row_changes(row, res: Resolution) -> Optional[Dict[str, Any]]:
    new = _new_values(res)
    if row.access_signature == new["access_signature"]:
        return None
    return {**new, "id": row.id}


@dataclass
class ApplyStats:
    entities_changed: int = 0
    fields_changed: int = 0
    cells_moved: int = 0
    review_pending: int = 0
    seconds: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def diff(snap: Snapshot, computed: Computed) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Tuple[str, Any, str, str]]]:
    ent_updates, field_updates, moved = [], [], []
    for e in snap.entities:
        res = computed.entities[e.id]
        ch = _row_changes(e, res)
        if ch:
            ent_updates.append(ch)
            if e.access_level_source is not None and (e.access_level, e.access_category) != res.cell:
                moved.append(("entity", e.id, cell_text(e.access_level, e.access_category), cell_text(*res.cell)))
        for f in snap.fields_by_entity.get(e.id, ()):
            fres = computed.fields[f.id]
            ch = _row_changes(f, fres)
            if ch:
                field_updates.append(ch)
                if f.access_level_source is not None and (f.access_level, f.access_category) != fres.cell:
                    moved.append(("field", f.id, cell_text(f.access_level, f.access_category), cell_text(*fres.cell)))
    return ent_updates, field_updates, moved


_UPDATE_COLS = ("access_level", "access_category", "access_level_source", "access_category_source", "access_detail",
                "access_ai_level", "access_ai_category", "access_review_pending", "access_classification_provisional",
                "access_signature")


def _bulk_update(session: Session, table: str, rows: List[Dict[str, Any]]) -> None:
    """One round trip per 5,000 rows: stage into a temp table, then a single
    UPDATE ... FROM. (Row-by-row UPDATEs took ~20 s per 100k columns.)"""
    if not rows:
        return
    import json

    if session.bind.dialect.name != "postgresql":  # pragma: no cover - tests use Postgres
        for i in range(0, len(rows), 2000):
            session.execute(update(CatalogEntity if table == "catalog_entities" else CatalogField), rows[i:i + 2000])
        return
    from psycopg2.extras import execute_values

    raw = session.connection().connection
    with raw.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _access_labels (id uuid PRIMARY KEY, access_level int, "
            "access_category varchar(4), access_level_source varchar(16), access_category_source varchar(16), "
            "access_detail jsonb, access_ai_level int, access_ai_category varchar(4), access_review_pending bool, "
            "access_classification_provisional bool, access_signature varchar(32)) ON COMMIT DROP"
        )
        cur.execute("TRUNCATE _access_labels")
        for i in range(0, len(rows), 5000):
            execute_values(
                cur,
                f"INSERT INTO _access_labels (id, {', '.join(_UPDATE_COLS)}) VALUES %s",
                [(str(r["id"]), r["access_level"], r["access_category"], r["access_level_source"],
                  r["access_category_source"], json.dumps(r["access_detail"]), r["access_ai_level"],
                  r["access_ai_category"], r["access_review_pending"], r["access_classification_provisional"],
                  r["access_signature"])
                 for r in rows[i:i + 5000]],
                page_size=5000,
            )
        sets = ", ".join(f"{c} = s.{c}" for c in _UPDATE_COLS)
        cur.execute(f"UPDATE {table} t SET {sets} FROM _access_labels s WHERE t.id = s.id")
        cur.execute("TRUNCATE _access_labels")


def recompute(session: Session, cfg, lattice: Lattice, database_ids: Optional[Iterable] = None,
              reason: str = "recompute") -> ApplyStats:
    """Recompute and persist labels for the given databases (all when None).
    Only rows whose label or provenance actually changed are written; a cell
    move is also written to catalog_history."""
    started = time.perf_counter()
    resolver = build_resolver(session, cfg, lattice)
    snap = load_snapshot(session, database_ids)
    computed = compute(resolver, snap)
    ent_updates, field_updates, moved = diff(snap, computed)
    _bulk_update(session, CatalogEntity.__table__.name, ent_updates)
    _bulk_update(session, CatalogField.__table__.name, field_updates)
    if moved:
        session.add_all([
            CatalogHistory(subject_type=kind, subject_id=obj_id, field_changed="access_classification",
                           old_value=old, new_value=new, reason=reason)
            for kind, obj_id, old, new in moved[:5000]
        ])
    session.commit()
    stats = ApplyStats(
        entities_changed=len(ent_updates), fields_changed=len(field_updates), cells_moved=len(moved),
        review_pending=sum(1 for r in computed.entities.values() if r.review) + sum(1 for r in computed.fields.values() if r.review),
        seconds=round(time.perf_counter() - started, 3),
    )
    if ent_updates or field_updates:
        logger.info("Access labels recomputed (%s): %s", reason, stats.as_dict())
    return stats


def preview(session: Session, cfg, lattice: Lattice, rules_override: List) -> Dict[str, Any]:
    """Dry-run with a hypothetical rule set: how many tables/columns would
    change cell, with a few examples. Writes nothing."""
    resolver = build_resolver(session, cfg, lattice, rules_override=rules_override)
    snap = load_snapshot(session)
    computed = compute(resolver, snap)
    names = {e.id: f"{e.schema_name}.{e.object_name}" for e in snap.entities}
    changed_tables, changed_columns, examples = 0, 0, []
    for e in snap.entities:
        new = computed.entities[e.id].cell
        if (e.access_level, e.access_category) != new:
            changed_tables += 1
            if len(examples) < 12:
                examples.append({"kind": "table", "name": names[e.id],
                                 "from": cell_text(e.access_level, e.access_category), "to": cell_text(*new)})
        for f in snap.fields_by_entity.get(e.id, ()):
            fnew = computed.fields[f.id].cell
            if (f.access_level, f.access_category) != fnew:
                changed_columns += 1
    return {"tables_changed": changed_tables, "columns_changed": changed_columns, "examples": examples}


# ---------------------------------------------------------------------------
# Explanations (the "why is this here?" panel)
# ---------------------------------------------------------------------------

def _axis_explain(axis: Axis) -> Dict[str, Any]:
    return {
        "value": axis.value, "source": axis.source, "via": axis.via, "human": axis.human,
        "candidates": [
            {"layer": c.layer, "value": c.value, "via": c.via, "human": c.human,
             "won": c is axis.chosen}
            for c in axis.candidates if c.value is not None
        ],
    }


def explain_resolution(res: Resolution) -> Dict[str, Any]:
    return {
        "level": _axis_explain(res.level), "category": _axis_explain(res.category),
        "ai": {"level": res.ai_level, "category": res.ai_category, "via": res.ai_via},
        "review": res.review, "review_reason": res.review_reason,
    }


def resolve_one(session: Session, cfg, lattice: Lattice, *, database_id, schema_name: Optional[str] = None,
                entity_id=None, field_id=None) -> Resolution:
    """Fresh resolution of one object and its parent chain (for explain)."""
    resolver = build_resolver(session, cfg, lattice)
    snap = load_snapshot(session, [database_id])
    if not snap.databases:
        raise KeyError("database not found")
    d = snap.databases[0]
    db_res = resolver.resolve_database(d.id, d.source_name, d.database_name)
    if schema_name is None and entity_id is None:
        return db_res
    if entity_id is None:
        return resolver.resolve_schema(d.id, d.source_name, d.database_name, schema_name, db_res)
    e = next((x for x in snap.entities if x.id == entity_id), None)
    if e is None:
        raise KeyError("table not found")
    s_res = resolver.resolve_schema(d.id, d.source_name, d.database_name, e.schema_name, db_res)
    e_res = resolver.resolve_entity(e, d.source_name, d.database_name, s_res)
    if field_id is None:
        return e_res
    f = next((x for x in snap.fields_by_entity.get(e.id, ()) if x.id == field_id), None)
    if f is None:
        raise KeyError("column not found")
    return resolver.resolve_field(f, (d.source_name, d.database_name, e.schema_name, e.object_name),
                                  f"{e.schema_name}.{e.object_name}", e_res)


@dataclass
class Containers:
    databases: List[Any]
    schemas: List[Tuple[Any, str, int]]          # (database_id, schema_name, queryable tables)
    tables_per_database: Dict[Any, int]


def container_cells(session: Session, cfg, lattice: Lattice) -> Tuple[Containers, Computed]:
    """Databases and schemas aren't stored with a cell (only tables and
    columns are) — this computes theirs, for the bucket grid. Reads only
    the databases and a GROUP BY of schemas, never the columns."""
    resolver = build_resolver(session, cfg, lattice)
    dbs = list(session.execute(
        select(CatalogDatabase.id, CatalogDatabase.database_name, CatalogSource.name.label("source_name"))
        .join(CatalogSource, CatalogSource.id == CatalogDatabase.source_id)))
    rows = session.execute(
        select(CatalogEntity.database_id, CatalogEntity.schema_name,
               func.count(CatalogEntity.id).filter(CatalogEntity.object_type.in_(("TABLE", "VIEW"))))
        .group_by(CatalogEntity.database_id, CatalogEntity.schema_name)).all()
    merged: Dict[Tuple[Any, str], Tuple[Any, str, int]] = {}
    for db_id, schema, n in rows:
        key = (db_id, schema.lower())
        prev = merged.get(key)
        merged[key] = (db_id, prev[1] if prev else schema, (prev[2] if prev else 0) + n)
    per_db: Dict[Any, int] = {}
    for db_id, _, n in merged.values():
        per_db[db_id] = per_db.get(db_id, 0) + n
    comp = Computed()
    db_by_id = {d.id: d for d in dbs}
    for d in dbs:
        comp.databases[d.id] = resolver.resolve_database(d.id, d.source_name, d.database_name)
    for (db_id, lower), (_, schema, _) in merged.items():
        d = db_by_id.get(db_id)
        if d is not None:
            comp.schemas[(db_id, lower)] = resolver.resolve_schema(d.id, d.source_name, d.database_name, schema,
                                                                   comp.databases[d.id])
    return Containers(dbs, list(merged.values()), per_db), comp


def needs_initial_labeling(session: Session) -> bool:
    return session.query(CatalogEntity.id).filter(CatalogEntity.access_level_source.is_(None)).limit(1).first() is not None
