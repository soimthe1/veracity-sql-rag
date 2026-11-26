"""
Principal-side policy helpers, shared by the api/ and answer/ processes so
both apply exactly the same rules:

- exact-match (level, category), no inheritance (shared/auth/grants.py)
- an admin sees ALL data when catalog.access.admins_see_all_data is on
- a column is visible only if its own cell AND its table's cell are held
"""
from __future__ import annotations

import dataclasses
from typing import Optional

from shared.auth.grants import UnrestrictedGrantSet
from shared.models import AppAccessAudit


def has_full_access(principal) -> bool:
    return isinstance(principal.grants, UnrestrictedGrantSet)


def elevate_admin(principal, access_cfg):
    """Admins see everything: swap in an unrestricted grant set, keeping the
    role cells themselves for display."""
    if principal.is_admin and access_cfg.admins_see_all_data and not has_full_access(principal):
        return dataclasses.replace(principal, grants=UnrestrictedGrantSet(pairs=principal.grants.pairs))
    return principal


def cell_allowed(principal, level: Optional[int], category: Optional[str]) -> bool:
    if has_full_access(principal):
        return True
    if level is None or not category:
        return False
    return principal.grants.allows(level, category)


def field_allowed(principal, entity, fld) -> bool:
    if has_full_access(principal):
        return True
    lvl = fld.access_level if fld.access_level is not None else entity.access_level
    cat = fld.access_category or entity.access_category
    return cell_allowed(principal, entity.access_level, entity.access_category) and cell_allowed(principal, lvl, cat)


def record_access_change(session, *, actor: Optional[str], action: str, target_type: Optional[str] = None,
                         target: Optional[str] = None, old_value: Optional[str] = None,
                         new_value: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """Adds (does not commit) one app_access_audit row."""
    session.add(AppAccessAudit(actor=actor, action=action, target_type=target_type, target=target,
                               old_value=old_value, new_value=new_value, detail=detail or {}))
