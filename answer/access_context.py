"""
Who is asking /v1/ask, and what they may see.

- catalog.auth disabled: unchanged behavior — the grants in the request
  body are used as sent (the Ask page's grant picker simulates a caller),
  and catalog.answer.require_grants: false still bypasses the check for
  testing.
- catalog.auth enabled:
    * a signed-in user (Authorization: Bearer <session token>, or the
      session cookie): their cells are resolved from THIS app's database;
      anything in the request body is ignored — a browser can never widen
      its own access. Admins see all data (catalog.access.admins_see_all_data).
    * a trusted server caller (another application that resolves its own
      users): may pass grants in the body only together with the
      shared secret in X-Service-Key (catalog.answer.service_key_env_var),
      and names its end user in X-On-Behalf-Of (audit, history, Create).
    * anyone else: 401.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, field
from typing import List, Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from answer.schemas import GrantIn
from shared.access.policy import elevate_admin, has_full_access
from shared.auth.login import LoginFailed, canonical_username, principal_from_token
from shared.auth.tokens import TokenError
from shared.config import AppConfig


@dataclass
class AskAccess:
    grants: List[GrantIn]
    bypass: bool                      # skip the (level, category) check entirely
    username: Optional[str] = None
    is_admin: bool = False
    roles: List[str] = field(default_factory=list)
    mode: str = "open"                # open | user | service
    can_view_sql: bool = False        # gets the generated SQL back ("Queries run")

    @property
    def pairs(self):
        return {(g.level, g.category.upper()) for g in self.grants}


def _token(request: Request, cfg: AppConfig) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(cfg.catalog.auth.cookie_name)


def resolve_ask_access(request: Request, db: Session, cfg: AppConfig, body_grants: List[GrantIn]) -> AskAccess:
    if not cfg.catalog.auth.enabled:
        # Sign-in off = a testing/single-user setup: everything is visible.
        return AskAccess(grants=list(body_grants), bypass=not cfg.catalog.answer.require_grants, mode="open",
                         can_view_sql=True)

    token = _token(request, cfg)
    if token:
        try:
            principal = principal_from_token(db, cfg.catalog.auth, token)
        except (LoginFailed, TokenError) as exc:
            raise HTTPException(status_code=401, detail="Your session is no longer valid — sign in again.") from exc
        principal = elevate_admin(principal, cfg.catalog.access)
        grants = [GrantIn(level=g.level, category=g.category) for g in principal.grants.pairs]
        return AskAccess(grants=grants, bypass=has_full_access(principal), username=principal.username,
                         is_admin=principal.is_admin, roles=list(principal.roles), mode="user",
                         can_view_sql=principal.is_admin or principal.can_view_sql)

    expected = os.environ.get(cfg.catalog.answer.service_key_env_var or "", "")
    supplied = request.headers.get("x-service-key") or ""
    if expected and supplied and hmac.compare_digest(expected, supplied):
        # The server caller says which of ITS users is asking (only
        # trusted together with the service key): the question audit, the
        # person's history and Create ownership use it.
        on_behalf = (request.headers.get("x-on-behalf-of") or "").strip()
        return AskAccess(grants=list(body_grants), bypass=False, mode="service",
                         username=canonical_username(on_behalf)[:256] if on_behalf else None)
    raise HTTPException(status_code=401, detail="Not authenticated.")


def optional_ask_access(request: Request, db: Session, cfg: AppConfig) -> AskAccess:
    """For the read-only helper routes (suggestions, recent): same
    resolution, but with no body grants."""
    return resolve_ask_access(request, db, cfg, [])
