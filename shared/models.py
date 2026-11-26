"""
ORM models for the catalog's OWN PostgreSQL database. This schema is
intentionally separate from — and knows nothing about — any monitored
SQL Server application database, and never lives inside another
application's database.

Design notes:
- Normal relational columns for anything a consumer would filter/join/
  search on. JSONB only for genuinely flexible evidence/properties
  payloads (spec section 3) — never an opaque blob covering everything.
- Status/trust-state columns are plain VARCHAR + an application-level
  allowed-values tuple (STATUS_VALUES below), not a native Postgres ENUM.
  A Postgres ENUM's ALTER TYPE ... ADD VALUE story is exactly the kind of
  migration friction this project is trying to avoid — a plain
  column plus an Alembic-tracked CHECK constraint is just as safe and
  much easier to evolve.
- Every table that represents a "belief" (entity, field, relationship)
  carries first_seen_at / last_seen_at / last_verified_at / confidence /
  status / evidence_count, per spec section 12, without a full event-
  sourcing architecture — CatalogHistory carries the append-only trail
  of *changes* to those beliefs, which is enough to answer "what did the
  catalog believe yesterday and why did it change" without duplicating
  every unchanged row on every cycle.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# Shared trust-state vocabulary (spec section 10).
STATUS_VALUES = (
    "DISCOVERED",   # seen once, nothing evaluated yet
    "LEARNING",     # under active evidence gathering, confidence not settled
    "TRUSTED",      # confidence >= trusted_threshold, safe for autonomous use
    "SUSPECT",      # confidence fell after previously being higher
    "STALE",        # source unreachable; last-known-good retained, not deleted
    "DEGRADED",     # profiling/validation failing for this object specifically
    "DISABLED",     # source or object explicitly disabled
    "UNKNOWN",      # confidence never converged; excluded from autonomous use
)

OBJECT_TYPES = ("TABLE", "VIEW", "PROCEDURE", "FUNCTION", "SYNONYM")

CLASSIFICATIONS = (
    "BUSINESS_ENTITY",
    "TRANSACTIONAL_DATA",
    "REFERENCE_DATA",
    "RELATIONSHIP_TABLE",
    "AUDIT_HISTORY",
    "SYSTEM_TECHNICAL",
    "STAGING",
    "TEMPORARY",
    "ARCHIVE",
    "LOGGING",
    "CONFIGURATION",
    "UNKNOWN",
)

SOURCE_STATUS_VALUES = ("DISCOVERED", "HEALTHY", "DEGRADED", "UNAVAILABLE", "DISABLED")

# Ask-audit guardrail verdicts (answer/ process — see
# docs/integration.md). ALLOWED = SQL passed guardrails and ran;
# REJECTED = guardrails or the access filter refused it before execution;
# ERROR = passed guardrails but the source/query itself failed.
ASK_AUDIT_VERDICTS = ("ALLOWED", "REJECTED", "ERROR")

EVIDENCE_TYPES = (
    "explicit_fk",
    "pk_fk_datatype_compatible",
    "value_overlap",
    "uniqueness_cardinality",
    "view_definition_confirms",
    "procedure_usage_confirms",
    "executed_join_evidence",
    "naming_similarity",
    "llm_semantic_interpretation",
    "contradictory_evidence",
    # Entity-level evidence (engine/confidence.py's
    # record_entity_structural_evidence) — added after the table's own
    # CHECK CONSTRAINT was first created, so this constant alone was
    # never enough; see migrations/versions/0004_entity_structural_evidence_types.py,
    # which actually altered the live constraint. Keep this tuple and
    # that migration's NEW_EVIDENCE_TYPES in sync if either changes again.
    "structural_completeness",
    "relationship_participation",
)


def _uuid_col():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class CatalogSource(Base):
    __tablename__ = "catalog_sources"

    id = _uuid_col()
    name = Column(String(128), nullable=False, unique=True)
    host = Column(String(256), nullable=False)
    port = Column(Integer, nullable=False, default=1433)
    status = Column(String(32), nullable=False, default="DISCOVERED")
    enabled = Column(Boolean, nullable=False, default=True)
    last_discovery_at = Column(DateTime(timezone=True))
    last_validation_at = Column(DateTime(timezone=True))
    last_error = Column(Text)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    databases = relationship("CatalogDatabase", back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(f"status IN {SOURCE_STATUS_VALUES}", name="ck_catalog_sources_status"),
    )


class CatalogDatabase(Base):
    __tablename__ = "catalog_databases"

    id = _uuid_col()
    source_id = Column(UUID(as_uuid=True), ForeignKey("catalog_sources.id", ondelete="CASCADE"), nullable=False)
    database_name = Column(String(256), nullable=False)
    is_online = Column(Boolean, nullable=False, default=True)
    schema_fingerprint = Column(String(64))
    entity_count = Column(Integer, nullable=False, default=0)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_verified_at = Column(DateTime(timezone=True))

    source = relationship("CatalogSource", back_populates="databases")
    entities = relationship("CatalogEntity", back_populates="database", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_id", "database_name", name="uq_catalog_databases_source_db"),
    )


class CatalogEntity(Base):
    """A physical object (table/view/procedure/function/synonym) plus the
    catalog's inferred semantic interpretation of it. Level-1-only objects
    have semantic_name/description NULL and status DISCOVERED; Level-2
    (deep_analysis=true) objects get everything else filled in as evidence
    accumulates."""
    __tablename__ = "catalog_entities"

    id = _uuid_col()
    database_id = Column(UUID(as_uuid=True), ForeignKey("catalog_databases.id", ondelete="CASCADE"), nullable=False)
    schema_name = Column(String(128), nullable=False)
    object_name = Column(String(256), nullable=False)
    object_type = Column(String(16), nullable=False)

    classification = Column(String(32), nullable=False, default="UNKNOWN")
    relevance_score = Column(Float, nullable=False, default=0.0)
    deep_analysis = Column(Boolean, nullable=False, default=False)

    semantic_name = Column(String(256))
    description = Column(Text)
    domain = Column(String(128))
    synonyms = Column(ARRAY(String))

    primary_key_fields = Column(ARRAY(String))
    row_count_estimate = Column(Integer)

    confidence = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="DISCOVERED")
    evidence_count = Column(Integer, nullable=False, default=0)

    sensitivity_flags = Column(ARRAY(String))  # e.g. PII, HEALTH, FINANCIAL, HR, SECURITY
    definition_hash = Column(String(64))       # view/proc/function body fingerprint
    object_fingerprint = Column(String(64))    # column-structure fingerprint (tables)

    # Provisional access classification. Derived today
    # from sensitivity_flags by engine/access_classification.py's shim —
    # never hand-set, never itself a security boundary yet.
    # access_classification_provisional=True means exactly that: "inferred
    # from a flag, not from a real governance decision." These three
    # columns exist now, ahead of a proper permission system, specifically
    # so that system's arrival needs no migration — only provisional
    # flips to False and the shim stops writing these fields. Exact-match
    # (level, category), no inheritance, on purpose — a
    # consumer joins on these two columns exactly like it would for
    # classified documents, never a new shape to learn.
    access_level = Column(Integer)
    access_category = Column(String(4))
    access_classification_provisional = Column(Boolean, nullable=False, default=True)
    # How the cell above was decided (shared/access/labeling.py), per axis:
    # manual | rule | inherited | ai | auto | default. access_detail holds
    # the human-readable "why" (which rule, which parent, which flags).
    access_level_source = Column(String(16))
    access_category_source = Column(String(16))
    access_detail = Column(JSONB, nullable=False, default=dict)
    # The AI layer's own proposal, kept even when a human decision wins, so
    # a disagreement can be shown for review.
    access_ai_level = Column(Integer)
    access_ai_category = Column(String(4))
    access_review_pending = Column(Boolean, nullable=False, default=False)
    access_review_dismissed = Column(String(16))  # AI proposal a reviewer kept the current cell over: "40C" ("40-"/"-C" for one axis)
    # Hash of all the access_* columns above — lets a recompute skip
    # unchanged rows without reading and comparing access_detail.
    access_signature = Column(String(32))

    # Demand-driven coverage feedback loop (self-correcting — see
    # engine/coverage.py's docstring and CoverageConfig.demand_*). Bumped
    # by answer/grounding.py whenever this entity is a search hit for a
    # question but gets excluded from grounding because it isn't TRUSTED
    # yet. Reset to 0 by engine/coverage.py the cycle after the entity
    # reaches TRUSTED — the demand was the signal, satisfying it clears it.
    unanswered_hit_count = Column(Integer, nullable=False, default=0)
    last_unanswered_at = Column(DateTime(timezone=True))

    properties = Column(JSONB, nullable=False, default=dict)  # flexible extras only

    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_verified_at = Column(DateTime(timezone=True))

    database = relationship("CatalogDatabase", back_populates="entities")
    fields = relationship("CatalogField", back_populates="entity", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("database_id", "schema_name", "object_name", name="uq_catalog_entities_object"),
        CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_entities_status"),
        CheckConstraint(f"object_type IN {OBJECT_TYPES}", name="ck_catalog_entities_object_type"),
        CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="ck_catalog_entities_classification"),
        Index("ix_catalog_entities_semantic_name", "semantic_name"),
        Index("ix_catalog_entities_domain", "domain"),
        Index("ix_catalog_entities_status", "status"),
        Index("ix_catalog_entities_deep_analysis", "deep_analysis"),
        # A VALUES-
        # join grant check on (level, category) wants exactly this index.
        Index("ix_catalog_entities_access", "access_level", "access_category"),
        # Backs the Suggestions tab's "what's everyone asking for that we
        # can't answer yet" view — sorted by demand, descending.
        Index("ix_catalog_entities_unanswered", "unanswered_hit_count"),
    )


class CatalogField(Base):
    __tablename__ = "catalog_fields"

    id = _uuid_col()
    entity_id = Column(UUID(as_uuid=True), ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False)
    column_name = Column(String(256), nullable=False)
    ordinal_position = Column(Integer, nullable=False, default=0)
    data_type = Column(String(64), nullable=False)
    max_length = Column(Integer)
    is_nullable = Column(Boolean, nullable=False, default=True)
    is_primary_key = Column(Boolean, nullable=False, default=False)
    is_computed = Column(Boolean, nullable=False, default=False)

    semantic_name = Column(String(256))
    description = Column(Text)

    null_ratio = Column(Float)          # bounded profiling result, nullable until profiled
    distinct_estimate = Column(Integer)
    sample_min = Column(String(256))
    sample_max = Column(String(256))
    top_values = Column(ARRAY(String))  # bounded, never full column contents

    confidence = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="DISCOVERED")
    sensitivity_flags = Column(ARRAY(String))

    # Same provisional shim as CatalogEntity (see there for the full
    # rationale) — a field can carry a stricter classification than its
    # parent entity (e.g. a HEALTH column on an otherwise general-purpose
    # table); it is never looser than the entity's own.
    access_level = Column(Integer)
    access_category = Column(String(4))
    access_classification_provisional = Column(Boolean, nullable=False, default=True)
    access_level_source = Column(String(16))
    access_category_source = Column(String(16))
    access_detail = Column(JSONB, nullable=False, default=dict)
    access_ai_level = Column(Integer)
    access_ai_category = Column(String(4))
    access_review_pending = Column(Boolean, nullable=False, default=False)
    access_review_dismissed = Column(String(16))
    access_signature = Column(String(32))

    properties = Column(JSONB, nullable=False, default=dict)

    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_verified_at = Column(DateTime(timezone=True))

    entity = relationship("CatalogEntity", back_populates="fields")

    __table_args__ = (
        UniqueConstraint("entity_id", "column_name", name="uq_catalog_fields_column"),
        CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_fields_status"),
        Index("ix_catalog_fields_semantic_name", "semantic_name"),
        Index("ix_catalog_fields_access", "access_level", "access_category"),
        Index("ix_catalog_fields_entity", "entity_id"),
    )


class CatalogRelationship(Base):
    __tablename__ = "catalog_relationships"

    id = _uuid_col()
    from_entity_id = Column(UUID(as_uuid=True), ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False)
    from_field_id = Column(UUID(as_uuid=True), ForeignKey("catalog_fields.id", ondelete="SET NULL"))
    to_entity_id = Column(UUID(as_uuid=True), ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False)
    to_field_id = Column(UUID(as_uuid=True), ForeignKey("catalog_fields.id", ondelete="SET NULL"))

    relationship_label = Column(String(256))     # e.g. "Employee has Assignments"
    is_declared_fk = Column(Boolean, nullable=False, default=False)

    confidence = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="DISCOVERED")
    evidence_count = Column(Integer, nullable=False, default=0)
    evidence_summary = Column(JSONB, nullable=False, default=dict)

    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_verified_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("from_entity_id", "from_field_id", "to_entity_id", "to_field_id",
                          name="uq_catalog_relationships_edge"),
        CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_relationships_status"),
        Index("ix_catalog_relationships_from", "from_entity_id"),
        Index("ix_catalog_relationships_to", "to_entity_id"),
    )


class CatalogJoinPath(Base):
    """Precomputed multi-hop join paths (spec: autonomous join-path
    discovery), so a consumer doesn't have to walk catalog_relationships
    itself for common traversals. Recomputed incrementally when the
    relationships along a path change confidence/status, not every cycle."""
    __tablename__ = "catalog_join_paths"

    id = _uuid_col()
    from_entity_id = Column(UUID(as_uuid=True), ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False)
    to_entity_id = Column(UUID(as_uuid=True), ForeignKey("catalog_entities.id", ondelete="CASCADE"), nullable=False)
    hop_count = Column(Integer, nullable=False)
    path = Column(JSONB, nullable=False)  # ordered list of relationship ids + entity ids
    confidence = Column(Float, nullable=False, default=0.0)  # min confidence along the path
    status = Column(String(32), nullable=False, default="LEARNING")
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_verified_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_catalog_join_paths_from_to", "from_entity_id", "to_entity_id"),
        CheckConstraint(f"status IN {STATUS_VALUES}", name="ck_catalog_join_paths_status"),
    )


class CatalogEvidence(Base):
    """One row per piece of evidence contributing to a confidence score.
    subject_type/subject_id point at whichever table the evidence is
    about (entity/field/relationship) — kept generic rather than three
    near-identical evidence tables, per spec's "keep this lightweight"."""
    __tablename__ = "catalog_evidence"

    id = _uuid_col()
    subject_type = Column(String(16), nullable=False)  # entity | field | relationship
    subject_id = Column(UUID(as_uuid=True), nullable=False)
    evidence_type = Column(String(64), nullable=False)
    weight_applied = Column(Float, nullable=False, default=0.0)
    detail = Column(JSONB, nullable=False, default=dict)
    observed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("subject_type IN ('entity','field','relationship')",
                         name="ck_catalog_evidence_subject_type"),
        CheckConstraint(f"evidence_type IN {EVIDENCE_TYPES}", name="ck_catalog_evidence_type"),
        Index("ix_catalog_evidence_subject", "subject_type", "subject_id"),
    )


