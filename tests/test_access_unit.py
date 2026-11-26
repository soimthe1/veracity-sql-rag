"""Pure unit tests (no database): lattice, naming conventions, the bucketing
precedence rules, AI review logic, and the column guardrail."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from answer.guardrails import check_columns, validate_sql
from shared.access.labeling import Resolver, ai_token
from shared.access.lattice import Category, Lattice, Level, cell_token, parse_cell
from shared.access.naming import match_category, match_level, normalize_phrase, tokens
from shared.auth.login import bootstrap_admin_names, canonical_username
from shared.config import AccessConfig, AnswerConfig, AuthConfig, ClassificationConfig


# --------------------------------------------------------------------- lattice
def lattice(**kw) -> Lattice:
    levels = [Level(v, f"L{v}", f"Level {v}", f"L{v}", "#000") for v in (10, 20, 30, 40, 50, 120)]
    cats = [Category(k, k, k, True, False) for k in "ABCDE"] + [Category("Z", "Quarantine", "Q", True, True)]
    base = dict(levels=levels, categories=cats, default_level=120, default_category="Z",
                category_names={normalize_phrase("personnel"): "C", normalize_phrase("operations"): "A"},
                level_names={normalize_phrase("general"): 10, normalize_phrase("top-secret"): 50,
                             normalize_phrase("confidential"): 20})
    base.update(kw)
    return Lattice(**base)


def test_cell_tokens_are_level_then_category():
    assert cell_token(10, "a") == "10A"
    assert cell_token(120, "Z") == "120Z"
    assert parse_cell("20B") == (20, "B")
    assert parse_cell("120Z") == (120, "Z")


@pytest.mark.parametrize("ok", ["20b", " 20B", "20B ", "\t20b\n"])
def test_cell_input_forgives_case_and_whitespace(ok):
    assert parse_cell(ok) == (20, "B")


@pytest.mark.parametrize("bad", ["B20", "b20", "A10", "20:B", "20-B", "20 B", "20", "B", "", "20BB", "12345A", "-20B", "20é"])
def test_any_other_cell_spelling_is_rejected_not_reread(bad):
    # "B20" is NOT "20B": a parser that ignored the order would silently
    # turn a wrong spelling into "no access".
    from shared.access.cells import CellError
    with pytest.raises(CellError):
        parse_cell(bad)


def test_cell_lists_and_sorting():
    from shared.access.cells import CellError, cell_text, cell_tokens, parse_cell_list, partial_cell_text
    assert parse_cell_list("20B,40A") == [(20, "B"), (40, "A")]
    assert parse_cell_list("20B, 40A,") == [(20, "B"), (40, "A")]
    assert parse_cell_list("20b,40a") == [(20, "B"), (40, "A")]
    assert parse_cell_list("") == []
    with pytest.raises(CellError, match="B20"):
        parse_cell_list("20B,B20")
    assert cell_tokens([(100, "A"), (20, "B"), (10, "A"), (20, "A")]) == ["10A", "20A", "20B", "100A"]
    assert partial_cell_text(20, None) == "20*" and partial_cell_text(None, "b") == "*B"
    assert cell_text(None, "B") == "(unlabeled)" and cell_text(40, "D") == "40D"


def test_grant_tokens_are_level_then_category():
    from shared.auth.grants import Grant, GrantSet
    gs = GrantSet.from_pairs([(40, "a"), (20, "B")])
    assert gs.to_tokens() == ["20B", "40A"]
    assert GrantSet.from_tokens(["20B"]).allows(20, "B")
    with pytest.raises(ValueError):
        Grant.parse("B20")


def test_ai_dismissal_token_is_level_first():
    assert ai_token(20, "C") == "20C" and ai_token(20, None) == "20-" and ai_token(None, "c") == "-C"


def test_cell_labels_with_old_spelling_are_a_config_error():
    from shared.access.lattice import _parse
    raw = {"levels": [{"value": 10, "key": "G", "name": "General"}], "categories": [{"key": "A", "name": "Ops"}],
           "cell_labels": {"10A": "fine"}}
    assert _parse(raw, "x.yaml").cell_label(10, "A")["name"] == "fine"
    raw["cell_labels"] = {"A10": "old spelling"}
    with pytest.raises(ValueError, match="A10"):
        _parse(raw, "x.yaml")


def test_migration_0009_rewrites_old_spellings():
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions", "0009_cell_spelling_level_first.py")
    spec = importlib.util.spec_from_file_location("m0009", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m._cell("B20") == "20B" and m._cell("20B") == "20B", "idempotent"
    assert m._history_cell("C40") == "40C" and m._history_cell("0") == "(unlabeled)" and m._history_cell("C0") == "(unlabeled)"
    assert [m._proposal(v) for v in ("C20", "C-", "-20", "--", "20C")] == ["20C", "-C", "20-", "--", "20C"]
    assert [m._partial(v) for v in ("C20", "C*", "*20", "C(keep)", "(keep)20", "(unresolved)")] == \
        ["20C", "*C", "20*", "*C", "20*", "(unresolved)"]
    assert m._cells_text("A10 D40") == "10A 40D" and m._cells_text("(none)") == "(none)"
    assert m._rule_text("database: Sales is ops -> A10") == "database: Sales is ops -> 10A"
    assert m._rule_text("table: t -> *20") == "table: t -> 20*"


def test_lattice_rules():
    lat = lattice()
    assert lat.snap_level(25) == 30 and lat.snap_level(10) == 10 and lat.snap_level(999) == 120
    assert lat.is_quarantine("Z") and not lat.is_quarantine("A")
    assert lat.is_valid_cell(10, "A", grantable=True)
    assert not lat.is_valid_cell(10, "Z", grantable=True), "quarantine is never grantable"
    assert not lat.is_valid_cell(15, "A")


def test_real_levels_file_parses():
    from shared.access.lattice import _parse
    import yaml, os
    path = os.path.join(os.path.dirname(__file__), "..", "config", "levels.yaml")
    lat = _parse(yaml.safe_load(open(path, encoding="utf-8")), path)
    assert lat.level(40).name == "Restricted"
    assert lat.default_level == 120 and lat.default_category == "Z"
    assert "Z" not in [c.key for c in lat.grantable_categories]
    assert lat.category_names["personnel"] == "C"


# ---------------------------------------------------------------------- naming
def test_tokens_and_whole_word_matching():
    assert tokens("TopSecret_HRData") == ["top", "secret", "hr", "data"]
    assert match_level("TopSecretDocs", {"top secret": 50})[0] == 50
    assert match_level("Chrome", {"hr": 20}) is None, "no substring matches"
    assert match_category("PayrollPersonnel", {"personnel": "C"})[0] == "C"
    assert match_level("general_confidential", {"general": 10, "confidential": 20})[0] == 20, "highest wins"


def test_arabic_normalization():
    assert tokens("سريّة") == tokens("سريه")


# -------------------------------------------------------------------- resolver
def rule(scope, name="r", priority=100, level=None, category=None, **pat):
    fields = dict(id=uuid4(), name=name, enabled=True, priority=priority, scope=scope, source_pattern=None,
                  database_pattern=None, schema_pattern=None, table_pattern=None, column_pattern=None,
                  object_type=None, domain_pattern=None, classification=None, sensitivity_flag=None,
                  set_level=level, set_category=category, created_at="2026-01-01")
    fields.update(pat)
    return NS(**fields)


def pin(target_type, level=None, category=None, **ids):
    return NS(target_type=target_type, level=level, category=category, reason="because", set_by="admin",
              database_id=ids.get("database_id"), schema_name=ids.get("schema_name"),
              entity_id=ids.get("entity_id"), field_id=ids.get("field_id"))


def resolver(rules=(), pins=(), lat=None, domains=None):
    access = AccessConfig(category_by_domain=domains or {"Human Resources": "C"})
    return Resolver(lat or lattice(), ClassificationConfig(), access, rules, pins)


def entity(**kw):
    base = dict(id=uuid4(), schema_name="dbo", object_name="Things", object_type="TABLE", domain=None,
                classification="BUSINESS_ENTITY", sensitivity_flags=[], access_review_dismissed=None)
    base.update(kw)
    return NS(**base)


def field(entity_row, **kw):
    base = dict(id=uuid4(), column_name="Col", sensitivity_flags=[], access_review_dismissed=None,
                _source="src", _database="DB", _schema=entity_row.schema_name, _table=entity_row.object_name)
    base.update(kw)
    return NS(**base)


CTX = ("src", "DB", "dbo", "Things")


def test_plain_column_shares_table_resolution():
    e = entity()
    r = resolver(rules=[rule("database", "db", level=20, category="A", database_pattern="*")])
    _, _, tres = chain(r, e)
    plain = r.inherited_field_resolution("dbo.Things", tres)
    assert r.resolve_field(field(e, column_name="X"), CTX, "dbo.Things", tres, plain) is plain
    flagged = r.resolve_field(field(e, column_name="X", sensitivity_flags=["PII"]), CTX, "dbo.Things", tres, plain)
    assert flagged is not plain and flagged.review


def chain(r, e, db_id=None, db_name="DB"):
    db_id = db_id or uuid4()
    d = r.resolve_database(db_id, "src", db_name)
    s = r.resolve_schema(db_id, "src", db_name, e.schema_name, d)
    return d, s, r.resolve_entity(e, "src", db_name, s)


def test_default_is_quarantine():
    _, _, res = chain(resolver(), entity())
    assert res.cell == (120, "Z") and res.level.source == "default"


def test_ai_proposal_applies_when_no_human_decision():
    e = entity(sensitivity_flags=["PII"])
    _, _, res = chain(resolver(), e)
    assert res.cell == (20, "C") and res.level.source == "ai" and res.category.source == "ai"
    assert not res.review


def test_ai_category_from_domain():
    _, _, res = chain(resolver(), entity(domain="Human Resources"))
    assert res.category.value == "C" and res.category.source == "ai"
    assert res.level.value == 120, "no level evidence -> default level"


def test_auto_naming_conventions():
    _, _, res = chain(resolver(), entity(object_name="TopSecretPlans"))
    assert res.level.value == 50 and res.level.source == "auto"
    _, s, _ = chain(resolver(), entity(schema_name="Personnel"))
    assert s.category.value == "C" and s.category.source == "auto"


def test_machine_level_takes_most_restrictive():
    # table name says 50 (auto), flags say 20 (ai): most restrictive wins
    _, _, res = chain(resolver(), entity(object_name="TopSecretPlans", sensitivity_flags=["PII"]))
    assert res.level.value == 50


def test_database_rule_inherits_down():
    db_id = uuid4()
    r = resolver(rules=[rule("database", "hr db", level=20, category="C", database_pattern="HR*")])
    _, _, res = chain(r, entity(), db_id, "HRMS")
    assert res.cell == (20, "C") and res.level.source == "inherited" and res.level.human


def test_rule_beats_inherited_and_priority_order():
    r = resolver(rules=[rule("database", "db", level=20, category="C", database_pattern="HRMS"),
                        rule("table", "salaries", level=40, table_pattern="*salar*"),
                        rule("table", "low prio", priority=1, level=10, table_pattern="*")])
    _, _, res = chain(r, entity(object_name="EmployeeSalaries"), None, "HRMS")
    assert res.cell == (40, "C") and res.level.source == "rule" and res.category.source == "inherited"


def test_manual_beats_everything_and_axes_are_independent():
    e = entity(object_name="EmployeeSalaries")
    r = resolver(rules=[rule("table", "salaries", level=40, category="C", table_pattern="*salar*")],
                 pins=[pin("entity", category="A", entity_id=e.id)])
    _, _, res = chain(r, e)
    assert res.category.value == "A" and res.category.source == "manual"
    assert res.level.value == 40 and res.level.source == "rule"


def test_ai_never_overrides_human_decision_goes_to_review():
    db_id = uuid4()
    e = entity(sensitivity_flags=["CREDENTIALS"])  # AI -> 50 (no category)
    r = resolver(pins=[pin("database", level=10, category="A", database_id=db_id)])
    _, _, res = chain(r, e, db_id)
    assert res.cell == (10, "A"), "human decision stands"
    assert res.review and "50" in res.review_reason


def test_ai_less_sensitive_than_human_is_not_a_review():
    db_id = uuid4()
    e = entity(sensitivity_flags=["PII"])  # AI -> 20C
    r = resolver(pins=[pin("database", level=40, category="C", database_id=db_id)])
    _, _, res = chain(r, e, db_id)
    assert res.cell == (40, "C") and not res.review


def test_ai_different_category_is_a_review_and_dismissal_sticks():
    db_id = uuid4()
    e = entity(sensitivity_flags=["HR"])  # AI -> 20C
    r = resolver(pins=[pin("database", level=20, category="A", database_id=db_id)])
    _, _, res = chain(r, e, db_id)
    assert res.review
    e2 = entity(id=e.id, sensitivity_flags=["HR"], access_review_dismissed=ai_token(20, "C"))
    _, _, res2 = chain(r, e2, db_id)
    assert not res2.review and res2.review_reason, "dismissed: reason kept, not pending"
    e3 = entity(id=e.id, sensitivity_flags=["HEALTH"], access_review_dismissed=ai_token(20, "C"))
    _, _, res3 = chain(r, e3, db_id)
    assert res3.review, "a changed AI proposal re-opens the review"


def test_column_inherits_table_and_can_be_stricter():
    e = entity()
    r = resolver(rules=[rule("database", "db", level=20, category="A", database_pattern="*"),
                        rule("column", "passwords", level=50, category="D", column_pattern="*password*")])
    _, _, tres = chain(r, e)
    plain = r.resolve_field(field(e, column_name="Name"), CTX, "dbo.Things", tres)
    secret = r.resolve_field(field(e, column_name="PasswordHash"), CTX, "dbo.Things", tres)
    assert plain.cell == (20, "A") and plain.level.source == "inherited"
    assert secret.cell == (50, "D") and secret.level.source == "rule"


def test_column_ai_under_human_table_goes_to_review():
    e = entity()
    r = resolver(pins=[pin("entity", level=10, category="A", entity_id=e.id)])
    _, _, tres = chain(r, e)
    f = r.resolve_field(field(e, column_name="Ssn", sensitivity_flags=["PII"]), CTX, "dbo.Things", tres)
    assert f.cell == (10, "A") and f.review


def test_disabled_rule_and_comma_patterns():
    r = resolver(rules=[rule("table", "off", level=50, table_pattern="*", enabled=False),
                        rule("table", "two", level=30, table_pattern="Pay*, *Salary")])
    assert chain(r, entity(object_name="PayRuns"))[2].level.value == 30
    assert chain(r, entity(object_name="BaseSalary"))[2].level.value == 30
    assert chain(r, entity(object_name="Other"))[2].level.value == 120


def test_rule_scope_ignores_inapplicable_patterns():
    # a database rule with a table pattern set: the table pattern is ignored
    r = resolver(rules=[rule("database", "db", level=20, database_pattern="X", table_pattern="nope")])
    d = r.resolve_database(uuid4(), "src", "X")
    assert d.level.value == 20


def test_explain_candidates_mark_winner():
    from shared.access.labeling import explain_resolution
    e = entity(object_name="TopSecretPlans", sensitivity_flags=["PII"])
    out = explain_resolution(chain(resolver(), e)[2])
    winners = [c for c in out["level"]["candidates"] if c["won"]]
    assert len(winners) == 1 and winners[0]["value"] == 50


# ------------------------------------------------------------------- guardrail
D = {("dbo", "employees"): {"salary"}}
A = {("dbo", "employees"): {"id", "name"}, ("dbo", "dept"): {"id", "salary"}}


@pytest.mark.parametrize("sql,blocked", [
    ("SELECT Name FROM dbo.Employees", False),
    ("SELECT Salary FROM dbo.Employees", True),
    ("SELECT e.Salary FROM dbo.Employees e", True),
    ("SELECT e.[Salary] FROM [dbo].[Employees] AS e", True),
    ("SELECT * FROM dbo.Employees", True),
    ("SELECT e.* FROM dbo.Employees e", True),
    ("SELECT COUNT(*) FROM dbo.Employees", False),
    ("SELECT MAX(Salary) m FROM dbo.Employees", True),
    ("SELECT e.Name, d.Salary FROM dbo.Employees e JOIN dbo.Dept d ON d.Id = e.Id", False),
    ("SELECT e.Name FROM dbo.Employees e WHERE e.Salary > 1", True),
    ("SELECT Name FROM dbo.Employees WHERE Name = 'Salary'", False),
    ("WITH x AS (SELECT Salary FROM dbo.Employees) SELECT * FROM x", True),
    ("SELECT Name, Salary2 FROM dbo.Employees", False),
])
def test_column_guardrail(sql, blocked):
    assert (check_columns(sql, D, A) is not None) == blocked


def test_validate_sql_passes_columns_through():
    wl = {("dbo", "employees")}
    assert not validate_sql("SELECT Salary FROM dbo.Employees", wl, AnswerConfig(), D, A).ok
    ok = validate_sql("SELECT Name FROM dbo.Employees", wl, AnswerConfig(), D, A)
    assert ok.ok and "TOP" in ok.sql_to_execute


# ------------------------------------------------------------------- bootstrap
def test_bootstrap_names():
    cfg = AuthConfig(bootstrap_admins="CORP\\Jane.Doe, jdoe@corp.local;  admin2")
    assert bootstrap_admin_names(cfg) == {"jane.doe", "jdoe", "admin2"}
    assert canonical_username("CORP\\X") == "x"
    assert bootstrap_admin_names(AuthConfig()) == set()


def test_db_cell_constraints_match_migration_0010():
    import importlib.util
    import os
    from shared.models import CELL_CHECKS
    path = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions", "0010_cell_check_constraints.py")
    spec = importlib.util.spec_from_file_location("m0010", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.CHECKS == CELL_CHECKS


def test_cors_wildcard_is_refused_with_cookies():
    from pydantic import ValidationError
    from shared.config import AnswerConfig, ApiConfig
    for cls in (ApiConfig, AnswerConfig):
        with pytest.raises(ValidationError, match="may not be"):
            cls(cors_origins=["*"])
        assert cls(cors_origins=["https://ui.example/", "", " "]).cors_origins == ["https://ui.example"]
        assert "*" not in cls().cors_origins


def test_ldap_tls_verification():
    import ssl
    from shared.auth.ad_auth import _tls_config
    from shared.config import LdapConfig
    assert _tls_config(LdapConfig(), use_ssl=False) is None, "plain ldap, no STARTTLS"
    assert _tls_config(LdapConfig(), use_ssl=True).validate == ssl.CERT_NONE, "dev default"
    tls = _tls_config(LdapConfig(tls_verify=True, use_starttls=True), use_ssl=False)
    assert tls is not None and tls.validate == ssl.CERT_REQUIRED, "STARTTLS is verified too"
    assert _tls_config(LdapConfig(tls_verify=True), use_ssl=True).validate == ssl.CERT_REQUIRED


def test_production_config_is_locked_down(monkeypatch):
    # Production runs on config.yaml with config.prod.yaml merged over it, so check the merged result.
    import os
    from shared.config import load_config
    monkeypatch.setenv("APP_ENV", "prod")
    path = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")
    cat = load_config(path, force_reload=True).catalog
    load_config(force_reload=True)   # leave the development config cached for other tests
    assert cat.auth.enabled is True
    assert cat.auth.cookie_secure is True
    assert cat.auth.ldap.tls_verify is True
    assert cat.access.admins_see_all_data is False
    assert cat.answer.require_grants is True
    for svc in (cat.api, cat.answer):
        assert "*" not in svc.cors_origins


def test_config_yaml_refuses_secrets_and_env_overrides():
    from shared.config import ConfigError, _check_secrets
    _check_secrets({"database": {"password": "${CATALOG_DB_PASSWORD}", "host": "db"}, "jwt_secret_env_var": "X"})
    with pytest.raises(ConfigError, match="database.password"):
        _check_secrets({"database": {"password": "hunter2"}})
    with pytest.raises(ConfigError, match="database.host"):
        _check_secrets({"database": {"host": "${CATALOG_DB_HOST}"}})


@pytest.mark.parametrize("raw", [
    {"level": 20, "category": "B"}, {"level": 20, "category": "b"}, {"level": "20", "category": " b "},
    {"level": 20.0, "category": "B"}, "20B", "20b", " 20b ",
])
def test_structured_grants_from_server_caller_are_normalised(raw):
    from answer.schemas import AskIn, GrantIn
    from api.routers.auth import RoleGrantIn
    assert GrantIn.model_validate(raw).model_dump() == {"level": 20, "category": "B"}
    assert RoleGrantIn.model_validate(raw).model_dump() == {"level": 20, "category": "B"}
    assert AskIn(question="q", grants=[raw]).grants[0].category == "B"


@pytest.mark.parametrize("raw", [
    {"level": 20, "category": "BB"}, {"level": 20, "category": ""}, {"level": 20, "category": None},
    {"level": 20, "category": "1"}, {"level": 20}, {"category": "B"}, {"level": -1, "category": "B"},
    {"level": 10000, "category": "B"}, {"level": 20.5, "category": "B"}, {"level": True, "category": "B"},
    {"level": "x", "category": "B"}, "B20", "20", "", 20,
])
def test_malformed_grants_are_rejected_not_dropped(raw):
    # A dropped grant is a silent "no access"; a 422 names the problem.
    import pydantic
    from answer.schemas import GrantIn
    with pytest.raises(pydantic.ValidationError):
        GrantIn.model_validate(raw)


def test_search_terms_normalise_arabic():
    from api.search.postgres_search import normalize_arabic, query_terms
    assert normalize_arabic("إدارة") == "اداره" and normalize_arabic("مُوَظَّف") == "موظف"
    # filler dropped (both hamza spellings), article stripped, variants unified
    assert query_terms("أخبرني عن الموظفين") == ["موظفين"]
    assert query_terms("اخبرني عن إدارة الموارد") == ["اداره", "موارد"]
    assert query_terms("tell me about employee qualification") == ["employee", "qualification"]
