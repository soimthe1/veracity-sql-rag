"""
The small, stable consumer contract (spec section 16). Deliberately
minimal — a consuming AI application should be productive from this
file plus CATALOG_CONSUMER_GUIDE.md in under an hour.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: str  # HEALTHY | DEGRADED | DISABLED | UNAVAILABLE
    catalog_enabled: bool
    catalog_db_connected: bool


class SourceOut(BaseModel):
    id: UUID
    name: str
    host: str
    status: str
    enabled: bool
    database_count: int
    last_discovery_at: Optional[datetime]
    last_validation_at: Optional[datetime]

    class Config:
        from_attributes = True


class SourceConfigOut(BaseModel):
    """Instance-level sweeping configuration for one source database server —
    deliberately INSTANCE level only (host/port/login/driver/TLS), never
    per-database controls, by explicit requirement:
    "purposely not doing at database level ... sweep the at instance
    level not at database level." Which databases and which objects
    within them get analyzed is entirely delegated to autodiscovery +
    the coverage-percentage knob (see README.md) — the
    web UI's Sources tab shows this as read-only, config-driven
    information (edit config.yaml/.env, not this API) plus whatever the
    engine has actually discovered so far. The password is never
    included — only whether a login name is configured."""
    name: str
    enabled: bool
    is_configured: bool
    type: str = "mssql"
    host: str
    port: int
    driver: str
    login_user: str
    trust_server_certificate: bool
    encrypt: bool
    connect_timeout_seconds: int
    databases_to_catalog: List[str]
    autodiscover_databases: bool
    # From the live catalog_sources row, if discovery has run at least
    # once for this source — None until then.
    live_status: Optional[str] = None
    live_database_count: int = 0
    last_discovery_at: Optional[datetime] = None
    last_validation_at: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None


class FieldOut(BaseModel):
    id: UUID
    column_name: str
    data_type: str
    is_primary_key: bool
    is_nullable: bool
    semantic_name: Optional[str]
    description: Optional[str]
    confidence: float
    status: str
    sensitivity_flags: List[str] = []
    null_ratio: Optional[float]
    distinct_estimate: Optional[int]
    # Additive: the column's data-access cell and how it was decided.
    access_level: Optional[int] = None
    access_category: Optional[str] = None
    access_level_source: Optional[str] = None
    access_category_source: Optional[str] = None
    access_review_pending: bool = False

    class Config:
        from_attributes = True


class EntityOut(BaseModel):
    id: UUID
    source_name: str
    database_name: str
    schema_name: str
    object_name: str
    object_type: str
    classification: str
    semantic_name: Optional[str]
    description: Optional[str]
    domain: Optional[str]
    synonyms: List[str] = []
    confidence: float
    status: str
    deep_analysis: bool
    sensitivity_flags: List[str] = []
    last_verified_at: Optional[datetime]
    # Additive: the table's data-access cell and how it was decided.
    access_level: Optional[int] = None
    access_category: Optional[str] = None
    access_level_source: Optional[str] = None
    access_category_source: Optional[str] = None
    access_review_pending: bool = False

    @property
    def trusted_for_autonomous_use(self) -> bool:
        return self.status == "TRUSTED"


class EntityDetailOut(EntityOut):
    fields: List[FieldOut] = []


class RelationshipOut(BaseModel):
    id: UUID
    from_entity_id: UUID
    from_entity_name: str
    from_field: Optional[str]
    to_entity_id: UUID
    to_entity_name: str
    to_field: Optional[str]
    relationship_label: Optional[str]
    is_declared_fk: bool
    confidence: float
    status: str
    last_verified_at: Optional[datetime]


class SchemaGraphOut(BaseModel):
    """Backs GET /v1/schema-graph — the whole-catalog relationship map
    (every visible entity as a node, every relationship between two
    visible entities as an edge), as opposed to
    GET /v1/entities/{id}/relationships' focused single-table view."""
    nodes: List[EntityOut]
    edges: List[RelationshipOut]
    total_entities: int  # how many entities exist before `limit` trimmed the node set