class CatalogObservation(Base):
    """Lightweight, BOUNDED profiling/workload observations (null ratio,
    distinct estimate, top-N values, join frequency, filter frequency).
    Never a copy of business data ("source data is live — catalog must remain current" / STRUCTURAL +
    SEMANTIC knowledge only, never LIVE BUSINESS DATA)."""
    __tablename__ = "catalog_observations"

    id = _uuid_col()
    subject_type = Column(String(16), nullable=False)  # entity | field
    subject_id = Column(UUID(as_uuid=True), nullable=False)
    observation_type = Column(String(64), nullable=False)  # profiling | workload_join | workload_filter
    detail = Column(JSONB, nullable=False, default=dict)
    observed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_catalog_observations_subject", "subject_type", "subject_id"),
    )


class CatalogHistory(Base):
    """Append-only trail of BELIEF CHANGES (not every unchanged cycle) —
    answers "what did the catalog believe yesterday, and why did it
    change" per spec section 12, without full event-sourcing."""
    __tablename__ = "catalog_history"

    id = _uuid_col()
    subject_type = Column(String(16), nullable=False)  # entity | field | relationship
    subject_id = Column(UUID(as_uuid=True), nullable=False)
    field_changed = Column(String(64), nullable=False)  # e.g. "semantic_name", "confidence", "status"
    old_value = Column(Text)
    new_value = Column(Text)
    old_confidence = Column(Float)
    new_confidence = Column(Float)
    reason = Column(Text)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_catalog_history_subject", "subject_type", "subject_id"),
        Index("ix_catalog_history_changed_at", "changed_at"),
    )


