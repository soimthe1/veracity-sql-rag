"""
End-to-end tests of the permission layer through the real FastAPI apps,
with catalog.auth ENABLED, against a real Postgres catalog.

Opt-in: set CATALOG_TEST_CONFIG to a config.yaml whose catalog.database
points at a DISPOSABLE catalog database (it is modified). AD is mocked (any
password works for registered users).

    CATALOG_TEST_CONFIG=/path/to/test_config.yaml python -m pytest tests -q
"""
from __future__ import annotations

import os
import tempfile

import pytest
import yaml

BASE = os.environ.get("CATALOG_TEST_CONFIG")
# Demo source databases the test catalog must contain (override to match yours).
DB_OPS = os.environ.get("CATALOG_TEST_DB_OPS", "Sales")          # has an Employees table with *Name* columns
DB_PERSONNEL = os.environ.get("CATALOG_TEST_DB_PERSONNEL", "HR")
DB_OTHER = os.environ.get("CATALOG_TEST_DB_OTHER", "Inventory")
pytestmark = pytest.mark.skipif(not BASE, reason="set CATALOG_TEST_CONFIG to run API tests against a test database")


@pytest.fixture(scope="module")
def env():
    raw = yaml.safe_load(open(BASE, encoding="utf-8"))
    cat = raw["catalog"]
    cat.setdefault("auth", {})
    cat["auth"].update({"enabled": True, "bootstrap_admins": "Boss.Admin", "jwt_secret_env_var": "TEST_JWT_SECRET"})
    cat["auth"]["ldap"] = {"server_url": "ldap://example.invalid", "domain": "example.local"}
    cat.setdefault("access", {})
    cat["access"]["lattice_file"] = os.path.join(os.path.dirname(__file__), "..", "config", "levels.yaml")
    cat["access"]["admins_see_all_data"] = True   # the default is false; most tests here use full-access admins
    cat.setdefault("answer", {})["service_key_env_var"] = "TEST_SERVICE_KEY"
    path = os.path.join(tempfile.mkdtemp(), "config.yaml")
    yaml.safe_dump(raw, open(path, "w", encoding="utf-8"))
    os.environ["CONFIG_PATH"] = path
    os.environ["TEST_JWT_SECRET"] = "x" * 40
    os.environ["TEST_SERVICE_KEY"] = "service-secret-123"

    from shared.config import load_config
    cfg = load_config(force_reload=True)

    import shared.auth.login as login_mod
    login_mod.ad_authenticate = lambda _cfg, _u, p: p == "pw"  # mocked AD

    from fastapi.testclient import TestClient
    import api.main as api_main
    import answer.main as answer_main
    from shared.db import get_session_factory, init_db
    assert init_db(cfg)
    Session = get_session_factory(cfg)
    with Session() as s:
        from sqlalchemy import text
        s.execute(text("DELETE FROM app_user_roles; DELETE FROM app_users; DELETE FROM catalog_access_rules; "
                       "DELETE FROM catalog_access_labels; DELETE FROM app_roles WHERE NOT is_system; "
                       "DELETE FROM app_access_audit; DELETE FROM catalog_ask_audit WHERE asked_by IS NOT NULL; DELETE FROM catalog_ask_log WHERE asked_by IS NOT NULL;"))
        s.commit()
        from shared.access import labeling
        from shared.access.lattice import load_lattice
        labeling.recompute(s, cfg, load_lattice(cfg), reason="test setup")
    with TestClient(api_main.app) as api, TestClient(answer_main.app) as ans:
        yield {"cfg": cfg, "api": api, "answer": ans, "Session": Session}


def login(api, user, pw="pw"):
    r = api.post("/v1/auth/login", json={"username": user, "password": pw})
    return r


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin(env):
    r = login(env["api"], "CORP\\Boss.Admin")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["principal"]["is_admin"] and body["principal"]["full_access"]
    assert "Administrator" in body["principal"]["roles"]
    return body["token"]


def test_unregistered_user_is_refused(env):
    assert login(env["api"], "stranger").status_code == 401
    assert login(env["api"], "Boss.Admin", "wrong").status_code == 401


def test_unauthenticated_requests_are_refused(env):
    assert env["api"].get("/v1/entities").status_code == 401
    assert env["api"].get("/v1/entities", headers=hdr("garbage")).status_code == 401
    assert env["answer"].post("/v1/ask", json={"question": "x", "grants": [{"level": 10, "category": "A"}]}).status_code == 401


def test_matrix_comes_from_lattice(env):
    m = env["api"].get("/v1/auth/matrix").json()
    assert any(lv["name"] == "Restricted" and lv["color"] for lv in m["levels"])
    assert "Z" not in m["grantable"]