class SearchResultOut(BaseModel):
    entity: EntityOut
    score: float


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


class JoinPathStepOut(BaseModel):
    entity_id: UUID
    entity_name: str
    relationship_id: Optional[UUID] = None


class JoinPathOut(BaseModel):
    from_entity: str
    to_entity: str
    hop_count: int
    confidence: float
    status: str
    steps: List[JoinPathStepOut]


class ResolveRequest(BaseModel):
    query: str
    limit: int = 10


class ResolveOut(BaseModel):
    query: str
    business_concepts: List[str]
    candidate_entities: List[SearchResultOut]
    trusted_relationships: List[RelationshipOut]
    join_paths: List[JoinPathOut]
    notes: str


class SuggestionOut(BaseModel):
    """One row in the Suggestions tab — an entity the demand-driven
    coverage loop has flagged (engine/coverage.py, answer/demand.py):
    people keep asking questions that match it, but it isn't TRUSTED
    (or trusted-but-not-yet-in-view) yet. `will_force_deep_analysis`
    tells the operator whether the NEXT coverage cycle will
    automatically promote it (unanswered_hit_count has reached
    catalog.coverage.demand_promote_threshold), same "self correcting"
    behavior the requirement asks for — no manual action required,
    this is a transparency view into something the system already does."""
    id: UUID
    source_name: str
    database_name: str
    schema_name: str
    object_name: str
    classification: str
    status: str
    deep_analysis: bool
    unanswered_hit_count: int
    last_unanswered_at: Optional[datetime]
    will_force_deep_analysis: bool


class StatusOut(BaseModel):
    """Backs both external status widgets / monitoring dashboards and
    this project's own standalone status UI."""
    enabled: bool
    catalog_db_connected: bool
    sources: int
    databases: int
    tables: int
    entities_understood: int
    trusted: int
    learning: int
    suspect: int
    unknown: int
    stale: int
    avg_confidence: Optional[float]
    coverage_deep_analysis_target_pct: float
    deep_analysis_entities: int
    entities_total: int = 0           # every cataloged object, whatever its trust state
    last_discovery_at: Optional[datetime]
    last_validation_at: Optional[datetime]
    llm_enabled: bool
    search_engine: str
    components: dict


class SourceCycleOut(BaseModel):
    """Per-source discovery/validation timing — what GET /v1/status
    gives as two catalog-wide MAX() timestamps, this gives per source,
    plus the NEXT run time computed from config's interval knobs (never
    stored — always derived, so it can never drift from config.yaml)."""
    name: str
    status: str
    last_discovery_at: Optional[datetime]
    next_discovery_at: Optional[datetime]
    last_validation_at: Optional[datetime]
    next_validation_at: Optional[datetime]
    last_error: Optional[str]


class LabeledCountOut(BaseModel):
    key: str
    label: str
    count: int


class PostgresInfoOut(BaseModel):
    """Non-secret connection facts for the catalog's own Postgres —
    password is never included. `connected` is a live SELECT 1 check
    (shared.db.healthcheck), not a cached flag, so it's always current
    at the moment this endpoint is called."""
    host: str
    port: int
    database: str
    sslmode: str
    connected: bool


class LlmInfoOut(BaseModel):
    """What's configured for inference, without secrets (no api_key).
    `effectively_enabled` mirrors InferenceConfig.effectively_enabled —
    the provider is set AND has both an endpoint and a model — which is
    the thing that actually determines whether inference runs, not just
    the `enabled` flag on its own."""
    enabled: bool
    effectively_enabled: bool
    provider: str
    endpoint: Optional[str]
    model: Optional[str]


