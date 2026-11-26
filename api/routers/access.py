"""
Data access realm API (Data access page, Ask's scope banner, the profile
popover). Reads are cheap indexed GROUP BYs over the materialized labels;
every write (a placement, a rule, a review decision) is audited in
app_access_audit and immediately recomputes the affected labels, so
enforcement never lags behind what an admin just did.

  GET    /v1/access/lattice              the matrix (public display metadata)
  GET    /v1/access/scope                what the CALLER can query
  --- admin only ---
  GET    /v1/access/grid                 object counts per cell
  GET    /v1/access/objects              objects in a cell / search
  GET    /v1/access/explain              why an object is in its cell
  POST   /v1/access/placements           move objects to a cell (manual pin)
  POST   /v1/access/placements/reset     back to automatic
  GET/POST/PATCH/DELETE /v1/access/rules, POST /v1/access/rules/preview
  GET    /v1/access/review, POST /v1/access/review/{accept|dismiss}
  GET    /v1/access/simulate?cells=10A,40C   "view as" these cells
  POST   /v1/access/recompute
  GET    /v1/access/audit
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from api.access import get_current_principal, require_admin
from api.deps import get_config, get_db
from shared.access import labeling
from shared.access.cells import CellError, cell_text, cell_token, cell_tokens, parse_cell_list, partial_cell_text
from shared.access.lattice import Lattice, load_lattice
from shared.access.policy import has_full_access, record_access_change
from shared.auth.login import Principal
from shared.config import AppConfig
from shared.models import (
    AppAccessAudit, CatalogAccessLabel, CatalogAccessRule, CatalogDatabase, CatalogEntity, CatalogField,
    CatalogSource,
)

router = APIRouter(prefix="/v1/access", tags=["access"])

Kind = Literal["database", "schema", "table", "column"]
_QUERYABLE = ("TABLE", "VIEW")
_TARGET_TYPE = {"database": "database", "schema": "schema", "table": "entity", "column": "field"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class Target(BaseModel):
    kind: Kind
    id: Optional[UUID] = None           # table / column id, or database id for kind=database
    database_id: Optional[UUID] = None  # for kind=schema
    schema_name: Optional[str] = None   # for kind=schema


class PlacementIn(BaseModel):
    targets: List[Target] = Field(..., min_length=1, max_length=500)
    level: Optional[int] = None
    category: Optional[str] = None
    reason: Optional[str] = None


class ResetIn(BaseModel):
    targets: List[Target] = Field(..., min_length=1, max_length=500)


class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    enabled: bool = True
    priority: int = 100
    scope: Literal["database", "schema", "table", "column"]
    source_pattern: Optional[str] = None
    database_pattern: Optional[str] = None
    schema_pattern: Optional[str] = None
    table_pattern: Optional[str] = None
    column_pattern: Optional[str] = None
    object_type: Optional[str] = None
    domain_pattern: Optional[str] = None
    classification: Optional[str] = None
    sensitivity_flag: Optional[str] = None
    set_level: Optional[int] = None
    set_category: Optional[str] = None
    note: Optional[str] = None


class RuleOut(RuleIn):
    id: UUID
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PreviewIn(BaseModel):
    rule: Optional[RuleIn] = None
    rule_id: Optional[UUID] = None      # editing an existing rule (replaced by `rule`)
    delete: bool = False                # previewing the removal of rule_id


class ReviewIn(BaseModel):
    targets: List[Target] = Field(..., min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lattice(cfg: AppConfig) -> Lattice:
    return load_lattice(cfg)


def _check_cell(lat: Lattice, level: Optional[int], category: Optional[str]) -> None:
    if level is None and not category:
        raise HTTPException(status_code=400, detail="Give a level, a category, or both.")
    if level is not None and lat.level(level) is None:
        raise HTTPException(status_code=400, detail=f"Unknown level {level}.")
    if category and lat.category(category) is None:
        raise HTTPException(status_code=400, detail=f"Unknown category {category}.")


def _entity_names(db: Session):
    return (db.query(CatalogEntity, CatalogDatabase.database_name, CatalogSource.name)
            .join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
            .join(CatalogSource, CatalogSource.id == CatalogDatabase.source_id))


def _principal_cells(principal: Principal) -> List[str]:
    return cell_tokens((g.level, g.category) for g in principal.grants.pairs)


def _visibility(db: Session, allows, full: bool) -> Dict[str, Any]:
    """Counts of queryable tables/views and their columns visible under
    `allows(level, category)` — the same exact-match rule Ask enforces."""
    rows = (db.query(CatalogDatabase.database_name, CatalogEntity.access_level, CatalogEntity.access_category,
                     func.count(CatalogEntity.id))
            .join(CatalogEntity, CatalogEntity.database_id == CatalogDatabase.id)
            .filter(CatalogEntity.object_type.in_(_QUERYABLE), CatalogEntity.status != "STALE")
            .group_by(CatalogDatabase.database_name, CatalogEntity.access_level, CatalogEntity.access_category).all())
    per_db: Dict[str, Dict[str, int]] = {}
    for dbn, lv, cat, n in rows:
        d = per_db.setdefault(dbn, {"visible": 0, "total": 0})
        d["total"] += n
        if full or allows(lv, cat):
            d["visible"] += n
    hidden_cols = 0
    if not full:
        frows = (db.query(CatalogEntity.access_level, CatalogEntity.access_category, CatalogField.access_level,
                          CatalogField.access_category, func.count(CatalogField.id))
                 .join(CatalogEntity, CatalogEntity.id == CatalogField.entity_id)
                 .filter(CatalogEntity.object_type.in_(_QUERYABLE), CatalogEntity.status != "STALE")
                 .group_by(CatalogEntity.access_level, CatalogEntity.access_category, CatalogField.access_level,
                           CatalogField.access_category).all())
        for elv, ecat, flv, fcat, n in frows:
            if allows(elv, ecat) and not allows(flv if flv is not None else elv, fcat or ecat):
                hidden_cols += n
    return {
        "tables_total": sum(d["total"] for d in per_db.values()),
        "tables_visible": sum(d["visible"] for d in per_db.values()),
        "databases_total": len(per_db),
        "databases_visible": sum(1 for d in per_db.values() if d["visible"] > 0),
        "columns_hidden": hidden_cols,
        "databases": sorted(({"database": k, **v} for k, v in per_db.items()), key=lambda x: (-x["visible"], x["database"])),
    }


def _entity_item(e: CatalogEntity, database: str, source: str) -> Dict[str, Any]:
    return {
        "kind": "table", "id": str(e.id), "database_id": str(e.database_id), "database": database, "source": source,
        "schema": e.schema_name, "name": e.object_name, "object_type": e.object_type,
        "semantic_name": e.semantic_name, "level": e.access_level, "category": e.access_category,
        "level_source": e.access_level_source, "category_source": e.access_category_source,
        "detail": e.access_detail or {}, "ai_level": e.access_ai_level, "ai_category": e.access_ai_category,
        "review_pending": bool(e.access_review_pending),
        "pinned": "manual" in (e.access_level_source, e.access_category_source),
    }


def _field_item(f: CatalogField, e: CatalogEntity, database: str, source: str) -> Dict[str, Any]:
    return {
        "kind": "column", "id": str(f.id), "database_id": str(e.database_id), "database": database, "source": source,
        "schema": e.schema_name, "table": e.object_name, "table_id": str(e.id), "name": f.column_name,
        "data_type": f.data_type, "semantic_name": f.semantic_name,
        "level": f.access_level, "category": f.access_category,
        "level_source": f.access_level_source, "category_source": f.access_category_source,
        "detail": f.access_detail or {}, "ai_level": f.access_ai_level, "ai_category": f.access_ai_category,
        "review_pending": bool(f.access_review_pending),
        "pinned": "manual" in (f.access_level_source, f.access_category_source),
        "table_level": e.access_level, "table_category": e.access_category,
    }


def _container_items(db: Session, cfg: AppConfig, lat: Lattice, kind: str) -> List[Dict[str, Any]]:
    cont, comp = labeling.container_cells(db, cfg, lat)
    db_by_id = {d.id: d for d in cont.databases}

    def item(r, **kw):
        return {**kw, "level": r.level.value, "category": r.category.value,
                "level_source": r.level.source if r.level.value is not None else None,
                "category_source": r.category.source if r.category.value is not None else None,
                "detail": r.detail(), "review_pending": False}

    items = []
    if kind == "database":
        pinned = labeling_pins(db, "database")
        for d in cont.databases:
            items.append(item(comp.databases[d.id], kind="database", id=str(d.id), database_id=str(d.id),
                              database=d.database_name, source=d.source_name, name=d.database_name,
                              pinned=d.id in pinned, tables=cont.tables_per_database.get(d.id, 0)))
    else:
        pinned = labeling_pins(db, "schema")
        for db_id, schema, n in cont.schemas:
            d = db_by_id.get(db_id)
            key = (db_id, schema.lower())
            if d is None or key not in comp.schemas:
                continue
            items.append(item(comp.schemas[key], kind="schema", id=f"{d.id}:{schema}", database_id=str(d.id),
                              database=d.database_name, source=d.source_name, schema=schema, name=schema,
                              pinned=key in pinned, tables=n))
    return items


def labeling_pins(db: Session, target_type: str):
    rows = db.query(CatalogAccessLabel).filter(CatalogAccessLabel.target_type == target_type).all()
    if target_type == "database":
        return {r.database_id for r in rows}
    return {(r.database_id, (r.schema_name or "").lower()) for r in rows}


def _resolve_target(db: Session, t: Target) -> Tuple[Dict[str, Any], Any]:
    """-> (pin key columns, database_id)"""
    if t.kind == "database":
        d = db.get(CatalogDatabase, t.id) if t.id else None
        if d is None:
            raise HTTPException(status_code=404, detail="database not found")
        return {"target_type": "database", "database_id": d.id}, d.id
    if t.kind == "schema":
        if not t.database_id or not t.schema_name:
            raise HTTPException(status_code=400, detail="schema targets need database_id and schema_name")
        exists = db.query(CatalogEntity.id).filter(CatalogEntity.database_id == t.database_id,
                                                   func.lower(CatalogEntity.schema_name) == t.schema_name.lower()).first()
        if not exists:
            raise HTTPException(status_code=404, detail="schema not found")
        return {"target_type": "schema", "database_id": t.database_id, "schema_name": t.schema_name}, t.database_id
    if t.kind == "table":
        e = db.get(CatalogEntity, t.id) if t.id else None
        if e is None:
            raise HTTPException(status_code=404, detail="table not found")
        return {"target_type": "entity", "entity_id": e.id}, e.database_id
    f = db.get(CatalogField, t.id) if t.id else None
    if f is None:
        raise HTTPException(status_code=404, detail="column not found")
    e = db.get(CatalogEntity, f.entity_id)
    return {"target_type": "field", "field_id": f.id}, e.database_id


def _find_pin(db: Session, key: Dict[str, Any]) -> Optional[CatalogAccessLabel]:
    q = db.query(CatalogAccessLabel).filter(CatalogAccessLabel.target_type == key["target_type"])
    if key["target_type"] == "database":
        q = q.filter(CatalogAccessLabel.database_id == key["database_id"])
    elif key["target_type"] == "schema":
        q = q.filter(CatalogAccessLabel.database_id == key["database_id"],
                     func.lower(CatalogAccessLabel.schema_name) == key["schema_name"].lower())
    elif key["target_type"] == "entity":
        q = q.filter(CatalogAccessLabel.entity_id == key["entity_id"])
    else:
        q = q.filter(CatalogAccessLabel.field_id == key["field_id"])
    return q.one_or_none()


def _target_text(db: Session, t: Target) -> str:
    if t.kind == "database":
        d = db.get(CatalogDatabase, t.id)
        return d.database_name if d else str(t.id)
    if t.kind == "schema":
        d = db.get(CatalogDatabase, t.database_id)
        return f"{d.database_name if d else t.database_id}.{t.schema_name}"
    if t.kind == "table":
        e = db.get(CatalogEntity, t.id)
        return f"{e.schema_name}.{e.object_name}" if e else str(t.id)
    f = db.get(CatalogField, t.id)
    e = db.get(CatalogEntity, f.entity_id) if f else None
    return f"{e.schema_name}.{e.object_name}.{f.column_name}" if f and e else str(t.id)


def _current_cell(db: Session, t: Target, cfg: AppConfig, lat: Lattice) -> str:
    try:
        if t.kind == "table":
            e = db.get(CatalogEntity, t.id)
            return cell_text(e.access_level, e.access_category)
        if t.kind == "column":
            f = db.get(CatalogField, t.id)
            return cell_text(f.access_level, f.access_category)
        res = labeling.resolve_one(db, cfg, lat, database_id=t.id if t.kind == "database" else t.database_id,
                                   schema_name=t.schema_name if t.kind == "schema" else None)
        return cell_text(res.level.value, res.category.value, missing="(unresolved)")
    except Exception:  # noqa: BLE001 - only for the audit text
        return "?"


def _recompute(db: Session, cfg: AppConfig, lat: Lattice, database_ids, reason: str) -> Dict[str, Any]:
    return labeling.recompute(db, cfg, lat, database_ids=database_ids, reason=reason).as_dict()


def _validate_rule(lat: Lattice, body: RuleIn) -> None:
    _check_cell(lat, body.set_level, body.set_category)
    allowed = set(labeling.SCOPE_FIELDS[body.scope])
    extra = [n for n in ("source_pattern", "database_pattern", "schema_pattern", "table_pattern", "column_pattern",
                         "object_type", "domain_pattern", "classification", "sensitivity_flag")
             if getattr(body, n) not in (None, "") and n not in allowed]
    if extra:
        raise HTTPException(status_code=400, detail=f"{', '.join(extra)} can't be used on a {body.scope} rule.")
    for n in allowed:
        v = getattr(body, n)
        if isinstance(v, str) and len(v) > 256:
            raise HTTPException(status_code=400, detail=f"{n} is too long")


def _rule_out(r: CatalogAccessRule) -> RuleOut:
    return RuleOut(**{k: getattr(r, k) for k in RuleOut.model_fields})


def _rule_text(r) -> str:
    cell = partial_cell_text(r.set_level, r.set_category)
    return f"{r.scope}: {r.name} -> {cell}"


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@router.get("/lattice")
def lattice_route(cfg: AppConfig = Depends(get_config)) -> Dict[str, Any]:
    lat = _lattice(cfg)
    out = lat.to_dict()
    out["grantable"] = [c.key for c in lat.grantable_categories]
    return out


@router.get("/scope")
def scope(db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal),
          cfg: AppConfig = Depends(get_config)) -> Dict[str, Any]:
    """What the caller can query — shown on the Ask page and the profile."""
    full = has_full_access(principal)
    out = _visibility(db, principal.grants.allows, full)
    if not principal.is_admin and cfg.catalog.auth.enabled:
        # Non-admins learn what THEY can query, not the size of the rest
        # of the estate.
        out["tables_total"] = None
        out["databases_total"] = None
        out["databases"] = [d for d in out.get("databases", []) if d.get("visible")]
    out.update({
        "full_access": full, "is_admin": principal.is_admin, "auth_enabled": cfg.catalog.auth.enabled,
        "cells": _principal_cells(principal), "roles": list(getattr(principal, "roles", []) or []),
    })
    return out


# ---------------------------------------------------------------------------
# Admin: grid / objects / explain
# ---------------------------------------------------------------------------

@router.get("/grid")
def grid(kind: Kind = "table", database: Optional[str] = None, db: Session = Depends(get_db),
         cfg: AppConfig = Depends(get_config), _: Principal = Depends(require_admin)) -> Dict[str, Any]:
    lat = _lattice(cfg)
    cells: Dict[Tuple[Any, Any], int] = {}
    level_sources: Dict[str, int] = {}
    category_sources: Dict[str, int] = {}
    if kind in ("database", "schema"):
        items = _container_items(db, cfg, lat, kind)
        if database:
            items = [i for i in items if i["database"] == database]
        for i in items:
            key = (i["level"], i["category"])
            cells[key] = cells.get(key, 0) + 1
            ls, cs = i["level_source"] or "none", i["category_source"] or "none"
            level_sources[ls] = level_sources.get(ls, 0) + 1
            category_sources[cs] = category_sources.get(cs, 0) + 1
        total, review = len(items), 0
    else:
        if kind == "table":
            model = CatalogEntity
            q = db.query(CatalogEntity.access_level, CatalogEntity.access_category, func.count(CatalogEntity.id))
            base = db.query(CatalogEntity)
            filt = [CatalogEntity.object_type.in_(_QUERYABLE)]
        else:
            model = CatalogField
            q = (db.query(CatalogField.access_level, CatalogField.access_category, func.count(CatalogField.id))
                 .join(CatalogEntity, CatalogEntity.id == CatalogField.entity_id))
            base = db.query(CatalogField).join(CatalogEntity, CatalogEntity.id == CatalogField.entity_id)
            filt = [CatalogEntity.object_type.in_(_QUERYABLE)]
        if database:
            q = q.join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
            base = base.join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
            filt.append(CatalogDatabase.database_name == database)
        for lv, cat, n in q.filter(*filt).group_by(model.access_level, model.access_category).all():
            cells[(lv, cat)] = n
        for src, n in (base.with_entities(model.access_level_source, func.count(model.id))
                       .filter(*filt).group_by(model.access_level_source).all()):
            level_sources[src or "none"] = n
        for src, n in (base.with_entities(model.access_category_source, func.count(model.id))
                       .filter(*filt).group_by(model.access_category_source).all()):
            category_sources[src or "none"] = n
        total = sum(cells.values())
        review = base.filter(*filt, model.access_review_pending.is_(True)).count()
    quarantine = sum(n for (lv, cat), n in cells.items() if lat.is_quarantine(cat))
    return {
        "kind": kind, "total": total, "quarantine": quarantine, "review_pending": review,
        "cells": [{"level": lv, "category": cat, "count": n} for (lv, cat), n in cells.items()],
        "level_sources": level_sources, "category_sources": category_sources,
    }


@router.get("/objects")
def objects(
    kind: Kind = "table", level: Optional[int] = None, category: Optional[str] = None,
    database: Optional[str] = None, q: Optional[str] = None, source: Optional[str] = None,
    review: Optional[bool] = None, table_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config), _: Principal = Depends(require_admin),
) -> Dict[str, Any]:
    lat = _lattice(cfg)
    cat = category.upper() if category else None
    if kind in ("database", "schema"):
        items = _container_items(db, cfg, lat, kind)
        if level is not None:
            items = [i for i in items if i["level"] == level]
        if cat:
            items = [i for i in items if i["category"] == cat]
        if database:
            items = [i for i in items if i["database"] == database]
        if q:
            items = [i for i in items if q.lower() in f"{i['database']} {i.get('schema') or ''}".lower()]
        if source:
            items = [i for i in items if source in (i["level_source"], i["category_source"])]
        items.sort(key=lambda i: (i["database"], i.get("schema") or ""))
        return {"total": len(items), "items": items[offset:offset + limit]}

    if kind == "table":
        base = _entity_names(db).filter(CatalogEntity.object_type.in_(_QUERYABLE))
        m = CatalogEntity
        if q:
            like = f"%{q}%"
            base = base.filter(or_(CatalogEntity.object_name.ilike(like), CatalogEntity.schema_name.ilike(like),
                                   CatalogEntity.semantic_name.ilike(like)))
    else:
        base = (db.query(CatalogField, CatalogEntity, CatalogDatabase.database_name, CatalogSource.name)
                .join(CatalogEntity, CatalogEntity.id == CatalogField.entity_id)
                .join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
                .join(CatalogSource, CatalogSource.id == CatalogDatabase.source_id)
                .filter(CatalogEntity.object_type.in_(_QUERYABLE)))
        m = CatalogField
        if table_id:
            base = base.filter(CatalogField.entity_id == table_id)
        if q:
            like = f"%{q}%"
            base = base.filter(or_(CatalogField.column_name.ilike(like), CatalogEntity.object_name.ilike(like),
                                   CatalogField.semantic_name.ilike(like)))
    if level is not None:
        base = base.filter(m.access_level == level)
    if cat:
        base = base.filter(m.access_category == cat)
    if database:
        base = base.filter(CatalogDatabase.database_name == database)
    if source:
        base = base.filter(or_(m.access_level_source == source, m.access_category_source == source))
    if review is not None:
        base = base.filter(m.access_review_pending.is_(review))
    total = base.count()
    if kind == "table":
        rows = base.order_by(CatalogDatabase.database_name, CatalogEntity.schema_name, CatalogEntity.object_name) \
                   .offset(offset).limit(limit).all()
        items = [_entity_item(e, dbn, src) for e, dbn, src in rows]
    else:
        rows = base.order_by(CatalogDatabase.database_name, CatalogEntity.object_name, CatalogField.ordinal_position) \
                   .offset(offset).limit(limit).all()
        items = [_field_item(f, e, dbn, src) for f, e, dbn, src in rows]
    return {"total": total, "items": items}


@router.get("/explain")
def explain(kind: Kind, id: Optional[UUID] = None, database_id: Optional[UUID] = None,
            schema_name: Optional[str] = None, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
            _: Principal = Depends(require_admin)) -> Dict[str, Any]:
    lat = _lattice(cfg)
    t = Target(kind=kind, id=id, database_id=database_id, schema_name=schema_name)
    key, dbid = _resolve_target(db, t)
    try:
        if kind == "database":
            res = labeling.resolve_one(db, cfg, lat, database_id=dbid)
        elif kind == "schema":
            res = labeling.resolve_one(db, cfg, lat, database_id=dbid, schema_name=schema_name)
        elif kind == "table":
            res = labeling.resolve_one(db, cfg, lat, database_id=dbid, entity_id=id)
        else:
            f = db.get(CatalogField, id)
            res = labeling.resolve_one(db, cfg, lat, database_id=dbid, entity_id=f.entity_id, field_id=id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    pin = _find_pin(db, key)
    out = labeling.explain_resolution(res)
    out["target"] = _target_text(db, t)
    out["pin"] = ({"level": pin.level, "category": pin.category, "reason": pin.reason, "set_by": pin.set_by,
                   "set_at": pin.set_at.isoformat() if pin.set_at else None} if pin else None)
    return out


# ---------------------------------------------------------------------------
# Admin: placements
# ---------------------------------------------------------------------------

@router.post("/placements")
def place(body: PlacementIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
          principal: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """Move objects into a cell — a manual placement that beats every
    automatic layer and survives re-scans until reset."""
    lat = _lattice(cfg)
    category = body.category.upper() if body.category else None
    _check_cell(lat, body.level, category)
    touched = set()
    for t in body.targets:
        key, dbid = _resolve_target(db, t)
        before = _current_cell(db, t, cfg, lat)
        pin = _find_pin(db, key)
        if pin is None:
            pin = CatalogAccessLabel(**key)
            db.add(pin)
        pin.level, pin.category = body.level, category
        pin.reason, pin.set_by = (body.reason or None), principal.username
        record_access_change(db, actor=principal.username, action="placement", target_type=t.kind,
                             target=_target_text(db, t), old_value=before,
                             new_value=partial_cell_text(body.level, category),
                             detail={"reason": body.reason})
        touched.add(dbid)
    db.commit()
    stats = _recompute(db, cfg, lat, touched, reason=f"placement by {principal.username}")
    return {"ok": True, "moved": len(body.targets), "stats": stats}


@router.post("/placements/reset")
def reset(body: ResetIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
          principal: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """Remove manual placements — the objects go back to automatic."""
    lat = _lattice(cfg)
    touched, removed = set(), 0
    for t in body.targets:
        key, dbid = _resolve_target(db, t)
        pin = _find_pin(db, key)
        if pin is None:
            continue
        record_access_change(db, actor=principal.username, action="placement_reset", target_type=t.kind,
                             target=_target_text(db, t),
                             old_value=partial_cell_text(pin.level, pin.category))
        db.delete(pin)
        removed += 1
        touched.add(dbid)
    db.commit()
    stats = _recompute(db, cfg, lat, touched, reason=f"reset by {principal.username}") if touched else None
    return {"ok": True, "reset": removed, "stats": stats}


# ---------------------------------------------------------------------------
# Admin: rules
# ---------------------------------------------------------------------------

@router.get("/rules", response_model=List[RuleOut])
def list_rules(db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> List[RuleOut]:
    rows = db.query(CatalogAccessRule).order_by(CatalogAccessRule.priority.desc(), CatalogAccessRule.name).all()
    return [_rule_out(r) for r in rows]


def _apply_rule_fields(r: CatalogAccessRule, body: RuleIn) -> None:
    for k in RuleIn.model_fields:
        v = getattr(body, k)
        if isinstance(v, str):
            v = v.strip() or None
        if k in ("set_category", "object_type", "classification", "sensitivity_flag") and v:
            v = v.upper()
        setattr(r, k, v)


@router.post("/rules", response_model=RuleOut)
def create_rule(body: RuleIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                principal: Principal = Depends(require_admin)) -> RuleOut:
    lat = _lattice(cfg)
    _validate_rule(lat, body)
    r = CatalogAccessRule(created_by=principal.username)
    _apply_rule_fields(r, body)
    db.add(r)
    record_access_change(db, actor=principal.username, action="rule_create", target_type="rule", target=r.name,
                         new_value=_rule_text(r))
    db.commit()
    db.refresh(r)
    _recompute(db, cfg, lat, None, reason=f"rule created by {principal.username}")
    return _rule_out(r)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: UUID, body: RuleIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                principal: Principal = Depends(require_admin)) -> RuleOut:
    lat = _lattice(cfg)
    r = db.get(CatalogAccessRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")
    _validate_rule(lat, body)
    old = _rule_text(r)
    _apply_rule_fields(r, body)
    record_access_change(db, actor=principal.username, action="rule_update", target_type="rule", target=r.name,
                         old_value=old, new_value=_rule_text(r))
    db.commit()
    db.refresh(r)
    _recompute(db, cfg, lat, None, reason=f"rule updated by {principal.username}")
    return _rule_out(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: UUID, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                principal: Principal = Depends(require_admin)) -> Dict[str, Any]:
    lat = _lattice(cfg)
    r = db.get(CatalogAccessRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")
    record_access_change(db, actor=principal.username, action="rule_delete", target_type="rule", target=r.name,
                         old_value=_rule_text(r))
    db.delete(r)
    db.commit()
    stats = _recompute(db, cfg, lat, None, reason=f"rule deleted by {principal.username}")
    return {"ok": True, "stats": stats}


@router.post("/rules/preview")
def preview_rule(body: PreviewIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                 _: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """How many tables/columns would change cell if this rule were saved
    (or removed). Writes nothing."""
    lat = _lattice(cfg)
    rules = [r for r in db.query(CatalogAccessRule).all() if not (body.rule_id and r.id == body.rule_id)]
    if body.rule is not None and not body.delete:
        _validate_rule(lat, body.rule)
        tmp = CatalogAccessRule(id=body.rule_id, created_at=datetime.utcnow())
        _apply_rule_fields(tmp, body.rule)
        rules.append(tmp)
    return labeling.preview(db, cfg, lat, rules)


# ---------------------------------------------------------------------------
# Admin: review
# ---------------------------------------------------------------------------

@router.get("/review")
def review_list(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                _: Principal = Depends(require_admin)) -> Dict[str, Any]:
    ents = (_entity_names(db).filter(CatalogEntity.access_review_pending.is_(True))
            .order_by(CatalogDatabase.database_name, CatalogEntity.object_name).limit(limit).all())
    flds = (db.query(CatalogField, CatalogEntity, CatalogDatabase.database_name, CatalogSource.name)
            .join(CatalogEntity, CatalogEntity.id == CatalogField.entity_id)
            .join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
            .join(CatalogSource, CatalogSource.id == CatalogDatabase.source_id)
            .filter(CatalogField.access_review_pending.is_(True))
            .order_by(CatalogDatabase.database_name, CatalogEntity.object_name, CatalogField.ordinal_position)
            .limit(limit).all())
    items = [_entity_item(e, d, s) for e, d, s in ents] + [_field_item(f, e, d, s) for f, e, d, s in flds]
    total = (db.query(CatalogEntity).filter(CatalogEntity.access_review_pending.is_(True)).count()
             + db.query(CatalogField).filter(CatalogField.access_review_pending.is_(True)).count())
    return {"total": total, "items": items}


@router.post("/review/accept")
def review_accept(body: ReviewIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                  principal: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """Adopt the AI's proposal: becomes a manual placement at the proposed
    cell (the axis the AI had no opinion on keeps its current value)."""
    lat = _lattice(cfg)
    touched = set()
    for t in body.targets:
        if t.kind not in ("table", "column"):
            raise HTTPException(status_code=400, detail="only tables and columns are reviewed")
        obj = db.get(CatalogEntity if t.kind == "table" else CatalogField, t.id)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"{t.kind} not found")
        level = obj.access_ai_level if obj.access_ai_level is not None else obj.access_level
        category = obj.access_ai_category or obj.access_category
        key, dbid = _resolve_target(db, t)
        pin = _find_pin(db, key) or CatalogAccessLabel(**key)
        db.add(pin)
        pin.level, pin.category, pin.set_by = level, category, principal.username
        pin.reason = f"accepted AI suggestion ({(obj.access_detail or {}).get('ai', {}).get('via') or 'sensitivity'})"
        record_access_change(db, actor=principal.username, action="review_accept", target_type=t.kind,
                             target=_target_text(db, t),
                             old_value=cell_text(obj.access_level, obj.access_category),
                             new_value=cell_text(level, category))
        touched.add(dbid)
    db.commit()
    stats = _recompute(db, cfg, lat, touched, reason=f"review accepted by {principal.username}")
    return {"ok": True, "stats": stats}


@router.post("/review/dismiss")
def review_dismiss(body: ReviewIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                   principal: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """Keep the current cell. Remembered until the AI's proposal changes."""
    for t in body.targets:
        if t.kind not in ("table", "column"):
            raise HTTPException(status_code=400, detail="only tables and columns are reviewed")
        obj = db.get(CatalogEntity if t.kind == "table" else CatalogField, t.id)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"{t.kind} not found")
        obj.access_review_dismissed = labeling.ai_token(obj.access_ai_level, obj.access_ai_category)
        obj.access_review_pending = False
        record_access_change(db, actor=principal.username, action="review_dismiss", target_type=t.kind,
                             target=_target_text(db, t),
                             old_value=cell_text(obj.access_level, obj.access_category),
                             detail={"ai": obj.access_review_dismissed})
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: simulate / recompute / audit
# ---------------------------------------------------------------------------