def test_setup_realm_and_users(env, admin):
    api = env["api"]
    # Place two databases, restrict one column-rich table's secrets
    grid0 = api.get("/v1/access/grid?kind=table", headers=hdr(admin)).json()
    assert grid0["quarantine"] == grid0["total"] or grid0["quarantine"] > 0
    r = api.post("/v1/access/rules", headers=hdr(admin), json={
        "name": f"{DB_OPS} is general operations", "scope": "database", "database_pattern": DB_OPS,
        "set_level": 10, "set_category": "A"})
    assert r.status_code == 200, r.text
    r = api.post("/v1/access/rules", headers=hdr(admin), json={
        "name": f"{DB_PERSONNEL} is personnel", "scope": "database", "database_pattern": DB_PERSONNEL,
        "set_level": 20, "set_category": "C"})
    assert r.status_code == 200
    # an inapplicable pattern is refused
    bad = api.post("/v1/access/rules", headers=hdr(admin), json={
        "name": "bad", "scope": "database", "table_pattern": "x", "set_level": 10})
    assert bad.status_code == 400
    # preview of a column rule
    pv = api.post("/v1/access/rules/preview", headers=hdr(admin), json={"rule": {
        "name": "names are secret", "scope": "column", "database_pattern": DB_OPS, "column_pattern": "*Name*",
        "set_level": 40, "set_category": "D"}}).json()
    assert pv["columns_changed"] > 0 and pv["tables_changed"] == 0
    r = api.post("/v1/access/rules", headers=hdr(admin), json={
        "name": "names are secret", "scope": "column", "database_pattern": DB_OPS, "column_pattern": "*Name*",
        "set_level": 40, "set_category": "D"})
    assert r.status_code == 200

    # roles: quarantine can't be granted; valid cells can
    assert api.post("/v1/auth/roles", headers=hdr(admin), json={"name": "bad", "grants": [{"level": 120, "category": "Z"}]}).status_code == 400
    ops = api.post("/v1/auth/roles", headers=hdr(admin), json={"name": "Ops reader", "grants": [{"level": 10, "category": "A"}]}).json()
    both = api.post("/v1/auth/roles", headers=hdr(admin), json={
        "name": "Ops + secrets", "grants": [{"level": 10, "category": "A"}, {"level": 40, "category": "D"}]}).json()
    u1 = api.post("/v1/auth/users", headers=hdr(admin), json={"username": "reader1", "role_ids": [ops["id"]]})
    u2 = api.post("/v1/auth/users", headers=hdr(admin), json={"username": "reader2", "role_ids": [both["id"]]})
    assert u1.status_code == 200 and u2.status_code == 200
    assert u2.json()["cells"] == [{"level": 10, "category": "A"}, {"level": 40, "category": "D"}]


def _token(env, user):
    r = login(env["api"], user)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_non_admins_get_ask_only_by_default(env):
    """catalog.access.non_admin_screens defaults to ["ask"]: a restricted
    user gets Ask and nothing else, and the API refuses the rest — hiding
    menu items is never the only gate. Admins get every screen."""
    api = env["api"]
    tok = _token(env, "reader2")
    me = api.get("/v1/auth/me", headers=hdr(tok)).json()
    assert me["screens"] == ["ask"]
    for path in ("/v1/status", "/v1/status/summary", "/v1/status/system", "/v1/entities", "/v1/sources",
                 "/v1/suggestions", "/v1/schema-graph", "/v1/settings/trust-level"):
        r = api.get(path, headers=hdr(tok))
        assert r.status_code == 403, (path, r.status_code)
    assert api.post("/v1/search", headers=hdr(tok), json={"query": "employee"}).status_code == 403
    # what Ask needs still works
    assert api.get("/v1/auth/matrix").status_code == 200
    scope = api.get("/v1/access/scope", headers=hdr(tok)).json()
    assert scope["tables_visible"] > 0 and scope["tables_total"] is None and scope["databases_total"] is None
    assert scope["databases"] and all(d["visible"] > 0 for d in scope["databases"])
    # status is no longer readable without signing in at all (the test
    # client keeps the session cookie from earlier sign-ins — drop it)
    api.cookies.clear()
    assert api.get("/v1/status").status_code == 401
    # admins: everything
    adm = _admin_token(env)
    assert "access" in api.get("/v1/auth/me", headers=hdr(adm)).json()["screens"]
    assert api.get("/v1/status", headers=hdr(adm)).status_code == 200
    assert api.get("/v1/access/scope", headers=hdr(adm)).json()["tables_total"] > 0


def test_restricted_user_sees_only_their_cells(env):
    """With the Entities screen opened to non-admins, browsing is still
    limited to the person's own cells."""
    cfg = env["cfg"]
    saved = list(cfg.catalog.access.non_admin_screens)
    cfg.catalog.access.non_admin_screens = ["ask", "entities"]
    try:
        _restricted_browse(env)
    finally:
        cfg.catalog.access.non_admin_screens = saved


def _restricted_browse(env):
    api = env["api"]
    tok = _token(env, "reader1")
    me = api.get("/v1/auth/me", headers=hdr(tok)).json()
    assert not me["is_admin"] and not me["full_access"] and me["grants"] == [{"level": 10, "category": "A"}]
    assert me["screens"] == ["ask", "entities"]
    assert api.get("/v1/status", headers=hdr(tok)).status_code == 403
    ents = api.get("/v1/entities?limit=500", headers=hdr(tok)).json()
    assert ents and all(e["database_name"] == DB_OPS for e in ents)
    assert all((e["access_level"], e["access_category"]) == (10, "A") for e in ents)
    # column-level: *Name* columns (40D) are withheld from reader1 ...
    with_names = next(e for e in ents if e["object_name"] == "Employees")
    detail = api.get(f"/v1/entities/{with_names['id']}", headers=hdr(tok)).json()
    assert detail["fields"] and not any("name" in f["column_name"].lower() for f in detail["fields"])
    # ... but shown to reader2 who holds 40D too
    tok2 = _token(env, "reader2")
    detail2 = api.get(f"/v1/entities/{with_names['id']}", headers=hdr(tok2)).json()
    assert any("name" in f["column_name"].lower() for f in detail2["fields"])
    # a table outside the cells is a 404, not a 403 (no enumeration)
    other = api.get(f"/v1/entities?limit=500&database={DB_PERSONNEL}", headers=hdr(_admin_token(env))).json()[0]
    assert api.get(f"/v1/entities/{other['id']}", headers=hdr(tok)).status_code == 404
    scope = api.get("/v1/access/scope", headers=hdr(tok)).json()
    assert scope["tables_visible"] == len([e for e in ents if e["object_type"] in ("TABLE", "VIEW") and e["status"] != "STALE"])
    assert scope["columns_hidden"] > 0 and scope["cells"] == ["10A"]
    # admin-only endpoints are refused
    for path in ("/v1/access/grid", "/v1/access/rules", "/v1/auth/users", "/v1/access/review"):
        assert api.get(path, headers=hdr(tok)).status_code == 403, path


