"""
Ad-hoc validation harness — NOT part of the shipped app. Exercises
engine/discovery.py's full orchestration against the real (local, test)
catalog Postgres using a FAKE connector that returns canned rows shaped
exactly like engine/sql_queries.py's real queries would, standing in
for a SQL Server instance (none is available in this build environment).
This proves the upsert/fingerprint/classification/evidence wiring is
correct; it does NOT prove the T-SQL itself is correct against a real
SQL Server (see engine/sql_queries.py's honesty note).
"""
import os
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")
os.environ.setdefault("ENV_FILE", ".env")

from shared.config import SourceConfig, load_config
from shared.db import get_session_factory
from engine.discovery import discover_source
from shared.models import CatalogEntity, CatalogField, CatalogRelationship, CatalogHistory, CatalogEvidence

TABLES_V1 = [
    {"schema_name": "dbo", "object_name": "HR_EMPLOYEE", "object_id": 101,
     "create_date": None, "modify_date": None, "row_count_estimate": 5000},
    {"schema_name": "dbo", "object_name": "ASSIGNMENTS", "object_id": 102,
     "create_date": None, "modify_date": None, "row_count_estimate": 20000},
    {"schema_name": "dbo", "object_name": "__MigrationHistory", "object_id": 103,
     "create_date": None, "modify_date": None, "row_count_estimate": 12},
]
COLUMNS_V1 = [
    {"object_id": 101, "ordinal_position": 1, "column_name": "EMP_ID", "data_type": "int",
     "max_length": 4, "precision": 10, "scale": 0, "is_nullable": False, "is_computed": False, "is_identity": True},
    {"object_id": 101, "ordinal_position": 2, "column_name": "DEP_CD", "data_type": "varchar",
     "max_length": 10, "precision": 0, "scale": 0, "is_nullable": True, "is_computed": False, "is_identity": False},
    {"object_id": 101, "ordinal_position": 3, "column_name": "FULL_NAME", "data_type": "nvarchar",
     "max_length": 200, "precision": 0, "scale": 0, "is_nullable": True, "is_computed": False, "is_identity": False},
    {"object_id": 102, "ordinal_position": 1, "column_name": "EMP_ID", "data_type": "int",
     "max_length": 4, "precision": 10, "scale": 0, "is_nullable": False, "is_computed": False, "is_identity": False},
    {"object_id": 102, "ordinal_position": 2, "column_name": "PROJECT_ID", "data_type": "int",
     "max_length": 4, "precision": 10, "scale": 0, "is_nullable": False, "is_computed": False, "is_identity": False},
    {"object_id": 102, "ordinal_position": 3, "column_name": "START_DATE", "data_type": "date",
     "max_length": 3, "precision": 0, "scale": 0, "is_nullable": True, "is_computed": False, "is_identity": False},
    {"object_id": 103, "ordinal_position": 1, "column_name": "MigrationId", "data_type": "varchar",
     "max_length": 150, "precision": 0, "scale": 0, "is_nullable": False, "is_computed": False, "is_identity": False},
]
PKS_V1 = [
    {"object_id": 101, "column_name": "EMP_ID", "key_ordinal": 1},
    {"object_id": 102, "column_name": "EMP_ID", "key_ordinal": 1},
    {"object_id": 102, "column_name": "PROJECT_ID", "key_ordinal": 2},
    {"object_id": 103, "column_name": "MigrationId", "key_ordinal": 1},
]
FKS_V1 = [
    {"fk_name": "FK_Assignments_Employee", "parent_object_id": 102, "parent_column": "EMP_ID",
     "referenced_object_id": 101, "referenced_column": "EMP_ID", "ordinal": 1},
]
INDEXES_V1 = []
VIEWS_V1 = [
    {"schema_name": "dbo", "object_name": "VW_DEPARTMENT_EMPLOYEE", "object_id": 201,
     "create_date": None, "modify_date": None,
     "definition": "CREATE VIEW dbo.VW_DEPARTMENT_EMPLOYEE AS SELECT e.*, d.DEP_NAME FROM HR_EMPLOYEE e JOIN DEPARTMENT d ON e.DEP_CD = d.DEP_CD"},
]
PROCS_V1 = []
FUNCS_V1 = []
SYNONYMS_V1 = []
DEPS_V1 = [
    {"referencing_object_id": 201, "referenced_object_id": 101},
]


