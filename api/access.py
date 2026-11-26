"""
Resolves the current principal for an API request, and centralizes the
(level, category) filtering rule applied to catalog entity/search/
resolve endpoints when catalog.auth.enabled is true. Builds on
shared/auth/ (the login mechanics) — this module is the api/ process's
own use of it, kept separate from shared/auth/ itself because it's
FastAPI-specific (Depends, HTTPException, cookies/headers) while
shared/auth/ has no web-framework dependency and is reused as-is by
anything else that wants to resolve a token (in this project, only
api/ does today).

THE single most important behavior here: when catalog.auth is disabled
(the default), get_current_principal() returns an UNRESTRICTED
principal and every filter helper below becomes a no-op — a catalog
running with auth off behaves EXACTLY as it did before this module
existed. See DECISIONS.md.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import false, tuple_
from sqlalchemy.orm import Query, Session

from api.deps import get_config, get_db
from shared.access.policy import elevate_admin
from shared.auth.grants import UnrestrictedGrantSet
from shared.auth.login import LoginFailed, Principal, principal_from_token
from shared.auth.tokens import TokenError
from shared.config import AppConfig
from shared.models import CatalogEntity

UNRESTRICTED = Principal(
    user_id="unrestricted",
    username="unrestricted",
    display_name="Authentication disabled",
    is_admin=True,
    grants=UnrestrictedGrantSet(pairs=frozenset()),
)


def _extract_token(request: Request, cfg: AppConfig) -> Optional[str]:
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return request.cookies.get(cfg.catalog.auth.cookie_name)


def get_current_principal(
    request: Request,
    db: Session = Depends(get_db),
    cfg: AppConfig = Depends(get_config),
) -> Principal:
    if not cfg.catalog.auth.enabled:
        return UNRESTRICTED
    token = _extract_token(request, cfg)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        principal = principal_from_token(db, cfg.catalog.auth, token)
    except (LoginFailed, TokenError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    # Admins see all data (catalog.access.admins_see_all_data) — their
    # grant set becomes unrestricted, so every filter below is a no-op.
    return elevate_admin(principal, cfg.catalog.access)


def require_admin(principal: Principal = Depends(get_current_principal)) -> Principal:
    """Whoever holds an admin role (the built-in Administrator, or any role
    with the admin flag) manages the application: users, roles, the
    permission matrix, data bucketing and the knobs."""
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="This action requires an administrator role.")
    return principal


def filter_entities_query(query: Query, principal: Principal) -> Query:
    """Apply the (level, category) exact-match filter to a CatalogEntity
    query — a no-op when the principal is unrestricted (auth disabled).
    Mirrors answer/grounding.py's own filtering rule exactly (same
    columns, same exact-match, no inheritance), so a question /v1/ask
    can answer and a browse through /v1/entities agree about what's
    visible to the same principal."""
    if isinstance(principal.grants, UnrestrictedGrantSet):
        return query
    pairs = [(g.level, g.category) for g in principal.grants.pairs]
    if not pairs:
        return query.filter(false())  # default-deny: no grants, nothing visible
    return query.filter(tuple_(CatalogEntity.access_level, CatalogEntity.access_category).in_(pairs))


def entity_visible(entity: Optional[CatalogEntity], principal: Principal) -> bool:
    if entity is None:
        return False
    if isinstance(principal.grants, UnrestrictedGrantSet):
        return True
    return principal.grants.allows(entity.access_level or 0, entity.access_category or "")


# ---------------------------------------------------------------------------
# Screens: who may open which part of the app
# ---------------------------------------------------------------------------

# Every screen of the web UI, in navigation order. "access" and "security"
# are administration and never open to non-admins.
USER_SCREENS = ("search", "ask", "create")
CATALOG_SCREENS = ("overview", "sources", "entities", "relationships", "glossary", "suggestions", "integrity", "health")
ADMIN_SCREENS = ("access", "security")
ALL_SCREENS = USER_SCREENS + CATALOG_SCREENS + ADMIN_SCREENS
ALWAYS_OPEN = ("ask",)


def allowed_screens(principal: Principal, cfg: AppConfig) -> List[str]:
    """The screens this principal may open. Admins (and everyone while
    sign-in is off) get all of them; anyone else gets Ask plus whatever
    catalog.access.non_admin_screens opens up."""
    if principal.is_admin or not cfg.catalog.auth.enabled:
        return list(ALL_SCREENS)
    listed = {str(s).strip().lower() for s in (cfg.catalog.access.non_admin_screens or [])}
    return [s for s in ALL_SCREENS if s not in ADMIN_SCREENS and (s in listed or s in ALWAYS_OPEN)]


def require_screen(*screens: str) -> Callable[..., Principal]:
    """Router dependency: the caller must be allowed to open at least one of
    `screens` (see allowed_screens). Non-admins get a 403 for anything
    outside their screens, so hiding a menu item is never the only gate."""

    def dependency(principal: Principal = Depends(get_current_principal),
                   cfg: AppConfig = Depends(get_config)) -> Principal:
        if principal.is_admin:
            return principal
        if set(screens) & set(allowed_screens(principal, cfg)):
            return principal
        raise HTTPException(status_code=403, detail="This part of the application is available to administrators only.")

    return dependency