class CatalogAskAudit(Base):
    """One row per /v1/ask call handled by the answer/ process (spec
    addendum: "fully autonomous, but tightly bounded" — bounded means
    every generated-and-executed query is logged here, always, whether
    it ran or was refused). This is the audit trail a security review
    of the "fully autonomous SQL generation" feature will ask for
    first; it is never optional and never best-effort like
    CatalogEvidence/CatalogObservation are."""
    __tablename__ = "catalog_ask_audit"

    id = _uuid_col()
    question = Column(Text, nullable=False)
    requested_grants = Column(JSONB)          # caller's [[level, category], ...], as received
    source_name = Column(String(128))
    database_name = Column(String(256))
    generated_sql = Column(Text)
    entities_touched = Column(ARRAY(String))  # ["schema.object", ...] from the grounding whitelist
    guardrail_verdict = Column(String(32), nullable=False)
    rejection_reason = Column(Text)
    row_count = Column(Integer)
    duration_ms = Column(Integer)
    error = Column(Text)
    # Who asked (canonical username) when catalog.auth is on; NULL when
    # auth is off or a trusted server caller asked.
    asked_by = Column(String(256))
    # The question (catalog_ask_log row) this query belongs to — a single
    # question can run several queries.
    ask_id = Column(UUID(as_uuid=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"guardrail_verdict IN {ASK_AUDIT_VERDICTS}", name="ck_catalog_ask_audit_verdict"),
        Index("ix_catalog_ask_audit_ask_id", "ask_id"),
        Index("ix_catalog_ask_audit_created_at", "created_at"),
        Index("ix_catalog_ask_audit_asked_by", "asked_by"),
    )


