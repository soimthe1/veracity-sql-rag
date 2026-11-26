"""Local sign-in (catalog.auth.mode: local) end to end: one-time setup code,
first administrator, local accounts, password change. Needs a throwaway
catalog Postgres: set CATALOG_TEST_CONFIG to a config file pointing at one
(every app user in it is deleted)."""
import os
import tempfile

import pytest
import yaml

BASE = os.environ.get("CATALOG_TEST_CONFIG")
pytestmark = pytest.mark.skipif(not BASE, reason="set CATALOG_TEST_CONFIG to run API tests against a test database")


@pytest.fixture(scope="module")
def api():
    raw = yaml.safe_load(open(BASE, encoding="utf-8"))
    auth = raw["catalog"].setdefault("auth", {})
    auth.pop("enabled", None)
    auth.update({"mode": "local", "jwt_secret_env_var": "TEST_JWT_SECRET"})
    path = os.path.join(tempfile.mkdtemp(), "config.yaml")
    yaml.safe_dump(raw, open(path, "w", encoding="utf-8"))
    os.environ["CONFIG_PATH"] = path
    os.environ["TEST_JWT_SECRET"] = "x" * 40

    from shared.config import load_config
    cfg = load_config(force_reload=True)
    assert cfg.catalog.auth.mode == "local" and cfg.catalog.auth.enabled

    from fastapi.testclient import TestClient
    from sqlalchemy import text
    import api.main as api_main
    from shared.db import get_session_factory, init_db
    assert init_db(cfg)
    with get_session_factory(cfg)() as s:
        s.execute(text("DELETE FROM app_user_roles; DELETE FROM app_users; "
                       "DELETE FROM catalog_settings WHERE key = 'auth.setup_code';"))
        s.commit()
    with TestClient(api_main.app) as client:
        client.setup_code = lambda: _code(cfg)
        yield client


def _code(cfg):
    from shared.auth.login import ensure_setup_code
    from shared.db import get_session_factory
    with get_session_factory(cfg)() as s:
        return ensure_setup_code(s)


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_first_admin_via_setup_code_then_local_accounts(api):
    assert api.get("/v1/auth/setup").json() == {"mode": "local", "needed": True}
    assert api.get("/v1/auth/me").status_code == 401

    wrong = api.post("/v1/auth/setup", json={"code": "NOPE-NOPE-NOPE", "username": "root", "password": "long-enough-1"})
    assert wrong.status_code == 400
    weak = api.post("/v1/auth/setup", json={"code": api.setup_code(), "username": "root", "password": "short"})
    assert weak.status_code == 400

    r = api.post("/v1/auth/setup", json={"code": api.setup_code().lower(), "username": "Root",
                                         "password": "long-enough-1", "display_name": "First Admin"})
    assert r.status_code == 200, r.text
    admin = r.json()["token"]
    assert r.json()["principal"]["is_admin"] and r.json()["principal"]["auth_mode"] == "local"
    assert api.get("/v1/auth/setup").json()["needed"] is False
    again = api.post("/v1/auth/setup", json={"code": "X", "username": "evil", "password": "long-enough-2"})
    assert again.status_code == 400

    roles = api.get("/v1/auth/roles", headers=hdr(admin)).json()
    reader_role = api.post("/v1/auth/roles", headers=hdr(admin),
                           json={"name": "Readers", "grants": [{"level": 10, "category": "A"}]}).json()
    assert roles
    no_pw = api.post("/v1/auth/users", headers=hdr(admin),
                     json={"username": "ana", "is_active": True, "role_ids": [reader_role["id"]]})
    assert no_pw.status_code == 400
    ana = api.post("/v1/auth/users", headers=hdr(admin), json={
        "username": "ana", "password": "ana-password", "is_active": True, "role_ids": [reader_role["id"]]}).json()

    assert api.post("/v1/auth/login", json={"username": "ana", "password": "wrong-one"}).status_code == 401
    login = api.post("/v1/auth/login", json={"username": "ANA", "password": "ana-password"})
    assert login.status_code == 200 and not login.json()["principal"]["is_admin"]
    tok = login.json()["token"]

    bad = api.post("/v1/auth/me/password", headers=hdr(tok),
                   json={"current_password": "nope", "new_password": "ana-new-password"})
    assert bad.status_code == 400
    ok = api.post("/v1/auth/me/password", headers=hdr(tok),
                  json={"current_password": "ana-password", "new_password": "ana-new-password"})
    assert ok.status_code == 200
    assert api.post("/v1/auth/login", json={"username": "ana", "password": "ana-new-password"}).status_code == 200

    reset = api.patch(f"/v1/auth/users/{ana['id']}", headers=hdr(admin), json={
        "username": "ana", "password": "admin-reset-1", "is_active": True, "role_ids": [reader_role["id"]]})
    assert reset.status_code == 200
    assert api.post("/v1/auth/login", json={"username": "ana", "password": "admin-reset-1"}).status_code == 200
