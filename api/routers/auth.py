"""
Login + this project's own (level, category) permission-matrix admin —
POST /v1/auth/login, GET /v1/auth/me, GET /v1/auth/matrix (public shape
for the Security tab), and admin-only user/role CRUD. All inert-by-
construction when catalog.auth.enabled is false: /login refuses with a
plain 400 ("no login required"), and every other route here resolves
its principal through api.access.get_current_principal, which returns
an unrestricted superuser principal in that state — so an admin route
called with auth disabled just... works, same as everything else in
that mode. See DECISIONS.md.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from api.access import allowed_screens, get_current_principal, require_admin
from api.deps import get_config, get_db
from shared.access.cells import cell_token, cell_tokens, coerce_grant
from shared.access.lattice import load_lattice
from shared.access.policy import has_full_access, record_access_change
from shared.auth import directory
from shared.auth.login import LoginFailed, Principal, canonical_username, change_password, complete_setup, setup_needed
from shared.auth.login import login as perform_login
from shared.auth.passwords import check_strength, hash_password
from shared.config import AppConfig
from shared.models import AppLoginAudit, AppRole, AppRoleGrant, AppUser, AppUserRole

router = APIRouter(prefix="/v1/auth", tags=["auth"])


# --------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class GrantOut(BaseModel):
    level: int
    category: str


class PrincipalOut(BaseModel):
    auth_enabled: bool
    auth_mode: str = "local"
    user_id: Optional[str] = None
    username: Optional[str] = None
    display_name: Optional[str] = None
    is_admin: bool = False
    grants: List[GrantOut] = Field(default_factory=list)
    # Additive: role names, and whether this principal sees ALL data
    # (auth disabled, or an admin while catalog.access.admins_see_all_data).
    roles: List[str] = Field(default_factory=list)
    full_access: bool = False
    # Screens this person may open (catalog.access.non_admin_screens).
    screens: List[str] = Field(default_factory=list)
    can_view_sql: bool = False


class LoginOut(BaseModel):
    token: str
    principal: PrincipalOut


class RoleGrantIn(BaseModel):
    level: int
    category: str

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value):
        return coerce_grant(value)


class RoleIn(BaseModel):
    name: str
    description: Optional[str] = None
    is_admin: bool = False
    grants: List[RoleGrantIn] = Field(default_factory=list)
    # Members see the SQL generated for their answers.
    can_view_sql: bool = False


class RoleOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    is_admin: bool
    grants: List[GrantOut]
    user_count: int
    is_system: bool = False
    can_view_sql: bool = False


class UserIn(BaseModel):
    username: str
    display_name: Optional[str] = None
    # Local sign-in only: required when creating a person, optional on
    # update (set = an administrator resets that person's password).
    password: Optional[str] = None
    is_active: bool = True
    role_ids: List[UUID] = Field(default_factory=list)


class UserOut(BaseModel):
    id: UUID
    username: str
    display_name: Optional[str]
    is_active: bool
    roles: List[str]
    is_admin: bool
    last_login_at: Optional[str]
    role_ids: List[UUID] = Field(default_factory=list)
    cells: List[GrantOut] = Field(default_factory=list)  # union of the roles' grant cells


class DirectoryPersonOut(BaseModel):
    username: str
    display_name: str
    email: Optional[str] = None
    department: Optional[str] = None
    title: Optional[str] = None
    registered: bool = False


class DirectorySearchOut(BaseModel):
    configured: bool
    results: List[DirectoryPersonOut] = Field(default_factory=list)


class LoginAuditOut(BaseModel):
    id: UUID
    username_attempted: str
    success: bool
    reason: Optional[str]
    created_at: Optional[str]


# --------------------------------------------------------------------
# Session
# --------------------------------------------------------------------

def _principal_out(cfg: AppConfig, principal: Principal) -> PrincipalOut:
    return PrincipalOut(
        auth_enabled=cfg.catalog.auth.enabled,
        auth_mode=cfg.catalog.auth.mode,
        user_id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        is_admin=principal.is_admin,
        grants=[GrantOut(**g) for g in principal.grants.as_level_category_list()],
        roles=list(getattr(principal, "roles", []) or []),
        full_access=has_full_access(principal),
        screens=allowed_screens(principal, cfg),
        can_view_sql=bool(principal.is_admin or getattr(principal, "can_view_sql", False)),
    )


@router.post("/login", response_model=LoginOut)
def login_route(
    body: LoginIn, response: Response, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)
) -> LoginOut:
    if not cfg.catalog.auth.enabled:
        raise HTTPException(
            status_code=400,
            detail="Authentication is disabled (catalog.auth.enabled: false) — no login is required.",
        )
    try:
        token, principal = perform_login(db, cfg.catalog.auth, body.username, body.password)
    except LoginFailed as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    from shared.access.policy import elevate_admin
    principal = elevate_admin(principal, cfg.catalog.access)

    response.set_cookie(
        cfg.catalog.auth.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=cfg.catalog.auth.cookie_secure,
        max_age=cfg.catalog.auth.expiry_minutes * 60,
    )
    return LoginOut(token=token, principal=_principal_out(cfg, principal))


class SetupIn(BaseModel):
    code: str
    username: str
    password: str
    display_name: Optional[str] = None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


def _set_session_cookie(response: Response, cfg: AppConfig, token: str) -> None:
    response.set_cookie(cfg.catalog.auth.cookie_name, token, httponly=True, samesite="lax",
                        secure=cfg.catalog.auth.cookie_secure, max_age=cfg.catalog.auth.expiry_minutes * 60)


@router.get("/setup")
def setup_status(db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config)) -> dict:
    """Whether the one-time "create the first administrator" screen applies."""
    return {"mode": cfg.catalog.auth.mode, "needed": setup_needed(db, cfg.catalog.auth)}


@router.post("/setup", response_model=LoginOut)
def setup_route(body: SetupIn, response: Response, db: Session = Depends(get_db),
                cfg: AppConfig = Depends(get_config)) -> LoginOut:
    try:
        token, principal = complete_setup(db, cfg.catalog.auth, body.code, body.username, body.password,
                                          body.display_name or "")
    except LoginFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from shared.access.policy import elevate_admin
    principal = elevate_admin(principal, cfg.catalog.access)
    _set_session_cookie(response, cfg, token)
    return LoginOut(token=token, principal=_principal_out(cfg, principal))


@router.post("/me/password")
def change_own_password(body: PasswordChangeIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                        principal: Principal = Depends(get_current_principal)) -> dict:
    if cfg.catalog.auth.mode != "local":
        raise HTTPException(status_code=400, detail="Passwords are managed by the directory (LDAP), not here.")
    try:
        change_password(db, principal.user_id, body.current_password, body.new_password)
    except LoginFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


def _apply_password(cfg: AppConfig, user: AppUser, password: Optional[str], required: bool) -> None:
    if cfg.catalog.auth.mode != "local":
        return
    if not password:
        if required:
            raise HTTPException(status_code=400, detail="Set a password for this person.")
        return
    try:
        check_strength(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user.password_hash = hash_password(password)


@router.post("/logout")
def logout_route(response: Response, cfg: AppConfig = Depends(get_config)) -> dict:
    response.delete_cookie(cfg.catalog.auth.cookie_name, httponly=True, samesite="lax",
                           secure=cfg.catalog.auth.cookie_secure)
    return {"ok": True}


@router.get("/me", response_model=PrincipalOut)
def me_route(principal: Principal = Depends(get_current_principal), cfg: AppConfig = Depends(get_config)) -> PrincipalOut:
    return _principal_out(cfg, principal)


@router.get("/matrix")
def matrix_route(cfg: AppConfig = Depends(get_config)) -> dict:
    """Public (no auth required, even when auth is enabled) — display
    metadata only (level/category names, colours, cell labels), never a
    grant itself. Read from the lattice file (catalog.access.lattice_file,
    config/levels.yaml format).

    `categories` keeps its original meaning (enabled categories);
    `grantable` excludes the reserved quarantine category, which no role
    can ever be granted."""
    lat = load_lattice(cfg)
    return {
        "levels": [lv.__dict__ for lv in lat.levels],
        "categories": [c.__dict__ for c in lat.enabled_categories],
        "all_categories": [c.__dict__ for c in lat.categories],
        "grantable": [c.key for c in lat.grantable_categories],
        "default_level": lat.default_level,
        "default_category": lat.default_category,
        "cell_labels": lat.cell_labels,
    }


def _validate_grants(cfg: AppConfig, grants: List[RoleGrantIn]) -> None:
    lat = load_lattice(cfg)
    bad = [cell_token(g.level, g.category) for g in grants if not lat.is_valid_cell(g.level, g.category, grantable=True)]
    if bad:
        raise HTTPException(status_code=400, detail=f"Not grantable cell(s): {', '.join(bad)} — unknown level, "
                                                    "disabled category, or the reserved quarantine category.")


def _active_admin_count(db: Session) -> int:
    return (
        db.query(AppUser.id).join(AppUserRole, AppUserRole.user_id == AppUser.id)
        .join(AppRole, AppRole.id == AppUserRole.role_id)
        .filter(AppUser.is_active.is_(True), AppRole.is_admin.is_(True)).distinct().count()
    )


def _guard_last_admin(db: Session, before: int) -> None:
    """Refuses a change that would leave no active administrator (checked
    after flush, before commit)."""
    db.flush()
    if before > 0 and _active_admin_count(db) == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="That would leave no active administrator. Make someone else an "
                                                    "administrator first.")


def _cells_text(grants) -> str:
    return " ".join(cell_tokens((g.level, g.category) for g in grants)) or "(none)"


@router.get("/directory", response_model=DirectorySearchOut)
def directory_search(q: str = "", db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                     _: Principal = Depends(require_admin)) -> DirectorySearchOut:
    """Look people up in AD (service account — see LdapConfig.search_*)."""
    if not directory.configured(cfg.catalog.auth.ldap):
        return DirectorySearchOut(configured=False)
    try:
        people = directory.search(cfg.catalog.auth.ldap, q)
    except Exception as exc:  # noqa: BLE001 - AD trouble must not 500 the Security page
        raise HTTPException(status_code=502, detail=f"Directory search failed: {exc}") from exc
    registered = {u for (u,) in db.query(AppUser.username).filter(
        AppUser.username.in_([p.username for p in people])).all()} if people else set()
    return DirectorySearchOut(configured=True, results=[
        DirectoryPersonOut(**p.__dict__, registered=p.username in registered) for p in people
    ])


# --------------------------------------------------------------------
# Admin: roles
# --------------------------------------------------------------------

def _role_out(role: AppRole) -> RoleOut:
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        is_admin=role.is_admin,
        grants=[GrantOut(level=g.level, category=g.category) for g in role.grants],
        user_count=len(role.user_links),
        is_system=bool(role.is_system),
        can_view_sql=bool(role.can_view_sql) or bool(role.is_admin),
    )


@router.get("/roles", response_model=List[RoleOut])
def list_roles(db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> List[RoleOut]:
    return [_role_out(r) for r in db.query(AppRole).order_by(AppRole.name).all()]


@router.post("/roles", response_model=RoleOut)
def create_role(body: RoleIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                principal: Principal = Depends(require_admin)) -> RoleOut:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="A role needs a name.")
    if db.query(AppRole).filter(AppRole.name == body.name).first():
        raise HTTPException(status_code=409, detail="A role with this name already exists.")
    _validate_grants(cfg, body.grants)
    role = AppRole(name=body.name.strip(), description=body.description, is_admin=body.is_admin,
                   can_view_sql=body.can_view_sql)
    role.grants = [AppRoleGrant(level=g.level, category=g.category.upper()) for g in body.grants]
    db.add(role)
    record_access_change(db, actor=principal.username, action="role_create", target_type="role", target=role.name,
                         new_value=_cells_text(role.grants),
                         detail={"is_admin": body.is_admin, "can_view_sql": body.can_view_sql})
    db.commit()
    db.refresh(role)
    return _role_out(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: UUID, body: RoleIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                principal: Principal = Depends(require_admin)) -> RoleOut:
    role = db.get(AppRole, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    if db.query(AppRole).filter(AppRole.name == body.name, AppRole.id != role_id).first():
        raise HTTPException(status_code=409, detail="A role with this name already exists.")
    if role.is_system and (not body.is_admin or body.name != role.name):
        raise HTTPException(status_code=403, detail="The built-in Administrator role can't be renamed or lose its "
                                                    "administrator flag.")
    _validate_grants(cfg, body.grants)
    before = _active_admin_count(db)
    old_cells = _cells_text(role.grants)
    role.name = body.name
    role.description = body.description
    role.is_admin = body.is_admin
    role.can_view_sql = body.can_view_sql
    # Keep the grant rows that stay (re-creating them would insert before
    # the old rows are deleted and hit the one-row-per-cell constraint).
    existing = {(g.level, g.category.upper()): g for g in role.grants}
    wanted = sorted({(g.level, g.category.upper()) for g in body.grants})
    role.grants = [existing.get(k) or AppRoleGrant(level=k[0], category=k[1]) for k in wanted]
    _guard_last_admin(db, before)
    new_cells = _cells_text(role.grants)
    record_access_change(db, actor=principal.username, action="role_update", target_type="role", target=role.name,
                         old_value=old_cells, new_value=new_cells,
                         detail={"is_admin": body.is_admin, "can_view_sql": body.can_view_sql})
    db.commit()
    db.refresh(role)
    return _role_out(role)


@router.delete("/roles/{role_id}")
def delete_role(role_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)) -> dict:
    role = db.get(AppRole, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="The built-in Administrator role can't be deleted.")
    before = _active_admin_count(db)
    name, cells = role.name, _cells_text(role.grants)
    db.delete(role)
    _guard_last_admin(db, before)
    record_access_change(db, actor=principal.username, action="role_delete", target_type="role", target=name,
                         old_value=cells)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------
# Admin: users
# --------------------------------------------------------------------

def _user_out(user: AppUser) -> UserOut:
    roles = [link.role for link in user.roles]
    cells = sorted({(g.level, g.category) for r in roles for g in r.grants})
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=[r.name for r in roles],
        is_admin=any(r.is_admin for r in roles),
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        role_ids=[r.id for r in roles],
        cells=[GrantOut(level=lv, category=cat) for lv, cat in cells],
    )


def _role_names(db: Session, role_ids) -> str:
    if not role_ids:
        return "(none)"
    return ", ".join(sorted(n for (n,) in db.query(AppRole.name).filter(AppRole.id.in_(list(role_ids))).all()))


def _validate_role_ids(db: Session, role_ids: List[UUID]) -> None:
    if not role_ids:
        return
    found = {r.id for r in db.query(AppRole.id).filter(AppRole.id.in_(role_ids)).all()}
    missing = set(role_ids) - found
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown role id(s): {', '.join(str(m) for m in missing)}")


@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> List[UserOut]:
    return [_user_out(u) for u in db.query(AppUser).order_by(AppUser.username).all()]


@router.post("/users", response_model=UserOut)
def create_user(body: UserIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
                principal: Principal = Depends(require_admin)) -> UserOut:
    canon = canonical_username(body.username)
    if not canon:
        raise HTTPException(status_code=400, detail="An account name is required.")
    if db.query(AppUser).filter(AppUser.username == canon).first():
        raise HTTPException(status_code=409, detail="A user with this (canonical) username already exists.")
    _validate_role_ids(db, body.role_ids)

    user = AppUser(username=canon, display_name=body.display_name or body.username, is_active=body.is_active)
    _apply_password(cfg, user, body.password, required=True)
    db.add(user)
    db.flush()
    for role_id in body.role_ids:
        db.add(AppUserRole(user_id=user.id, role_id=role_id))
    record_access_change(db, actor=principal.username, action="user_create", target_type="user", target=canon,
                         new_value=_role_names(db, body.role_ids))
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: UUID, body: UserIn, db: Session = Depends(get_db), cfg: AppConfig = Depends(get_config),
    principal: Principal = Depends(require_admin),
) -> UserOut:
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    current_role_ids = {str(link.role_id) for link in user.roles}
    requested_role_ids = {str(r) for r in body.role_ids}
    if str(user.id) == principal.user_id and requested_role_ids != current_role_ids:
        # Deliberate rule: nobody can edit their
        # own roles (prevents self-escalation to admin, or self-
        # broadening of grants). Display name / active flag on one's
        # own row are still editable here; role assignment is not.
        raise HTTPException(status_code=403, detail="You cannot change your own role assignments.")

    _validate_role_ids(db, body.role_ids)
    if str(user.id) == principal.user_id and not body.is_active:
        raise HTTPException(status_code=403, detail="You cannot deactivate your own account.")
    before = _active_admin_count(db)
    old_roles, old_active = _role_names(db, current_role_ids), user.is_active
    user.display_name = body.display_name or user.display_name
    user.is_active = body.is_active
    _apply_password(cfg, user, body.password, required=False)
    if requested_role_ids != current_role_ids:
        db.query(AppUserRole).filter(AppUserRole.user_id == user.id).delete()
        for role_id in body.role_ids:
            db.add(AppUserRole(user_id=user.id, role_id=role_id))
    _guard_last_admin(db, before)
    if requested_role_ids != current_role_ids or old_active != body.is_active:
        record_access_change(db, actor=principal.username, action="user_update", target_type="user",
                             target=user.username,
                             old_value=f"{old_roles}{'' if old_active else ' (inactive)'}",
                             new_value=f"{_role_names(db, requested_role_ids)}{'' if body.is_active else ' (inactive)'}")
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)) -> dict:
    if str(user_id) == principal.user_id:
        raise HTTPException(status_code=403, detail="You cannot delete your own account.")
    user = db.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    before = _active_admin_count(db)
    name = user.username
    db.delete(user)
    _guard_last_admin(db, before)
    record_access_change(db, actor=principal.username, action="user_delete", target_type="user", target=name)
    db.commit()
    return {"ok": True}


@router.get("/login-audit", response_model=List[LoginAuditOut])
def login_audit(limit: int = 100, db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> List[LoginAuditOut]:
    rows = (
        db.query(AppLoginAudit)
        .order_by(AppLoginAudit.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [
        LoginAuditOut(
            id=r.id, username_attempted=r.username_attempted, success=r.success, reason=r.reason,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]