ASK_OUTCOMES = ("ANSWERED", "NO_ROWS", "NOT_FOUND", "DECLINED", "BLOCKED", "ERROR", "DISABLED")


class CatalogAskLog(Base):
    """One row per question asked (catalog_ask_audit has one per generated
    query). What an auditor needs to see who asked what and what it
    touched: the asker, their cells, the cells and tables the answer drew
    on and its classification, the outcome and trust. The answer's data
    rows are deliberately NOT stored — that would be a second copy of
    sensitive data."""
    __tablename__ = "catalog_ask_log"

    id = _uuid_col()
    question = Column(Text, nullable=False)
    asked_by = Column(String(256))
    mode = Column(String(16))                  # open | user | service
    scope_database = Column(String(256))       # the database the asker picked, if any
    database_name = Column(String(256))        # the database that answered
    outcome = Column(String(16), nullable=False)
    trust_label = Column(String(16))
    trust_score = Column(Float)
    label_level = Column(Integer)              # classification: most sensitive cell used
    label_category = Column(String(4))
    cells_held = Column(ARRAY(String))
    cells_used = Column(ARRAY(String))
    tables = Column(ARRAY(String))
    queries = Column(Integer, nullable=False, default=0)
    queries_blocked = Column(Integer, nullable=False, default=0)
    queries_failed = Column(Integer, nullable=False, default=0)
    row_count = Column(Integer, nullable=False, default=0)
    columns_withheld = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"outcome IN {ASK_OUTCOMES}", name="ck_catalog_ask_log_outcome"),
        Index("ix_catalog_ask_log_created_at", "created_at"),
        Index("ix_catalog_ask_log_asked_by", "asked_by", "created_at"),
    )