@router.get("/simulate")
def simulate(cells: str = "", db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
             _: Principal = Depends(require_admin)) -> Dict[str, Any]:
    """"View as" a set of cells — exactly what a user holding them could query."""
    lat = _lattice(cfg)
    pairs = set()
    try:
        parsed = parse_cell_list(cells)
    except CellError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for lv, cat in parsed:
        if not lat.is_valid_cell(lv, cat):
            raise HTTPException(status_code=400, detail=f"Unknown cell {cell_token(lv, cat)}.")
        pairs.add((lv, cat))
    out = _visibility(db, lambda lv, cat: (lv, (cat or "").upper()) in pairs, False)
    out["cells"] = cell_tokens(pairs)
    if pairs:
        tables = (_entity_names(db).filter(CatalogEntity.object_type.in_(_QUERYABLE), CatalogEntity.status != "STALE")
                  .filter(or_(*[and_(CatalogEntity.access_level == lv, CatalogEntity.access_category == cat)
                                for lv, cat in pairs]))
                  .order_by(CatalogDatabase.database_name, CatalogEntity.object_name).limit(200).all())
        out["sample_tables"] = [{"database": d, "table": f"{e.schema_name}.{e.object_name}",
                                 "cell": cell_token(e.access_level, e.access_category)} for e, d, _s in tables]
    else:
        out["sample_tables"] = []
    return out


@router.post("/recompute")
def recompute_all(db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                  principal: Principal = Depends(require_admin)) -> Dict[str, Any]:
    return _recompute(db, cfg, _lattice(cfg), None, reason=f"manual recompute by {principal.username}")


@router.get("/audit")
def audit(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
          _: Principal = Depends(require_admin)) -> List[Dict[str, Any]]:
    rows = db.query(AppAccessAudit).order_by(AppAccessAudit.created_at.desc()).limit(limit).all()
    return [{"id": str(r.id), "actor": r.actor, "action": r.action, "target_type": r.target_type, "target": r.target,
             "old_value": r.old_value, "new_value": r.new_value, "detail": r.detail or {},
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