def _admin_token(env):
    return _token(env, "Boss.Admin")


def test_admin_sees_everything(env):
    api = env["api"]
    tok = _admin_token(env)
    all_ents = api.get("/v1/entities?limit=500", headers=hdr(tok)).json()
    assert len({e["database_name"] for e in all_ents}) > 2
    scope = api.get("/v1/access/scope", headers=hdr(tok)).json()
    assert scope["full_access"] and scope["tables_visible"] == scope["tables_total"]


def test_manual_placement_explain_and_reset(env):
    api = env["api"]
    tok = _admin_token(env)
    ent = api.get(f"/v1/entities?limit=5&database={DB_PERSONNEL}", headers=hdr(tok)).json()[0]
    target = {"kind": "table", "id": ent["id"]}
    r = api.post("/v1/access/placements", headers=hdr(tok), json={"targets": [target], "level": 30, "category": "E", "reason": "test"})
    assert r.status_code == 200, r.text
    ex = api.get(f"/v1/access/explain?kind=table&id={ent['id']}", headers=hdr(tok)).json()
    assert ex["level"]["value"] == 30 and ex["level"]["source"] == "manual" and ex["pin"]["reason"] == "test"
    rule_cands = [c for c in ex["level"]["candidates"] if c["layer"] == "inherited"]
    assert rule_cands and not rule_cands[0]["won"], "the database rule is listed but lost"
    after = api.get(f"/v1/entities/{ent['id']}", headers=hdr(tok)).json()
    assert (after["access_level"], after["access_category"], after["access_level_source"]) == (30, "E", "manual")
    r = api.post("/v1/access/placements/reset", headers=hdr(tok), json={"targets": [target]})
    assert r.json()["reset"] == 1
    back = api.get(f"/v1/entities/{ent['id']}", headers=hdr(tok)).json()
    assert (back["access_level"], back["access_category"]) == (20, "C")
    audit = api.get("/v1/access/audit", headers=hdr(tok)).json()
    assert {"placement", "placement_reset", "rule_create", "role_create", "user_create"} <= {a["action"] for a in audit}


def test_database_and_schema_placements_cascade(env):
    api = env["api"]
    tok = _admin_token(env)
    dbs = api.get("/v1/access/objects?kind=database", headers=hdr(tok)).json()["items"]
    target_db = next(d for d in dbs if d["database"] == DB_OTHER)
    r = api.post("/v1/access/placements", headers=hdr(tok), json={
        "targets": [{"kind": "database", "id": target_db["id"]}], "level": 20, "category": "B"})
    assert r.status_code == 200
    ents = api.get(f"/v1/entities?limit=500&database={DB_OTHER}", headers=hdr(tok)).json()
    assert ents and all((e["access_level"], e["access_category"]) == (20, "B") for e in ents if e["object_type"] in ("TABLE", "VIEW"))
    grid = api.get("/v1/access/grid?kind=database", headers=hdr(tok)).json()
    assert any(c["level"] == 20 and c["category"] == "B" for c in grid["cells"])


def test_review_flow(env):
    api = env["api"]
    tok = _admin_token(env)
    # DB_OPS has flagged tables (AI proposes > 10); the database rule says 10A
    rv = api.get("/v1/access/review", headers=hdr(tok)).json()
    assert rv["total"] > 0
    item = next(i for i in rv["items"] if i["kind"] == "table")
    t = {"kind": "table", "id": item["id"]}
    assert api.post("/v1/access/review/dismiss", headers=hdr(tok), json={"targets": [t]}).status_code == 200
    rv2 = api.get("/v1/access/review", headers=hdr(tok)).json()
    assert rv2["total"] == rv["total"] - 1
    api.post("/v1/access/recompute", headers=hdr(tok))
    assert api.get("/v1/access/review", headers=hdr(tok)).json()["total"] == rv2["total"], "dismissal survives recompute"
    item2 = next(i for i in rv2["items"] if i["kind"] == "table")
    assert api.post("/v1/access/review/accept", headers=hdr(tok), json={"targets": [{"kind": "table", "id": item2["id"]}]}).status_code == 200
    accepted = api.get(f"/v1/entities/{item2['id']}", headers=hdr(tok)).json()
    assert accepted["access_level_source"] == "manual" and accepted["access_level"] == item2["ai_level"]


def test_simulate(env):
    api = env["api"]
    tok = _admin_token(env)
    sim = api.get("/v1/access/simulate?cells=10A", headers=hdr(tok)).json()
    assert sim["cells"] == ["10A"]
    assert sim["tables_visible"] > 0 and all(t["cell"] == "10A" for t in sim["sample_tables"])
    assert api.get("/v1/access/simulate?cells=99Q", headers=hdr(tok)).status_code == 400
    # the old category-first spelling is an error naming it, not "no tables"
    old = api.get("/v1/access/simulate?cells=A10", headers=hdr(tok))
    assert old.status_code == 400 and "A10" in old.json()["detail"] and "level then category" in old.json()["detail"]