from engine.connectors.mssql import MssqlSourceConnector

class FakeConnector:
    kind = "mssql"
    load_metadata = MssqlSourceConnector.load_metadata  # routes each sys.* query through query_in_database below

    def __init__(self, version="v1"):
        self.version = version

    def resolve_target_databases(self):
        return ["HR_PROD"]

    def query_in_database(self, database, sql):
        # Order matters only in that each check below must be a substring
        # unique to exactly one real query in engine/sql_queries.py -
        # verified against the actual query text (several queries share
        # "sys.columns"/"sys.indexes" as one of several joined tables).
        if "FROM sys.tables t" in sql:
            return list(TABLES_V1)
        if "JOIN sys.types ty" in sql:
            rows = [dict(r) for r in COLUMNS_V1]
            if self.version == "v2":
                rows.append({"object_id": 101, "ordinal_position": 4, "column_name": "EMAIL",
                              "data_type": "varchar", "max_length": 100, "precision": 0, "scale": 0,
                              "is_nullable": True, "is_computed": False, "is_identity": False})
            return rows
        if "i.is_primary_key = 1" in sql:
            return list(PKS_V1)
        if "FROM sys.foreign_keys fk" in sql:
            return list(FKS_V1)
        if "i.is_primary_key = 0" in sql:
            return list(INDEXES_V1)
        if "FROM sys.views v" in sql:
            return list(VIEWS_V1)
        if "FROM sys.procedures p" in sql:
            return list(PROCS_V1)
        if "'FN', 'IF', 'TF'" in sql:
            return list(FUNCS_V1)
        if "FROM sys.synonyms sy" in sql:
            return list(SYNONYMS_V1)
        if "sys.sql_expression_dependencies" in sql:
            return list(DEPS_V1)
        raise AssertionError(f"Unexpected query, no unique anchor matched: {sql[:80]}")


def main():
    cfg = load_config(force_reload=True)
    Session = get_session_factory(cfg)
    src_cfg = SourceConfig(name="primary", host="testhost", port=1433, user="x", password="x")

    print("=== Run 1 (v1) ===")
    with Session() as session:
        result = discover_source(session, src_cfg, FakeConnector("v1"), fingerprint_enabled=True)
        print(result)

    with Session() as session:
        entities = session.query(CatalogEntity).order_by(CatalogEntity.object_name).all()
        for e in entities:
            print(f"  {e.object_name:24s} type={e.object_type:10s} class={e.classification:18s} "
                  f"score={e.relevance_score} status={e.status}")
        rels = session.query(CatalogRelationship).all()
        for r in rels:
            print(f"  REL {r.relationship_label} declared_fk={r.is_declared_fk} status={r.status}")
        ev = session.query(CatalogEvidence).all()
        print(f"  evidence rows: {len(ev)}")

    print("\n=== Run 2 (v1 again, expect ~0 changed) ===")
    with Session() as session:
        result = discover_source(session, src_cfg, FakeConnector("v1"), fingerprint_enabled=True)
        print(result)

    print("\n=== Run 3 (v2: HR_EMPLOYEE gains EMAIL column, expect change detected) ===")
    with Session() as session:
        result = discover_source(session, src_cfg, FakeConnector("v2"), fingerprint_enabled=True)
        print(result)

    with Session() as session:
        emp = session.query(CatalogEntity).filter_by(object_name="HR_EMPLOYEE").one()
        fields = session.query(CatalogField).filter_by(entity_id=emp.id).order_by(CatalogField.ordinal_position).all()
        print("HR_EMPLOYEE fields after v2:", [f.column_name for f in fields])
        print("HR_EMPLOYEE status after schema change:", emp.status)
        hist = session.query(CatalogHistory).filter_by(subject_id=emp.id).all()
        print("history rows for HR_EMPLOYEE:", len(hist))
        for h in hist:
            print("  ", h.field_changed, "->", (h.new_value or "")[:16])


if __name__ == "__main__":
    main()
