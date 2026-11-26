"""
Metadata-only SQL Server system-catalog queries used by discovery.py.

Every query here reads sys.* catalog views/DMVs only — never a user
table — per the source-safety rules ("prefer sys.tables, sys.columns,
sys.indexes, sys.foreign_keys, sys.views, sys.procedures, sys.types,
DMVs — before touching actual user tables"). engine/profiling.py is the
only place that ever reads from a user table, and only in the bounded
ways described there.

Honesty note:
these are written against Microsoft's documented, stable system-catalog
shapes (sys.tables/sys.columns/sys.foreign_keys/sys.sql_modules and
friends have been stable since SQL Server 2012+), but this project was
built without a live SQL Server instance to run them against end-to-end
— verify the first real discovery run's output against SSMS on your
actual target before trusting it at face value, same as you would for
any new metadata query.
"""

SQL_TABLES = """
SELECT
    s.name          AS schema_name,
    t.name          AS object_name,
    t.object_id     AS object_id,
    t.create_date   AS create_date,
    t.modify_date   AS modify_date,
    ISNULL(p.row_count_estimate, 0) AS row_count_estimate
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
OUTER APPLY (
    SELECT SUM(ps.row_count) AS row_count_estimate
    FROM sys.dm_db_partition_stats ps
    WHERE ps.object_id = t.object_id AND ps.index_id IN (0, 1)
) p
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name
"""

SQL_COLUMNS = """
SELECT
    c.object_id       AS object_id,
    c.column_id       AS ordinal_position,
    c.name            AS column_name,
    ty.name           AS data_type,
    c.max_length      AS max_length,
    c.precision       AS precision,
    c.scale           AS scale,
    c.is_nullable     AS is_nullable,
    c.is_computed     AS is_computed,
    c.is_identity     AS is_identity
FROM sys.columns c
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
JOIN sys.objects o ON o.object_id = c.object_id
WHERE o.is_ms_shipped = 0 AND o.type IN ('U', 'V')
ORDER BY c.object_id, c.column_id
"""

SQL_PRIMARY_KEYS = """
SELECT
    ic.object_id  AS object_id,
    c.name        AS column_name,
    ic.key_ordinal AS key_ordinal
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_primary_key = 1
ORDER BY ic.object_id, ic.key_ordinal
"""

SQL_FOREIGN_KEYS = """
SELECT
    fk.name                    AS fk_name,
    fk.parent_object_id        AS parent_object_id,
    pc.name                    AS parent_column,
    fk.referenced_object_id    AS referenced_object_id,
    rc.name                    AS referenced_column,
    fkc.constraint_column_id   AS ordinal
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
ORDER BY fk.parent_object_id, fkc.constraint_column_id
"""

SQL_INDEXES = """
SELECT
    i.object_id     AS object_id,
    i.name          AS index_name,
    i.is_unique     AS is_unique,
    i.type_desc     AS index_type,
    STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS key_columns
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_primary_key = 0 AND i.name IS NOT NULL
GROUP BY i.object_id, i.name, i.is_unique, i.type_desc
"""

SQL_VIEWS = """
SELECT
    s.name        AS schema_name,
    v.name        AS object_name,
    v.object_id   AS object_id,
    v.create_date AS create_date,
    v.modify_date AS modify_date,
    m.definition  AS definition
FROM sys.views v
JOIN sys.schemas s ON s.schema_id = v.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = v.object_id
WHERE v.is_ms_shipped = 0
ORDER BY s.name, v.name
"""

SQL_PROCEDURES = """
SELECT
    s.name        AS schema_name,
    p.name        AS object_name,
    p.object_id   AS object_id,
    p.create_date AS create_date,
    p.modify_date AS modify_date,
    m.definition  AS definition
FROM sys.procedures p
JOIN sys.schemas s ON s.schema_id = p.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = p.object_id
WHERE p.is_ms_shipped = 0
ORDER BY s.name, p.name
"""

SQL_FUNCTIONS = """
SELECT
    s.name        AS schema_name,
    o.name        AS object_name,
    o.object_id   AS object_id,
    o.type_desc   AS function_type,
    o.create_date AS create_date,
    o.modify_date AS modify_date,
    m.definition  AS definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.type IN ('FN', 'IF', 'TF') AND o.is_ms_shipped = 0
ORDER BY s.name, o.name
"""

SQL_OBJECT_DEPENDENCIES = """
-- Which views/procs/functions reference which tables/views — used both
-- for relevance scoring (referenced_by_count) and as join-mining
-- evidence (engine/relationships.py mines the paired definitions for
-- predicates joining these same objects).
SELECT DISTINCT
    d.referencing_id  AS referencing_object_id,
    d.referenced_id   AS referenced_object_id
FROM sys.sql_expression_dependencies d
WHERE d.referenced_id IS NOT NULL
"""

SQL_SYNONYMS = """
SELECT
    s.name AS schema_name,
    sy.name AS object_name,
    sy.object_id AS object_id,
    sy.base_object_name AS base_object_name
FROM sys.synonyms sy
JOIN sys.schemas s ON s.schema_id = sy.schema_id
"""