class CatalogAskExport(Base):
    """Every document created from an answer (Create: email, Word,
    PowerPoint, PDF) — who, which answer, which format, at which
    classification."""
    __tablename__ = "catalog_ask_exports"

    id = _uuid_col()
    ask_id = Column(UUID(as_uuid=True), ForeignKey("catalog_ask_log.id", ondelete="CASCADE"), nullable=False)
    asked_by = Column(String(256))
    format = Column(String(16), nullable=False)
    label_level = Column(Integer)
    label_category = Column(String(4))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_catalog_ask_exports_ask_id", "ask_id"),
        Index("ix_catalog_ask_exports_created_at", "created_at"),
    )


class AppRole(Base):
    """A named bundle of (level, category) grants for THIS project's own
    web UI — roles are the grant-bearing unit, never individual users,
    on purpose ("grants
    attach to ROLES not people"). is_admin is a role PROPERTY, fully
    orthogonal to read clearance ("admin-is-a-role-not-a-clearance") — an admin role can legitimately hold zero grant cells;
    it manages users/roles/sources, it does not thereby see restricted
    catalog data."""
    __tablename__ = "app_roles"

    id = _uuid_col()
    name = Column(String(128), nullable=False, unique=True)
    description = Column(Text)
    is_admin = Column(Boolean, nullable=False, default=False)
    # The built-in Administrator role (seeded by migration 0007): cannot be
    # deleted or lose its admin flag, so the app can never be left with no
    # way to manage it.
    is_system = Column(Boolean, nullable=False, default=False)
    # Members see the SQL generated for their answers ("Queries run").
    # Administrators always do.
    can_view_sql = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    grants = relationship("AppRoleGrant", back_populates="role", cascade="all, delete-orphan")
    user_links = relationship("AppUserRole", back_populates="role", cascade="all, delete-orphan")


