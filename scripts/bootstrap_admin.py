"""
One-time bootstrap: create the first admin role + user so
catalog.auth.enabled: true has SOMEONE who can log in and manage
everything else (other users, roles, sources visibility) through the
web UI's Security tab. Run once, by hand, after the very first
`alembic upgrade head` against a fresh catalog Postgres (init_db()
already does this automatically at process startup — this script just
also makes sure it has run before it queries app_users/app_roles).

Bootstrapping the first admin could be a manual SQL step; this project
already has shared/db.py's session machinery, so a small script is more
convenient than hand-written SQL, but it does the same job.

Usage (from repo root, venv active, .env/CONFIG_PATH pointed at your
real catalog Postgres):

    python scripts/bootstrap_admin.py <ad-username> ["Display Name"]

<ad-username> is folded through the same canonical_username() rule the
login flow itself uses (DOMAIN\\user / user@domain / MixedCase all
become the same lowercase key) — pass it in whatever form you'd
normally type at the AD login prompt.

Idempotent: running it again for a username that already exists just
makes sure they hold the Administrator role, rather than erroring.

Alternative that needs no shell access at all: put the account name in
CATALOG_BOOTSTRAP_ADMINS in .env (catalog.auth.bootstrap_admins) and just
sign in — that account is registered as an administrator on its first
successful AD login.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.auth.login import canonical_username, ensure_admin_role  # noqa: E402
from shared.config import load_config  # noqa: E402
from shared.db import get_session_factory, init_db  # noqa: E402
from shared.models import AppAccessAudit, AppUser, AppUserRole  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(f'Usage: python {sys.argv[0]} <ad-username> ["Display Name"]')
        raise SystemExit(2)

    raw_username = sys.argv[1]
    display_name = sys.argv[2] if len(sys.argv) > 2 else raw_username
    username = canonical_username(raw_username)

    cfg = load_config()
    if not cfg.catalog.auth.enabled:
        print("Note: catalog.auth.enabled is currently false — this user/role will be created, but the "
              "web UI won't ask for a login (or enforce any permission matrix) until you flip that on.")

    if not init_db(cfg):
        print("Could not reach/migrate the catalog Postgres database — check CATALOG_DB_* in .env.")
        raise SystemExit(1)

    session_factory = get_session_factory(cfg)
    session = session_factory()
    try:
        # The built-in Administrator role (migration 0007). Admins manage
        # the whole app and see all data (catalog.access.admins_see_all_data),
        # so it needs no grant cells of its own.
        role = ensure_admin_role(session)
        ADMIN_ROLE_NAME = role.name

        user = session.query(AppUser).filter(AppUser.username == username).one_or_none()
        if user is None:
            user = AppUser(username=username, display_name=display_name, is_active=True)
            session.add(user)
            session.flush()
            print(f"Created user '{username}'.")
        else:
            user.is_active = True
            print(f"User '{username}' already existed — ensured active.")

        already_linked = (
            session.query(AppUserRole).filter_by(user_id=user.id, role_id=role.id).one_or_none()
        )
        if already_linked is None:
            session.add(AppUserRole(user_id=user.id, role_id=role.id))
            session.add(AppAccessAudit(actor="scripts/bootstrap_admin.py", action="bootstrap_admin",
                                       target_type="user", target=username, new_value=ADMIN_ROLE_NAME))
            print(f"Granted '{ADMIN_ROLE_NAME}' to '{username}'.")
        else:
            print(f"'{username}' already holds '{ADMIN_ROLE_NAME}'.")

        session.commit()
        print(
            f"\nDone. '{raw_username}' can now log in (once catalog.auth.enabled: true and their AD "
            f"password authenticates against catalog.auth.ldap) and will land as a full administrator."
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