def test_admin_protections(env):
    api = env["api"]
    tok = _admin_token(env)
    roles = api.get("/v1/auth/roles", headers=hdr(tok)).json()
    admin_role = next(r for r in roles if r["is_system"])
    assert api.delete(f"/v1/auth/roles/{admin_role['id']}", headers=hdr(tok)).status_code == 403
    assert api.patch(f"/v1/auth/roles/{admin_role['id']}", headers=hdr(tok), json={
        "name": admin_role["name"], "is_admin": False, "grants": []}).status_code == 403
    users = api.get("/v1/auth/users", headers=hdr(tok)).json()
    me = next(u for u in users if u["username"] == "boss.admin")
    # can't change own roles, can't deactivate self
    assert api.patch(f"/v1/auth/users/{me['id']}", headers=hdr(tok), json={
        "username": me["username"], "is_active": True, "role_ids": []}).status_code == 403
    assert api.patch(f"/v1/auth/users/{me['id']}", headers=hdr(tok), json={
        "username": me["username"], "is_active": False, "role_ids": me["role_ids"]}).status_code == 403
    # a second admin, then removing admin from the only-other path works; last one can't be removed
    r1 = next(u for u in users if u["username"] == "reader1")
    assert api.patch(f"/v1/auth/users/{r1['id']}", headers=hdr(tok), json={
        "username": "reader1", "is_active": True, "role_ids": r1["role_ids"] + [admin_role["id"]]}).status_code == 200
    tok_r1 = _token(env, "reader1")
    # reader1 (now admin) removes Boss's admin role -> allowed (reader1 remains)
    assert api.patch(f"/v1/auth/users/{me['id']}", headers=hdr(tok_r1), json={
        "username": me["username"], "is_active": True, "role_ids": []}).status_code == 200
    users2 = api.get("/v1/auth/users", headers=hdr(tok_r1)).json()
    r1b = next(u for u in users2 if u["username"] == "reader1")
    # reader1 can't remove its own admin (self-change), and nobody can delete the last admin
    assert api.delete(f"/v1/auth/users/{r1b['id']}", headers=hdr(tok_r1)).status_code == 403
    # restore Boss as admin for the remaining tests
    assert api.patch(f"/v1/auth/users/{me['id']}", headers=hdr(tok_r1), json={
        "username": me["username"], "is_active": True, "role_ids": [admin_role["id"]]}).status_code == 200