class AppRoleGrant(Base):
    """One (level, category) cell granted to a role — exact-match, no
    inheritance on either axis (shared/auth/grants.py's
    GrantSet.allows() is the only place this is ever evaluated)."""
    __tablename__ = "app_role_grants"

    id = _uuid_col()
    role_id = Column(UUID(as_uuid=True), ForeignKey("app_roles.id", ondelete="CASCADE"), nullable=False)
    level = Column(Integer, nullable=False)
    category = Column(String(4), nullable=False)

    role = relationship("AppRole", back_populates="grants")

    __table_args__ = (
        UniqueConstraint("role_id", "level", "category", name="uq_app_role_grants_cell"),
        Index("ix_app_role_grants_role", "role_id"),
    )


class AppUser(Base):
    """A registered user of this project's own web UI. AD proves
    identity; this table decides whether that identity is allowed in at
    all and, if so, with which roles — login.py's "no self-provisioning"
    rule means a row here must exist (created by an admin) before an
    otherwise-valid AD account can sign in. username is the canonical-
    folded form (shared/auth/login.py's canonical_username — lowercased,
    DOMAIN\\ and @domain stripped) so the same AD account can never
    create a second shadow row."""
    __tablename__ = "app_users"

    id = _uuid_col()
    username = Column(String(256), nullable=False, unique=True)
    display_name = Column(String(256))
    is_active = Column(Boolean, nullable=False, default=True)
    # Local accounts only (catalog.auth.mode: local); NULL for LDAP accounts.
    password_hash = Column(String(512))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login_at = Column(DateTime(timezone=True))

    roles = relationship("AppUserRole", back_populates="user", cascade="all, delete-orphan")


class AppUserRole(Base):
    """User <-> role membership (many-to-many). Roles combine by union
    when resolving a principal's effective grants (login.py's
    resolve_principal)."""
    __tablename__ = "app_user_roles"

    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("app_roles.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("AppUser", back_populates="roles")
    role = relationship("AppRole", back_populates="user_links")


class AppLoginAudit(Base):
    """Every login attempt against this app's own AD-backed login,
    success or failure — this project's "full audit trail" habit
    (CatalogAskAudit is the other example) applied to authentication.
    Never records the password; reason is a short, operator-facing
    string (e.g. "AD bind failed", "not registered or inactive") — the
    user-facing error message stays generic (anti-enumeration) even
    though the audit trail itself is precise."""
    __tablename__ = "app_login_audit"

    id = _uuid_col()
    username_attempted = Column(String(256), nullable=False)
    success = Column(Boolean, nullable=False)
    reason = Column(String(256))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_app_login_audit_created_at", "created_at"),
        Index("ix_app_login_audit_username", "username_attempted"),
    )


