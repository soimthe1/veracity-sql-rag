"""
The login flow. Four steps, in this exact order:

  1. AD proves identity — shared.auth.ad_auth.authenticate(). A failed
     bind is indistinguishable, from the caller's perspective, from
     every other failure mode below (anti-enumeration).
  2. NO self-provisioning: an AD-authenticated user with no matching,
     active AppUser row (or with zero roles) is refused with the SAME
     generic message as a wrong password. An administrator must create
     the user row and assign at least one role first — login() WRITES
     NOTHING to app_users except last_login_at on a successful login.
  3. Roles -> grants are resolved from THIS project's OWN Postgres,
     fresh, on every login (and, via principal_from_token(), on every
     subsequent authenticated request — see api/access.py) — never
     cached inside the session token itself.
  4. A session token carrying identity only is issued (tokens.py).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Set, Tuple

from sqlalchemy.orm import Session

import secrets

from shared.auth import tokens
from shared.auth.ad_auth import authenticate as ad_authenticate
from shared.auth.passwords import check_strength, hash_password, verify_password
from shared.auth.grants import GrantSet
from shared.config import AuthConfig
from shared.models import AppAccessAudit, AppLoginAudit, AppRole, AppUser, AppUserRole, CatalogSetting

logger = logging.getLogger("shared.auth.login")

_GENERIC = "Invalid username or password, or your account is not registered for this application."


class LoginFailed(Exception):
    def __init__(self, message: str = _GENERIC):
        super().__init__(message)


@dataclass
class Principal:
    user_id: str
    username: str
    display_name: str
    is_admin: bool
    grants: GrantSet
    roles: List[str] = field(default_factory=list)
    # sees the SQL generated for their answers (a role flag; admins always)
    can_view_sql: bool = False


def canonical_username(raw: str) -> str:
    """Folds DOMAIN\\user / user@domain / MixedCase down to one lowercase
    key (AD's sAMAccountName is case-insensitive; this app's Postgres
    text key is not) so the same AD account can never create a second
    shadow row here."""
    name = raw.strip()
    if "\\" in name:
        name = name.split("\\", 1)[1]
    if "@" in name:
        name = name.split("@", 1)[0]
    return name.lower()


def resolve_principal(user: AppUser) -> Principal:
    """Roles -> effective grants, resolved fresh from the ORM relationship
    graph already loaded on `user`. Roles combine by UNION (knowledge
    platform rule) and is_admin is true if ANY held role is an admin role —
    orthogonal to read clearance, never itself a grant."""
    roles = [link.role for link in user.roles]
    is_admin = any(r.is_admin for r in roles)
    pairs = {(g.level, g.category) for r in roles for g in r.grants}
    return Principal(
        user_id=str(user.id),
        username=user.username,
        display_name=user.display_name or user.username,
        is_admin=is_admin,
        grants=GrantSet.from_pairs(pairs),
        roles=sorted(r.name for r in roles),
        can_view_sql=is_admin or any(getattr(r, "can_view_sql", False) for r in roles),
    )


def _audit(session: Session, username_attempted: str, success: bool, reason: str) -> None:
    try:
        session.add(AppLoginAudit(username_attempted=username_attempted, success=success, reason=reason))
        session.commit()
    except Exception:  # noqa: BLE001 - the audit trail must never block a login outcome either way
        logger.exception("Failed to write login audit row (login itself proceeds).")
        session.rollback()


def bootstrap_admin_names(cfg: AuthConfig) -> Set[str]:
    return {canonical_username(n) for n in re.split(r"[,;\s]+", cfg.bootstrap_admins or "") if n.strip()}


def has_active_admin(session: Session) -> bool:
    return (session.query(AppUser.id).join(AppUserRole, AppUserRole.user_id == AppUser.id)
            .join(AppRole, AppRole.id == AppUserRole.role_id)
            .filter(AppUser.is_active.is_(True), AppRole.is_admin.is_(True)).first()) is not None


def ensure_admin_role(session: Session) -> AppRole:
    """The built-in Administrator role (seeded by migration 0007) — created
    here too if someone deleted the row by hand."""
    role = session.query(AppRole).filter(AppRole.is_system.is_(True)).order_by(AppRole.created_at).first()
    if role is None:
        role = session.query(AppRole).filter(AppRole.name == "Administrator").one_or_none()
    if role is None:
        role = AppRole(name="Administrator", description="Built-in. Members manage the whole application and see all data.",
                       is_admin=True, is_system=True)
        session.add(role)
        session.flush()
    role.is_admin = True
    role.is_system = True
    return role


def _bootstrap(session: Session, canon: str, user) -> AppUser:
    """First-time setup (catalog.auth.bootstrap_admins): register an
    AD-authenticated, listed account and make it an Administrator."""
    role = ensure_admin_role(session)
    if user is None:
        user = AppUser(username=canon, display_name=canon, is_active=True)
        session.add(user)
        session.flush()
    session.add(AppUserRole(user_id=user.id, role_id=role.id))
    session.add(AppAccessAudit(actor=canon, action="bootstrap_admin", target_type="user", target=canon,
                               new_value=role.name, detail={"via": "catalog.auth.bootstrap_admins"}))
    session.commit()
    session.refresh(user)
    logger.warning("Bootstrap: registered %s as the first administrator (listed in catalog.auth.bootstrap_admins). "
                   "Bootstrap is now off while an active administrator exists; clear CATALOG_BOOTSTRAP_ADMINS.", canon)
    return user


def login(session: Session, cfg: AuthConfig, username: str, password: str) -> Tuple[str, Principal]:
    canon = canonical_username(username)

    if cfg.mode == "local":
        user = session.query(AppUser).filter(AppUser.username == canon).one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            _audit(session, canon, False, "wrong password or unknown account")
            raise LoginFailed()
    else:
        if not ad_authenticate(cfg.ldap, canon, password):
            _audit(session, canon, False, "AD bind failed")
            raise LoginFailed()
        user = session.query(AppUser).filter(AppUser.username == canon).one_or_none()
    if cfg.mode == "ldap" and (user is None or (user.is_active and not user.roles)) and canon in bootstrap_admin_names(cfg) \
            and not has_active_admin(session):
        # Only while the app has NO active administrator: a first-time
        # setup, never a standing way back in (an account whose roles were
        # removed is not re-promoted). Afterwards admins add people in
        # Security > People, and CATALOG_BOOTSTRAP_ADMINS can be cleared.
        user = _bootstrap(session, canon, user)
    if user is None or not user.is_active:
        # AD proved identity but this app has no (active) registration
        # for them — refused with the SAME generic message as a bad
        # password, deliberately, so this endpoint never discloses which
        # AD accounts are or aren't registered here.
        _audit(session, canon, False, "not registered or inactive")
        raise LoginFailed()

    if not user.roles:
        _audit(session, canon, False, "registered but has no roles assigned")
        raise LoginFailed()

    user.last_login_at = datetime.now(timezone.utc)
    session.commit()

    principal = resolve_principal(user)
    token = tokens.issue(cfg, principal.user_id, principal.username, principal.display_name)
    _audit(session, canon, True, "ok")
    return token, principal


def principal_from_token(session: Session, cfg: AuthConfig, token: str) -> Principal:
    claims = tokens.verify(cfg, token)
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise LoginFailed("Session is no longer valid.") from exc
    user = session.get(AppUser, user_id)
    if user is None or not user.is_active or not user.roles:
        raise LoginFailed("Session is no longer valid.")
    return resolve_principal(user)


# ---- first-time setup (catalog.auth.mode: local) -------------------------

_SETUP_CODE_KEY = "auth.setup_code"


def setup_needed(session: Session, cfg: AuthConfig) -> bool:
    """Local mode with no active administrator yet: the web UI shows the
    one-time "create administrator" screen instead of the sign-in form."""
    return cfg.mode == "local" and not has_active_admin(session)


def ensure_setup_code(session: Session) -> str:
    """The code that unlocks the setup screen. Kept in catalog_settings so
    every API process and restart shows the same one until it is used;
    printed to the API log by api/main.py, so only someone who can read
    the server's logs can claim the first administrator account."""
    row = session.get(CatalogSetting, _SETUP_CODE_KEY)
    if row is None:
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        row = CatalogSetting(key=_SETUP_CODE_KEY, value=code)
        session.add(row)
        session.commit()
    return str(row.value)


