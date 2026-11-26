"""ask log (one row per question), document exports, SQL visibility per role

- catalog_ask_log: one row per question asked — who, what, which cells it
  drew on and its classification, the tables it touched, outcome, trust.
  (catalog_ask_audit keeps one row per generated query, now linked by
  ask_id.) The answer's data rows are never stored.
- catalog_ask_exports: every document created from an answer (Create).
- app_roles.can_view_sql: members see the generated SQL under answers.

Existing catalog_ask_audit history is carried over into the log (one row
per question per person), so "recent questions" doesn't start empty.

Revision ID: 0008_ask_log_and_create
Revises: 0007_data_access_realm
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_ask_log_and_create"
down_revision: Union[str, None] = "0007_data_access_realm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OUTCOMES = ("ANSWERED", "NO_ROWS", "NOT_FOUND", "DECLINED", "BLOCKED", "ERROR", "DISABLED")


def upgrade() -> None:
    op.create_table(
        "catalog_ask_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("asked_by", sa.String(256)),
        sa.Column("mode", sa.String(16)),
        sa.Column("scope_database", sa.String(256)),
        sa.Column("database_name", sa.String(256)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("trust_label", sa.String(16)),
        sa.Column("trust_score", sa.Float()),
        sa.Column("label_level", sa.Integer()),
        sa.Column("label_category", sa.String(4)),
        sa.Column("cells_held", postgresql.ARRAY(sa.String())),
        sa.Column("cells_used", postgresql.ARRAY(sa.String())),
        sa.Column("tables", postgresql.ARRAY(sa.String())),
        sa.Column("queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("columns_withheld", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(f"outcome IN {_OUTCOMES}", name="ck_catalog_ask_log_outcome"),
    )
    op.create_index("ix_catalog_ask_log_created_at", "catalog_ask_log", ["created_at"])
    op.create_index("ix_catalog_ask_log_asked_by", "catalog_ask_log", ["asked_by", "created_at"])

    op.create_table(
        "catalog_ask_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ask_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("catalog_ask_log.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("asked_by", sa.String(256)),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("label_level", sa.Integer()),
        sa.Column("label_category", sa.String(4)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_catalog_ask_exports_ask_id", "catalog_ask_exports", ["ask_id"])
    op.create_index("ix_catalog_ask_exports_created_at", "catalog_ask_exports", ["created_at"])

    op.add_column("catalog_ask_audit", sa.Column("ask_id", postgresql.UUID(as_uuid=True)))
    op.create_index("ix_catalog_ask_audit_ask_id", "catalog_ask_audit", ["ask_id"])

    op.add_column("app_roles", sa.Column("can_view_sql", sa.Boolean(), nullable=False, server_default=sa.false()))

    _backfill()


def _backfill() -> None:
    """One log row per (question, asker) from the existing per-query audit."""
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT question, asked_by, max(database_name) AS database_name,
               bool_or(row_count > 0) AS answered,
               count(*) FILTER (WHERE generated_sql IS NOT NULL AND guardrail_verdict = 'ALLOWED') AS queries,
               count(*) FILTER (WHERE generated_sql IS NOT NULL AND guardrail_verdict = 'REJECTED') AS blocked,
               count(*) FILTER (WHERE guardrail_verdict = 'ERROR') AS failed,
               coalesce(sum(row_count), 0) AS rows,
               max(created_at) AS created_at
        FROM catalog_ask_audit
        GROUP BY question, asked_by
    """)).fetchall()
    if not rows:
        return
    log = sa.table(
        "catalog_ask_log",
        sa.column("id", postgresql.UUID(as_uuid=True)), sa.column("question"), sa.column("asked_by"),
        sa.column("mode"), sa.column("database_name"), sa.column("outcome"), sa.column("queries"),
        sa.column("queries_blocked"), sa.column("queries_failed"), sa.column("row_count"), sa.column("created_at"),
    )
    values = []
    for r in rows:
        if r.answered:
            outcome = "ANSWERED"
        elif r.queries:
            outcome = "NO_ROWS"
        elif r.blocked:
            outcome = "BLOCKED"
        elif r.failed:
            outcome = "ERROR"
        else:
            outcome = "NOT_FOUND" if r.database_name is None else "DECLINED"
        values.append({
            "id": uuid.uuid4(), "question": r.question, "asked_by": r.asked_by,
            "mode": "user" if r.asked_by else "open", "database_name": r.database_name, "outcome": outcome,
            "queries": int(r.queries or 0), "queries_blocked": int(r.blocked or 0),
            "queries_failed": int(r.failed or 0), "row_count": int(r.rows or 0), "created_at": r.created_at,
        })
    op.bulk_insert(log, values)


def downgrade() -> None:
    op.drop_column("app_roles", "can_view_sql")
    op.drop_index("ix_catalog_ask_audit_ask_id", table_name="catalog_ask_audit")
    op.drop_column("catalog_ask_audit", "ask_id")
    op.drop_table("catalog_ask_exports")
    op.drop_table("catalog_ask_log")