class CatalogHealth(Base):
    """Small, mostly-single-row-per-component status table backing the
    external status widgets and this project's own status UI. Not an
    events table — upserted in place per component."""
    __tablename__ = "catalog_health"

    id = _uuid_col()
    component = Column(String(64), nullable=False, unique=True)  # engine | api | llm | source:<name>
    status = Column(String(32), nullable=False, default="DISCOVERED")
    detail = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogSetting(Base):
    """Small, generic key -> value store for the handful of knobs the
    web UI is meant to control live, without editing config.yaml or
    restarting a process (see shared/settings.py). config.yaml still
    defines each knob's DEFAULT (e.g. AnswerConfig.min_trust_level) —
    this table only ever holds an override once someone has actually
    touched the control; a missing key means "use the YAML default",
    same upsert-in-place shape as CatalogHealth above, not an events
    table."""
    __tablename__ = "catalog_settings"

    key = Column(String(128), primary_key=True)
    value = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


ACCESS_TARGET_TYPES = ("database", "schema", "entity", "field")
ACCESS_RULE_SCOPES = ("database", "schema", "table", "column")


class CatalogAccessLabel(Base):
    """A MANUAL placement ("pin") of one database, schema, table or column
    into a cell — the strongest bucketing layer (shared/access/labeling.py).
    Survives every re-scan until someone resets it to automatic. Either axis
    may be left NULL to pin just the other one."""
    __tablename__ = "catalog_access_labels"

    id = _uuid_col()
    target_type = Column(String(16), nullable=False)
    database_id = Column(UUID(as_uuid=True), ForeignKey("catalog_databases.id", ondelete="CASCADE"))
    schema_name = Column(String(128))
    entity_id = Column(UUID(as_uuid=True), ForeignKey("catalog_entities.id", ondelete="CASCADE"))
    field_id = Column(UUID(as_uuid=True), ForeignKey("catalog_fields.id", ondelete="CASCADE"))
    level = Column(Integer)
    category = Column(String(4))
    reason = Column(Text)
    set_by = Column(String(256))
    set_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(f"target_type IN {ACCESS_TARGET_TYPES}", name="ck_catalog_access_labels_target"),
        CheckConstraint("level IS NOT NULL OR category IS NOT NULL", name="ck_catalog_access_labels_value"),
        Index("uq_catalog_access_labels_database", "database_id", unique=True,
              postgresql_where=text("target_type = 'database'")),
        Index("uq_catalog_access_labels_schema", "database_id", "schema_name", unique=True,
              postgresql_where=text("target_type = 'schema'")),
        Index("uq_catalog_access_labels_entity", "entity_id", unique=True,
              postgresql_where=text("target_type = 'entity'")),
        Index("uq_catalog_access_labels_field", "field_id", unique=True,
              postgresql_where=text("target_type = 'field'")),
    )


class CatalogAccessRule(Base):
    """An admin-written bucketing rule: "objects matching THIS go to THAT
    level and/or category". Patterns are case-insensitive wildcards (* and
    ?); an empty pattern matches anything. Highest priority wins, then the
    more specific rule (more patterns set)."""
    __tablename__ = "catalog_access_rules"

    id = _uuid_col()
    name = Column(String(256), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=100)
    scope = Column(String(16), nullable=False)
    source_pattern = Column(String(256))
    database_pattern = Column(String(256))
    schema_pattern = Column(String(256))
    table_pattern = Column(String(256))
    column_pattern = Column(String(256))
    object_type = Column(String(16))
    domain_pattern = Column(String(256))
    classification = Column(String(32))
    sensitivity_flag = Column(String(32))
    set_level = Column(Integer)
    set_category = Column(String(4))
    note = Column(Text)
    created_by = Column(String(256))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(f"scope IN {ACCESS_RULE_SCOPES}", name="ck_catalog_access_rules_scope"),
        CheckConstraint("set_level IS NOT NULL OR set_category IS NOT NULL", name="ck_catalog_access_rules_value"),
    )


class AppAccessAudit(Base):
    """Every change to who-can-see-what: placements, rules, review
    decisions, roles, role grants and user role assignments. Separate from
    the login audit; never pruned by the app."""
    __tablename__ = "app_access_audit"

    id = _uuid_col()
    actor = Column(String(256))
    action = Column(String(64), nullable=False)
    target_type = Column(String(32))
    target = Column(Text)
    old_value = Column(Text)
    new_value = Column(Text)
    detail = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_app_access_audit_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Cell spelling, enforced by the database (migration 0010): a stored cell is