def test_answer_ignores_browser_grants_for_signed_in_users(env):
    from fastapi import Request
    from answer.access_context import resolve_ask_access
    from answer.schemas import GrantIn

    cfg = env["cfg"]
    tok = _token(env, "reader2")

    def req(headers):
        scope = {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
        return Request(scope)

    with env["Session"]() as s:
        forged = [GrantIn(level=120, category="Z"), GrantIn(level=50, category="D")]
        acc = resolve_ask_access(req(hdr(tok)), s, cfg, forged)
        assert acc.mode == "user" and acc.pairs == {(10, "A"), (40, "D")} and not acc.bypass
        adm = resolve_ask_access(req(hdr(_admin_token(env))), s, cfg, [])
        assert adm.bypass and adm.is_admin
        svc = resolve_ask_access(req({"X-Service-Key": "service-secret-123"}), s, cfg, forged)
        assert svc.mode == "service" and svc.pairs == {(120, "Z"), (50, "D")}
        with pytest.raises(Exception):
            resolve_ask_access(req({"X-Service-Key": "wrong"}), s, cfg, forged)


def test_grounding_withholds_restricted_columns(env):
    from answer.grounding import ground_question
    from answer.schemas import GrantIn
    cfg = env["cfg"]
    with env["Session"]() as s:
        g = ground_question(s, cfg.catalog.search, cfg.catalog.answer, cfg.catalog.confidence,
                            "employees names", [GrantIn(level=10, category="A")], bypass_grants=False)
        assert g.ok and g.database.database_name == DB_OPS
        shown = {f.column_name.lower() for t in g.tables for f in t.fields}
        assert not any("name" in c for c in shown), "restricted columns never reach the LLM"
        assert g.denied_columns and (10, "A") in g.cells
        g2 = ground_question(s, cfg.catalog.search, cfg.catalog.answer, cfg.catalog.confidence,
                             "employees names", [GrantIn(level=10, category="A"), GrantIn(level=40, category="D")],
                             bypass_grants=False)
        assert not g2.denied_columns and (40, "D") in g2.cells


def test_suggestions_and_recent_are_per_user(env):
    ans = env["answer"]
    tok = _token(env, "reader2")  # reader1 was made an admin by test_admin_protections
    sug = ans.get("/v1/ask/suggestions?limit=10", headers=hdr(tok)).json()
    assert all(s["database_name"] == DB_OPS for s in sug)
    assert ans.get("/v1/ask/recent", headers=hdr(tok)).json() == []


def test_ask_response_carries_access_and_label(env, monkeypatch):
    """Full /v1/ask round trip with a signed-in user: forged body grants are
    ignored, the response reports the caller's cells and the answer's label,
    and restricted columns never reach the generated SQL."""
    import answer.routers.ask as ask_mod
    from answer import sql_generation
    from answer.executor import ExecutionResult

    seen_sql = []

    def fake_generate(provider, cfg, question, grounding, understanding=None, previous_attempt=None):
        cols = [f.column_name for t in grounding.tables for f in t.fields][:2] or ["1"]
        t0 = grounding.tables[0].entity
        sql = f"SELECT {', '.join(cols)} FROM {t0.schema_name}.{t0.object_name}"
        return sql_generation.SqlProposal(queries=[sql_generation.GeneratedQuery(sql=sql, purpose="test")],
                                          reasoning="", confidence_hint=0.5)

    def fake_execute(source_cfg, database_name, sql, answer_cfg):
        seen_sql.append(sql)
        return ExecutionResult(ok=True, rows=[{"n": 1}])

    monkeypatch.setattr(ask_mod.sql_generation, "generate_sql", fake_generate)
    monkeypatch.setattr(ask_mod.executor_mod, "execute", fake_execute)
    monkeypatch.setattr(ask_mod, "build_llm_provider", lambda cfg: None)
    monkeypatch.setattr(ask_mod, "understand_question", lambda *a, **k: None)
    monkeypatch.setattr(ask_mod.synthesis, "synthesize_answer", lambda *a, **k: "ok")
    cfg = env["cfg"]
    if not any(s.name == "primary" and s.is_configured for s in cfg.catalog.sources):
        pytest.skip("test config has no configured source named primary")

    tok = _token(env, "reader2")
    r = env["answer"].post("/v1/ask", headers=hdr(tok), json={
        "question": "employees names", "grants": [{"level": 120, "category": "Z"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access"]["mode"] == "user"
    assert body["access"]["cells"] == ["10A", "40D"], "forged body grants ignored"
    assert body["access"]["label_level"] is not None
    assert body["chunks"] and body["chunks"][0]["level"] == body["access"]["label_level"]
    assert seen_sql


# ---------------------------------------------------------------------------
# Ask log, SQL visibility, Create, question audit
# ---------------------------------------------------------------------------

def _fake_pipeline(monkeypatch, rows=None):
    import answer.routers.ask as ask_mod
    from answer import sql_generation
    from answer.executor import ExecutionResult

    def fake_generate(provider, cfg, question, grounding, understanding=None, previous_attempt=None):
        cols = [f.column_name for t in grounding.tables for f in t.fields][:2] or ["1"]
        t0 = grounding.tables[0].entity
        sql = f"SELECT {', '.join(cols)} FROM {t0.schema_name}.{t0.object_name}"
        return sql_generation.SqlProposal(queries=[sql_generation.GeneratedQuery(sql=sql, purpose="count by status")],
                                          reasoning="", confidence_hint=0.5)

    monkeypatch.setattr(ask_mod.sql_generation, "generate_sql", fake_generate)
    monkeypatch.setattr(ask_mod.executor_mod, "execute",
                        lambda *a, **k: ExecutionResult(ok=True, rows=rows if rows is not None else
                                                        [{"status": "Done", "n": 12345678901234567}, {"status": "New", "n": 3}]))
    monkeypatch.setattr(ask_mod, "build_llm_provider", lambda cfg: None)
    monkeypatch.setattr(ask_mod, "understand_question", lambda *a, **k: None)
    monkeypatch.setattr(ask_mod.synthesis, "synthesize_answer", lambda *a, **k: "There are 12 requests.")


def _need_source(env):
    if not any(s.name == "primary" and s.is_configured for s in env["cfg"].catalog.sources):
        pytest.skip("test config has no configured source named primary")


def test_every_question_is_logged_and_sql_only_for_permitted_roles(env, monkeypatch):
    _need_source(env)
    _fake_pipeline(monkeypatch)
    ans, api = env["answer"], env["api"]
    tok = _token(env, "reader2")
    body = ans.post("/v1/ask", headers=hdr(tok), json={"question": "employees by status"}).json()
    assert body["ask_id"] and body["database_name"]
    # structured rows, values as strings (big numbers survive JSON)
    assert body["results"] and body["results"][0]["columns"] == ["status", "n"]
    assert body["results"][0]["rows"][0] == ["Done", "12345678901234567"]
    # a normal user never receives the SQL
    assert body["queries"] is None
    # ... one log row per question, with the classification and cells
    from shared.models import CatalogAskAudit, CatalogAskLog
    with env["Session"]() as s:
        log = s.get(CatalogAskLog, __import__("uuid").UUID(body["ask_id"]))
        assert log.asked_by == "reader2" and log.outcome == "ANSWERED" and log.queries == 1
        assert log.cells_held == ["10A", "40D"] and log.label_level == body["access"]["label_level"]
        assert s.query(CatalogAskAudit).filter(CatalogAskAudit.ask_id == log.id).count() == 1
    # history / recent read the log
    hist = ans.get("/v1/ask/history", headers=hdr(tok)).json()
    assert hist["total"] >= 1 and hist["items"][0]["ask_id"] == body["ask_id"]
    assert hist["items"][0]["tables"] >= 1 and hist["items"][0]["outcome"] == "ANSWERED"

    # a role with "Can see generated SQL" gets the queries back
    adm = _admin_token(env)
    roles = api.get("/v1/auth/roles", headers=hdr(adm)).json()
    both = next(r for r in roles if r["name"] == "Ops + secrets")
    upd = api.patch(f"/v1/auth/roles/{both['id']}", headers=hdr(adm), json={
        "name": both["name"], "description": both["description"], "is_admin": False,
        "grants": both["grants"], "can_view_sql": True})
    assert upd.status_code == 200 and upd.json()["can_view_sql"] is True
    try:
        assert api.get("/v1/auth/me", headers=hdr(tok)).json()["can_view_sql"] is True
        body2 = ans.post("/v1/ask", headers=hdr(tok), json={"question": "employees by status"}).json()
        assert body2["queries"] and body2["queries"][0]["sql"].upper().startswith("SELECT")
        assert body2["queries"][0]["purpose"] == "count by status"
    finally:
        api.patch(f"/v1/auth/roles/{both['id']}", headers=hdr(adm), json={
            "name": both["name"], "description": both["description"], "is_admin": False,
            "grants": both["grants"], "can_view_sql": False})


def test_database_picker_limits_where_the_answer_comes_from(env, monkeypatch):
    _need_source(env)
    _fake_pipeline(monkeypatch)
    ans = env["answer"]
    adm = _admin_token(env)
    unknown = ans.post("/v1/ask", headers=hdr(adm), json={"question": "employees", "database": "NoSuchDb"}).json()
    assert not unknown["chunks"] and "not in the catalog" in unknown["notes"]
    scoped = ans.post("/v1/ask", headers=hdr(adm), json={"question": "employees", "database": DB_OPS}).json()
    assert scoped["database_name"] in (None, DB_OPS)


def test_create_word_and_powerpoint_with_marking_and_ownership(env, monkeypatch):
    _need_source(env)
    _fake_pipeline(monkeypatch)
    ans = env["answer"]
    tok = _token(env, "reader2")
    body = ans.post("/v1/ask", headers=hdr(tok), json={"question": "employees by status"}).json()
    content = {"answer": body["answer"], "results": body["results"], "tables": [n["label"] for n in body["graph_nodes"]],
               "trust_label": body["trust_label"], "trust_score": body["trust_score"]}

    r = ans.post("/v1/ask/create", headers=hdr(tok), json={"ask_id": body["ask_id"], "format": "docx", "content": content})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]
    import io
    import docx
    d = docx.Document(io.BytesIO(r.content))
    header_text = " ".join(p.text for p in d.sections[0].header.paragraphs)
    cell = f"{body['access']['label_level']}{body['access']['label_category']}"
    assert body["access"]["cells_used"] and all(c[0].isdigit() for c in body["access"]["cells_used"])
    assert cell in header_text, "classification comes from the audit record"
    assert d.paragraphs[0].text == "employees by status"
    assert any("Done" in c.text for t in d.tables for row in t.rows for c in row.cells)
    assert "SELECT" not in "\n".join(p.text for p in d.paragraphs), "no SQL for users without the role flag"

    r = ans.post("/v1/ask/create", headers=hdr(tok), json={"ask_id": body["ask_id"], "format": "pptx", "lang": "ar",
                                                           "content": content})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    import pptx
    deck = pptx.Presentation(io.BytesIO(r.content))
    assert len(deck.slides) >= 3
    texts = [sh.text_frame.text for sl in deck.slides for sh in sl.shapes if sh.has_text_frame]
    assert any(cell in t for t in texts)

    # pdf/email are made in the browser and only logged
    assert ans.post("/v1/ask/create", headers=hdr(tok), json={"ask_id": body["ask_id"], "format": "pdf"}).json() == {"ok": True}
    assert ans.post("/v1/ask/create", headers=hdr(tok), json={"ask_id": body["ask_id"], "format": "exe"}).status_code == 400
    # someone else's answer is not found (no probing); admins may
    other = _token(env, "reader1")
    assert ans.post("/v1/ask/create", headers=hdr(other), json={"ask_id": body["ask_id"], "format": "pdf"}).status_code in (200, 404)
    assert ans.post("/v1/ask/create", headers=hdr(tok), json={"ask_id": "not-a-uuid", "format": "pdf"}).status_code == 404

    from shared.models import CatalogAskExport
    with env["Session"]() as s:
        fmts = [e.format for e in s.query(CatalogAskExport).filter(CatalogAskExport.ask_id == __import__("uuid").UUID(body["ask_id"]))]
        assert {"docx", "pptx", "pdf"} <= set(fmts)


def test_question_audit_is_admin_only_and_shows_the_sql(env, monkeypatch):
    _need_source(env)
    _fake_pipeline(monkeypatch)
    ans, api = env["answer"], env["api"]
    tok = _token(env, "reader2")
    body = ans.post("/v1/ask", headers=hdr(tok), json={"question": "audited question"}).json()
    assert api.get("/v1/audit/questions", headers=hdr(tok)).status_code == 403
    adm = _admin_token(env)
    page = api.get("/v1/audit/questions?period=24h&user=reader2", headers=hdr(adm)).json()
    assert page["total"] >= 1 and page["kpis"]["questions"] >= 1 and "reader2" in page["people"]
    item = next(i for i in page["items"] if i["id"] == body["ask_id"])
    assert item["asked_by"] == "reader2" and item["outcome"] == "ANSWERED"
    detail = api.get(f"/v1/audit/questions/{body['ask_id']}", headers=hdr(adm)).json()
    assert detail["queries_list"] and detail["queries_list"][0]["sql"].upper().startswith("SELECT")
    assert detail["cells_held"] == ["10A", "40D"]
    assert api.get("/v1/audit/questions?flag=attention", headers=hdr(adm)).status_code == 200


def test_retention_prunes_only_when_set(env):
    from answer.audit import prune
    with env["Session"]() as s:
        assert prune(s, 0) == 0


def test_server_caller_integration_path(env, monkeypatch):
    """Exactly what docs/integration.md tells a server caller to do: service key +
    the end user's grants + X-On-Behalf-Of, then Create through the same
    headers."""
    _need_source(env)
    _fake_pipeline(monkeypatch)
    ans = env["answer"]
    ans.cookies.clear()
    svc = {"X-Service-Key": "service-secret-123", "X-On-Behalf-Of": "CORP\\App.User"}
    r = ans.post("/v1/ask", headers=svc, json={"question": "employees by status", "grants": [{"level": 10, "category": "A"}, {"level": 40, "category": "D"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access"]["mode"] == "service" and body["ask_id"] and body["answer"]
    assert body["queries"] is None, "no SQL to the server caller"
    from shared.models import CatalogAskLog
    import uuid as _uuid
    with env["Session"]() as s:
        log = s.get(CatalogAskLog, _uuid.UUID(body["ask_id"]))
        assert log.asked_by == "app.user" and log.mode == "service"
    # the user's history, through the server caller
    hist = ans.get("/v1/ask/history", headers=svc).json()
    assert hist["items"] and all(i["question"] for i in hist["items"])
    assert ans.get("/v1/ask/history", headers={"X-Service-Key": "service-secret-123"}).json()["items"] == []
    # suggestions for the user's cells
    sug = ans.get("/v1/ask/suggestions?limit=5&cells=10A", headers=svc).json()
    assert all(s["database_name"] == DB_OPS for s in sug)
    # Create: file, email text, print page
    content = {"answer": body["answer"], "results": body["results"], "tables": [n["label"] for n in body["graph_nodes"]]}
    f = ans.post("/v1/ask/create", headers=svc, json={"ask_id": body["ask_id"], "format": "docx", "content": content})
    assert f.status_code == 200 and f.content[:2] == b"PK"
    em = ans.post("/v1/ask/create", headers=svc, json={"ask_id": body["ask_id"], "format": "email", "content": content}).json()
    cell = f"{body['access']['label_level']}{body['access']['label_category']}"
    assert body["access"]["cells"] == ["10A", "40D"] and all(c[0].isdigit() for c in body["access"]["cells_used"])
    assert em["ok"] and cell in em["subject"] and "employees by status" in em["body"]
    page = ans.post("/v1/ask/create", headers=svc, json={"ask_id": body["ask_id"], "format": "html", "content": content})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html") and cell in page.text
    # someone else, or nobody, can't create from it
    other = {"X-Service-Key": "service-secret-123", "X-On-Behalf-Of": "someone.else"}
    assert ans.post("/v1/ask/create", headers=other, json={"ask_id": body["ask_id"], "format": "email"}).status_code == 404
    assert ans.post("/v1/ask/create", headers={"X-Service-Key": "service-secret-123"},
                    json={"ask_id": body["ask_id"], "format": "email"}).status_code == 403
    # no key: refused
    assert ans.post("/v1/ask", headers={"X-On-Behalf-Of": "app.user"}, json={"question": "x"}).status_code == 401


def test_suggestions_cells_are_level_then_category(env):
    """A server caller sends ?cells=20B (level then category). It must match a
    table stored at level 20, category B. "B20" is a 400 naming it, not a
    silent "no suggestions" that looks like "no access"."""
    from shared.models import CatalogDatabase, CatalogEntity
    api, ans = env["api"], env["answer"]
    adm = _admin_token(env)
    ans.cookies.clear()
    # a table that suggestions can be built from, pinned into 20B
    everything = ans.get("/v1/ask/suggestions?limit=60", headers=hdr(adm)).json()
    pick = next(s for s in everything if s["database_name"] != DB_OTHER)
    schema, obj = pick["table"].split(".", 1)
    with env["Session"]() as s:
        ent = (s.query(CatalogEntity).join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
               .filter(CatalogDatabase.database_name == pick["database_name"], CatalogEntity.schema_name == schema,
                       CatalogEntity.object_name == obj).one())
        eid = str(ent.id)
    target = {"kind": "table", "id": eid}
    assert api.post("/v1/access/placements", headers=hdr(adm),
                    json={"targets": [target], "level": 20, "category": "B"}).status_code == 200
    try:
        with env["Session"]() as s:
            e = s.get(CatalogEntity, ent.id)
            assert (e.access_level, e.access_category) == (20, "B"), "stored as level 20, category B"
            at_20b = {(d, f"{x.schema_name}.{x.object_name}") for x, d in
                      s.query(CatalogEntity, CatalogDatabase.database_name)
                      .join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
                      .filter(CatalogEntity.access_level == 20, CatalogEntity.access_category == "B").all()}
        svc = {"X-Service-Key": "service-secret-123", "X-On-Behalf-Of": "app.user"}
        got = ans.get("/v1/ask/suggestions?limit=60&cells=20B", headers=svc)
        assert got.status_code == 200, got.text
        sug = got.json()
        assert any(x["table"] == pick["table"] and x["database_name"] == pick["database_name"] for x in sug), \
            "?cells=20B matches the table stored at level 20, category B"
        assert all((x["database_name"], x["table"]) in at_20b for x in sug), "and only 20B tables"
        # a different cell does not match it
        other = ans.get("/v1/ask/suggestions?limit=60&cells=20A", headers=svc).json()
        assert not any(x["table"] == pick["table"] and x["database_name"] == pick["database_name"] for x in other)
        # the old spelling, and any other one, is refused loudly
        for bad in ("B20", "20b", "20:B", "20B,B40"):
            r = ans.get(f"/v1/ask/suggestions?cells={bad}", headers=svc)
            assert r.status_code == 400, (bad, r.status_code)
            assert "level then category" in r.json()["detail"]
        assert "'B20'" in ans.get("/v1/ask/suggestions?cells=B20", headers=svc).json()["detail"]
    finally:
        api.post("/v1/access/placements/reset", headers=hdr(adm), json={"targets": [target]})


def test_startup_check_refuses_old_cell_spelling(env):
    """No stored cell may be in the old category-first form. The database
    refuses it (migration 0010), and if a value ever got past that (a
    database without the constraints), the services refuse to start and
    say where it is (shared/access/cell_check.py)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from shared.access.cell_check import CellSpellingError, find_misspelled_cells, verify_cell_spelling
    with env["Session"]() as s:
        assert find_misspelled_cells(s) == [], "the real data (after migration 0009) is all level-first"
        verify_cell_spelling(s, env["cfg"])
    # 1. the database itself rejects every wrong spelling
    eid = None
    for sql in ("INSERT INTO catalog_ask_log (id, question, outcome, cells_held, cells_used, created_at) VALUES "
                "(gen_random_uuid(), 'probe', 'ERROR', ARRAY['10A','B20'], ARRAY[]::varchar[], now())",
                "UPDATE catalog_entities SET access_review_dismissed = 'C20' WHERE id = (SELECT id FROM catalog_entities LIMIT 1)",
                "UPDATE catalog_entities SET access_category = 'c' WHERE id = (SELECT id FROM catalog_entities LIMIT 1)",
                "UPDATE catalog_entities SET access_level = -10 WHERE id = (SELECT id FROM catalog_entities LIMIT 1)",
                "UPDATE app_role_grants SET category = 'd' WHERE id = (SELECT id FROM app_role_grants LIMIT 1)"):
        with env["Session"]() as s:
            with pytest.raises(IntegrityError):
                s.execute(text(sql))
                s.flush()
            s.rollback()
    # 2. with the constraints dropped (inside a transaction that is rolled
    #    back), the startup check finds the values and refuses
    with env["Session"]() as s:
        s.execute(text("ALTER TABLE catalog_ask_log DROP CONSTRAINT ck_catalog_ask_log_cells_held"))
        s.execute(text("ALTER TABLE catalog_entities DROP CONSTRAINT ck_catalog_entities_review_dismissed"))
        s.execute(text("INSERT INTO catalog_ask_log (id, question, outcome, cells_held, cells_used, created_at) VALUES "
                       "(gen_random_uuid(), 'probe', 'ERROR', ARRAY['10A','B20'], ARRAY[]::varchar[], now())"))
        eid = s.execute(text("SELECT id FROM catalog_entities LIMIT 1")).scalar()
        s.execute(text("UPDATE catalog_entities SET access_review_dismissed = 'C20' WHERE id = :i"), {"i": eid})
        try:
            problems = find_misspelled_cells(s)
            assert any("catalog_ask_log.cells_held" in p and "'B20'" in p for p in problems), problems
            assert any("access_review_dismissed" in p and "'C20'" in p for p in problems), problems
            with pytest.raises(CellSpellingError, match="level then category"):
                verify_cell_spelling(s)
        finally:
            s.rollback()
    with env["Session"]() as s:
        assert find_misspelled_cells(s) == [] and s.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_catalog_ask_log_cells_held'")).scalar() == 1


def test_scope_says_what_cells_can_query(env):
    """GET /v1/ask/scope: a server caller's "has database access?" signal — tables
    in the cells, and how many clear the trust dial."""
    from shared.models import CatalogDatabase, CatalogEntity
    ans = env["answer"]
    ans.cookies.clear()
    svc = {"X-Service-Key": "service-secret-123", "X-On-Behalf-Of": "app.user"}
    sc = ans.get("/v1/ask/scope?cells=10A", headers=svc).json()
    with env["Session"]() as s:
        expected = (s.query(CatalogEntity).join(CatalogDatabase, CatalogDatabase.id == CatalogEntity.database_id)
                    .filter(CatalogEntity.object_type.in_(("TABLE", "VIEW")), CatalogEntity.status != "STALE",
                            CatalogEntity.access_level == 10, CatalogEntity.access_category == "A").count())
    assert sc["cells"] == ["10A"] and not sc["full_access"]
    assert sc["tables"] == expected > 0
    assert sum(d["tables"] for d in sc["databases"]) == sc["tables"] and sc["tables_usable"] <= sc["tables"]
    assert any(d["database_name"] == DB_OPS for d in sc["databases"])
    none = ans.get("/v1/ask/scope", headers=svc).json()
    assert none["tables"] == 0 and none["databases"] == [] and none["cells"] == [], "no cells = no access"
    bad = ans.get("/v1/ask/scope?cells=A10", headers=svc)
    assert bad.status_code == 400 and "'A10'" in bad.json()["detail"]
    assert ans.get("/v1/ask/scope?cells=10A").status_code == 401, "no key, no session"


def test_admins_see_only_their_cells_when_admin_is_not_clearance(env, monkeypatch):
    """catalog.access.admins_see_all_data: false (production): an admin
    still manages the app, but Ask and the catalog screens show only the
    cells the admin's own roles grant."""
    api, ans = env["api"], env["answer"]
    monkeypatch.setattr(env["cfg"].catalog.access, "admins_see_all_data", False)
    tok = _admin_token(env)
    me = api.get("/v1/auth/me", headers=hdr(tok)).json()
    assert me["is_admin"] and not me["full_access"]
    held = {(g["level"], g["category"]) for g in me["grants"]}
    ents = api.get("/v1/entities?limit=500", headers=hdr(tok)).json()
    assert all((e["access_level"], e["access_category"]) in held for e in ents), "only the admin's own cells"
    if not held:   # the built-in Administrator role grants no cells
        assert ents == [], "an admin with no cells sees no tables"
    full = api.get("/v1/entities?limit=500", headers=hdr(tok)).json()
    monkeypatch.setattr(env["cfg"].catalog.access, "admins_see_all_data", True)
    assert len(api.get("/v1/entities?limit=500", headers=hdr(tok)).json()) > len(full), "true = everything"
    monkeypatch.setattr(env["cfg"].catalog.access, "admins_see_all_data", False)
    ans.cookies.clear()
    r = ans.post("/v1/ask", headers=hdr(tok), json={"question": "employees"}).json()
    assert r["access"]["full_access"] is False and r["access"]["cells"] == sorted(r["access"]["cells"], key=lambda c: (int(c[:-1]), c[-1]))
    # administration still works
    assert api.get("/v1/access/grid?kind=table", headers=hdr(tok)).status_code == 200
    assert api.get("/v1/auth/roles", headers=hdr(tok)).status_code == 200


def test_bootstrap_only_while_no_admin_exists(env, monkeypatch):
    """CATALOG_BOOTSTRAP_ADMINS makes the FIRST administrator only — once an
    active admin exists, a listed account is not auto-registered."""
    api = env["api"]
    _admin_token(env)  # an active admin exists
    monkeypatch.setattr(env["cfg"].catalog.auth, "bootstrap_admins", "Boss.Admin, Late.Comer")
    assert login(api, "Late.Comer").status_code == 401
    from shared.models import AppUser
    with env["Session"]() as s:
        assert s.query(AppUser).filter(AppUser.username.ilike("%late.comer%")).count() == 0


def test_session_cookie_secure_flag(env, monkeypatch):
    api = env["api"]
    r = login(api, "CORP\\Boss.Admin")
    assert "secure" not in r.headers["set-cookie"].lower(), "dev (http) default"
    monkeypatch.setattr(env["cfg"].catalog.auth, "cookie_secure", True)
    r = login(api, "CORP\\Boss.Admin")
    assert "secure" in r.headers["set-cookie"].lower() and "httponly" in r.headers["set-cookie"].lower()
    api.cookies.clear()