def complete_setup(session: Session, cfg: AuthConfig, code: str, username: str, password: str,
                   display_name: str = "") -> Tuple[str, Principal]:
    """Creates the first administrator and signs them in. Refused once any
    active administrator exists, or with a wrong code."""
    if not setup_needed(session, cfg):
        raise LoginFailed("Setup is already complete.")
    row = session.get(CatalogSetting, _SETUP_CODE_KEY)
    if row is None or not secrets.compare_digest(str(row.value), (code or "").strip().upper()):
        _audit(session, canonical_username(username or "?"), False, "setup: wrong code")
        raise LoginFailed("The setup code is not right. It is printed in the API log.")
    canon = canonical_username(username)
    if not canon:
        raise LoginFailed("Choose a username.")
    try:
        check_strength(password)
    except ValueError as exc:
        raise LoginFailed(str(exc)) from exc
    user = session.query(AppUser).filter(AppUser.username == canon).one_or_none()
    if user is None:
        user = AppUser(username=canon, display_name=display_name or canon, is_active=True)
        session.add(user)
        session.flush()
    user.password_hash = hash_password(password)
    user.is_active = True
    session.add(AppUserRole(user_id=user.id, role_id=ensure_admin_role(session).id))
    session.add(AppAccessAudit(actor=canon, action="bootstrap_admin", target_type="user", target=canon,
                               new_value="Administrator", detail={"via": "setup screen"}))
    session.delete(row)
    session.commit()
    logger.warning("Setup complete: %s is the first administrator.", canon)
    return login(session, cfg, canon, password)


def change_password(session: Session, user_id: str, current: str, new: str) -> None:
    user = session.get(AppUser, uuid.UUID(user_id))
    if user is None or not verify_password(current, user.password_hash):
        raise LoginFailed("The current password is not right.")
    try:
        check_strength(new)
    except ValueError as exc:
        raise LoginFailed(str(exc)) from exc
    user.password_hash = hash_password(new)
    session.commit()