# level then category ("20B"), a category is one upper-case letter, a level
# is >= 0. Same list as the migration (tests/test_access_unit.py compares).
# ---------------------------------------------------------------------------
_CELL_RE_SQL = "'^[0-9]{1,4}[A-Z]$'"
_CAT_RE_SQL = "'^[A-Z]$'"
_PROPOSAL = "access_review_dismissed IS NULL OR access_review_dismissed ~ '^([0-9]{1,4}|-)([A-Z]|-)$'"


def _cat_ck(col: str) -> str:
    return f"{col} IS NULL OR {col} ~ {_CAT_RE_SQL}"


def _lvl_ck(col: str) -> str:
    return f"{col} IS NULL OR {col} >= 0"


def _hist_ck(col: str) -> str:
    return f"{col} IS NULL OR {col} = '(unlabeled)' OR {col} ~ {_CELL_RE_SQL}"


CELL_CHECKS = [
    ("catalog_entities", "ck_catalog_entities_access_category", _cat_ck("access_category")),
    ("catalog_entities", "ck_catalog_entities_access_ai_category", _cat_ck("access_ai_category")),
    ("catalog_entities", "ck_catalog_entities_access_level", _lvl_ck("access_level")),
    ("catalog_entities", "ck_catalog_entities_access_ai_level", _lvl_ck("access_ai_level")),
    ("catalog_entities", "ck_catalog_entities_review_dismissed", _PROPOSAL),
    ("catalog_fields", "ck_catalog_fields_access_category", _cat_ck("access_category")),
    ("catalog_fields", "ck_catalog_fields_access_ai_category", _cat_ck("access_ai_category")),
    ("catalog_fields", "ck_catalog_fields_access_level", _lvl_ck("access_level")),
    ("catalog_fields", "ck_catalog_fields_access_ai_level", _lvl_ck("access_ai_level")),
    ("catalog_fields", "ck_catalog_fields_review_dismissed", _PROPOSAL),
    ("catalog_history", "ck_catalog_history_cell_values",
     f"field_changed <> 'access_classification' OR (({_hist_ck('old_value')}) AND ({_hist_ck('new_value')}))"),
    ("catalog_ask_log", "ck_catalog_ask_log_cells_held", "catalog_cells_valid(cells_held)"),
    ("catalog_ask_log", "ck_catalog_ask_log_cells_used", "catalog_cells_valid(cells_used)"),
    ("catalog_ask_log", "ck_catalog_ask_log_label_category", _cat_ck("label_category")),
    ("catalog_ask_log", "ck_catalog_ask_log_label_level", _lvl_ck("label_level")),
    ("app_role_grants", "ck_app_role_grants_category", "category ~ " + _CAT_RE_SQL),
    ("app_role_grants", "ck_app_role_grants_level", "level >= 0"),
    ("catalog_access_labels", "ck_catalog_access_labels_category", _cat_ck("category")),
    ("catalog_access_labels", "ck_catalog_access_labels_level", _lvl_ck("level")),
    ("catalog_access_rules", "ck_catalog_access_rules_set_category", _cat_ck("set_category")),
    ("catalog_access_rules", "ck_catalog_access_rules_set_level", _lvl_ck("set_level")),
]

for _table, _name, _expr in CELL_CHECKS:
    Base.metadata.tables[_table].append_constraint(CheckConstraint(_expr, name=_name))


class CatalogSourceConfig(Base):
    """A data source added by an administrator in the web UI (Sources >
    Add source). Merged with config.yaml's catalog.sources at load time
    (shared/sources.py); a YAML source with the same name wins. The
    password is encrypted (shared/secrets.py) and never sent to a browser."""
    __tablename__ = "catalog_source_configs"

    id = _uuid_col()
    name = Column(String(128), nullable=False, unique=True)
    type = Column(String(64), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    host = Column(String(512), nullable=False, default="")
    port = Column(Integer)
    username = Column(String(256), nullable=False, default="")
    password_encrypted = Column(Text)
    url = Column(Text, nullable=False, default="")
    databases = Column(JSONB, nullable=False, default=list)
    schemas = Column(JSONB, nullable=False, default=list)
    options = Column(JSONB, nullable=False, default=dict)
    created_by = Column(String(256))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