class AnswerServiceInfoOut(BaseModel):
    """catalog.answer.enabled is a behavior switch INSIDE the answer
    process (see answer/main.py) — it says nothing about whether that
    process is even running, which is a separate `dev.ps1 -Answer` step.
    This exposes the config side; the web UI combines it with its own
    live answerApi.health() call (which fails outright if the process
    isn't up at all) to tell the two failure modes apart for the user."""
    enabled_in_config: bool
    port: int


class SystemHealthOut(BaseModel):
    """Everything the Health tab needs to answer 'is the system actually
    working, and if not, which piece' — connection-level facts for every
    moving part, not just the catalog_health event rows. Deliberately
    separate from StatusOut (spec section 19's stable external contract)
    and from CatalogSummaryOut (Overview's own needs) — this one is free
    to grow with whatever the Health tab needs next."""
    postgres: PostgresInfoOut
    llm: LlmInfoOut
    answer: AnswerServiceInfoOut
    auth_enabled: bool
    search_engine: str


class DatabaseSummaryOut(BaseModel):
    """One cataloged database: what's in the catalog for it, and what the
    last discovery scan actually saw there. `visibility_warning` flags a
    database where the scan saw nothing or almost nothing — usually the
    read-only login lacking VIEW DEFINITION / db_datareader there rather
    than a genuinely empty database."""
    source_name: str
    database_name: str
    objects: int
    tables: int
    views: int
    programmable: int  # procedures + functions
    trusted: int
    learning: int
    unknown: int
    avg_confidence: Optional[float]
    objects_seen_last_discovery: Optional[int]
    last_error: Optional[str]
    visibility_warning: Optional[str]  # "none" | "few" | None


class CatalogSummaryOut(BaseModel):
    """'What has the catalog actually figured out' — the human-readable
    counterpart to GET /v1/status's raw counts. Every grouping here is
    computed fresh from catalog_entities on each call (no caching, no
    extra table) — this catalog's realistic data volume (thousands, not
    millions, of entities) makes that cheap
    enough to just always be current."""
    generated_at: datetime
    discovery_interval_minutes: int
    validation_interval_hours: float
    sources: List[SourceCycleOut]
    domains: List[LabeledCountOut]          # CatalogEntity.domain, non-null, top N by count
    object_types: List[LabeledCountOut]     # TABLE / VIEW / PROCEDURE / FUNCTION / SYNONYM
    classifications: List[LabeledCountOut]  # BUSINESS_ENTITY / REFERENCE_DATA / ... (engine/classification.py)
    sensitivity: List[LabeledCountOut]      # PII / FINANCIAL / HR / ... unnested from sensitivity_flags
    relationships_total: int
    relationships_declared_fk: int
    databases: List[DatabaseSummaryOut] = Field(default_factory=list)
    relationships_inferred: int


class TrustLevelOut(BaseModel):
    """The Ask trust dial (Security tab) — see AnswerConfig.min_trust_level
    and answer/grounding.py's min_confidence_for_level(). `level` is what
    the UI shows/edits (0-10); `min_confidence` is what it actually
    resolves to right now, included so the UI can show its effect ("this
    requires confidence >= 0.63") without duplicating the 0-10 ->
    confidence math client-side. `source` says whether this is the live,
    UI-set value (catalog_settings) or still just config.yaml's default."""
    level: int
    min_confidence: float
    trusted_threshold: float
    source: str  # "override" | "yaml_default"


class TrustLevelIn(BaseModel):
    level: int  # clamped server-side to 0-10


class CoverageTargetOut(BaseModel):
    """The catalog-depth knob (engine/coverage.py) — what percentage of
    discovered objects get expensive Level-2 deep analysis. `source` is
    "override" once someone has set it from the UI, "yaml_default" while
    config.yaml's catalog.coverage.deep_analysis_target_pct is in effect."""
    target_pct: float
    min_deep_objects_per_source: int
    max_deep_objects_per_source: int
    source: str


class CoverageTargetIn(BaseModel):
    target_pct: float
