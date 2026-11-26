"""Database-independence checks: the generic (SQLAlchemy) connector, the
dialect-aware profiling queries, the per-dialect row cap and prompt, all
against a real SQLite file (no server needed)."""
import sqlite3

import pytest

from answer.guardrails import validate_sql
from answer.sql_generation import system_prompt
from engine.connectors import NotReadOnlyError, QueryFailedError, make_connector
from engine.connectors.generic import GenericSourceConnector
from engine.connectors.mssql import MssqlSourceConnector
from engine.profiling import profile_column, test_value_overlap as value_overlap
from shared.config import AnswerConfig, ProfilingConfig, SourceConfig


@pytest.fixture()
def shop(tmp_path):
    path = tmp_path / "shop.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                             total REAL, status TEXT);
        CREATE INDEX ix_orders_status ON orders(status);
        CREATE VIEW big_orders AS SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id
                                  WHERE o.total > 100;
        INSERT INTO customers VALUES (1, 'Ada', 'London'), (2, 'Lin', NULL), (3, 'Omar', 'Cairo');
        INSERT INTO orders VALUES (1, 1, 50, 'paid'), (2, 1, 150, 'paid'), (3, 2, 300, 'open'), (4, 9, 10, 'open');
    """)
    con.commit()
    con.close()
    src = SourceConfig(name="shop", type="sqlite", url=f"sqlite:///{path.as_posix()}")
    return make_connector(src, statement_timeout_seconds=5)


def test_factory_picks_connector_by_type():
    assert isinstance(make_connector(SourceConfig(name="a", host="h")), MssqlSourceConnector)
    src = SourceConfig(name="b", type="postgresql", host="h", user="u", password="p")
    assert src.port == 5432 and src.dialect == "postgresql"
    assert src.sqlalchemy_url("sales").render_as_string(hide_password=True) == "postgresql+psycopg2://u:***@h:5432/sales"
    assert SourceConfig(name="c", type="mariadb", host="h").dialect == "mysql"
    assert SourceConfig(name="d", type="snowflake", url="snowflake://u@acct/db").dialect == "snowflake"


def test_metadata_has_native_row_shapes(shop):
    assert isinstance(shop, GenericSourceConnector) and shop.kind == "sqlite"
    assert shop.resolve_target_databases() == ["shop"]
    meta = shop.load_metadata("shop")
    tables = {t["object_name"]: t for t in meta["tables"]}
    assert set(tables) == {"customers", "orders"}
    assert [v["object_name"] for v in meta["views"]] == ["big_orders"]
    assert "JOIN customers" in meta["views"][0]["definition"]
    cols = [c for c in meta["columns"] if c["object_id"] == tables["customers"]["object_id"]]
    assert [(c["column_name"], c["data_type"], c["is_nullable"]) for c in cols] == [
        ("id", "integer", True), ("name", "text", False), ("city", "text", True)]
    assert {(p["object_id"], p["column_name"]) for p in meta["primary_keys"]} >= {
        (tables["orders"]["object_id"], "id")}
    fk = meta["foreign_keys"][0]
    assert (fk["parent_object_id"], fk["parent_column"], fk["referenced_object_id"], fk["referenced_column"]) == (
        tables["orders"]["object_id"], "customer_id", tables["customers"]["object_id"], "id")
    assert any(i["index_name"] == "ix_orders_status" and i["key_columns"] == "status" for i in meta["indexes"])
    view_id = meta["views"][0]["object_id"]
    assert {(d["referencing_object_id"], d["referenced_object_id"]) for d in meta["dependencies"]} == {
        (view_id, tables["orders"]["object_id"]), (view_id, tables["customers"]["object_id"])}


def test_profiling_and_overlap(shop):
    cfg = ProfilingConfig(enabled=True)
    p = profile_column(shop, "shop", "main", "customers", "city", cfg, data_type="text")
    assert p.ok and p.sampled_rows == 3 and p.null_ratio == round(1 / 3, 4) and p.distinct_estimate == 2
    assert set(p.top_values) == {"London", "Cairo"}
    o = value_overlap(shop, "shop", "main", "orders", "customer_id", "main", "customers", "id", cfg)
    assert o.ok and (o.sampled, o.matched) == (4, 3)


def test_queries_are_read_only_and_capped(shop):
    rows = shop.query_in_database("shop", "SELECT id FROM main.orders ORDER BY id", max_rows=2)
    assert [r["id"] for r in rows] == [1, 2]
    like = shop.query_in_database("shop", "SELECT name FROM main.customers WHERE name LIKE '%a%' AND '10:30' <> ''")
    assert {r["name"] for r in like} == {"Ada", "Omar"}
    with pytest.raises(NotReadOnlyError):
        shop.query_in_database("shop", "DELETE FROM orders")
    with pytest.raises(QueryFailedError):  # a second statement smuggled after a SELECT
        shop.query_in_database("shop", "WITH x AS (SELECT 1) SELECT * FROM x; DELETE FROM orders")
    # Below the guard, the session itself is read-only (PRAGMA query_only).
    with shop._engine("shop").connect() as conn, pytest.raises(Exception, match="readonly"):
        conn.exec_driver_sql("DELETE FROM orders")


@pytest.mark.parametrize("dialect,sql,expected_tail", [
    ("postgresql", "SELECT id FROM main.orders ORDER BY id", "LIMIT 200"),
    ("mysql", "SELECT id FROM main.orders LIMIT 5", "LIMIT 5"),
    ("sqlite", "SELECT id FROM main.orders LIMIT 5000", "LIMIT 200"),
    ("oracle", "SELECT id FROM main.orders", "FETCH FIRST 200 ROWS ONLY"),
    ("snowflake", 'SELECT "id" FROM "main"."orders"', "LIMIT 200"),
])
def test_row_cap_per_dialect(dialect, sql, expected_tail):
    result = validate_sql(sql, {("main", "orders")}, AnswerConfig(max_rows=200), dialect=dialect)
    assert result.ok, result.reason
    assert result.sql_to_execute.rstrip().endswith(expected_tail)
    assert "TOP" not in result.sql_to_execute


def test_mssql_cap_unchanged():
    result = validate_sql("SELECT id FROM dbo.orders", {("dbo", "orders")}, AnswerConfig(max_rows=200))
    assert result.sql_to_execute == "SELECT TOP 200 id FROM dbo.orders"


def test_backtick_and_quoted_tables_still_whitelisted():
    bad = validate_sql("SELECT * FROM `main`.`secrets`", {("main", "orders")}, AnswerConfig(), dialect="mysql")
    assert not bad.ok and "outside the grounded whitelist" in bad.reason


def test_prompt_per_dialect():
    assert "T-SQL" in system_prompt("mssql")
    for d in ("postgresql", "mysql", "oracle", "sqlite", "duckdb"):
        p = system_prompt(d)
        assert "T-SQL" not in p and "SQL Server" not in p
    assert "FETCH FIRST" in system_prompt("oracle") and "ILIKE" in system_prompt("postgresql")
