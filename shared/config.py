"""
Config loader: the one place settings are read and validated.

Two sources, never overlapping:

  config/config.yaml        every setting that is not a secret (hosts, ports, models,
                            flags, tuning; AI profiles under `ai:`)
  config/config.prod.yaml   every key of config.yaml with its production value; merged over
                            config.yaml when APP_ENV=prod (set by docker-compose.prod.yaml)
  config/local/             git-ignored private overlay (config.yaml, config.prod.yaml when
                            APP_ENV=prod), merged last: your hosts, users, domains live here
  environment               secrets only (passwords, keys). Development loads them from
                            .env; production passes .env.prod through compose `env_file:`.

A secret is referenced from YAML in one of two ways, never written there:
  - a key ending in `_env` / `_env_var` that names the variable
    (jwt_secret_env_var: CATALOG_AUTH_JWT_SECRET, api_key_env: LLM_API_KEY), or
  - a secret-named key (password, secret, token, api_key) whose whole value is a
    `${VAR}` reference, filled from the environment at load time.
Any other `${VAR}` is refused: environment variables never override YAML settings.
Unknown keys and secret values found in YAML stop startup with the offending key named.

Usage:
    from shared.config import load_config
    cfg = load_config()
    cfg.catalog.coverage.deep_analysis_target_pct

CONFIG_PATH selects the base YAML file (default config/config.yaml; tests point it at a
temporary file); config.prod.yaml is read from the same folder. ENV_FILE selects the
dotenv file (default .env).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared import ai_providers

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Words that mark a secret: such keys hold only a ${VAR} reference, never a value.
_SECRET_WORDS = ("password", "secret", "token", "api_key")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigError(RuntimeError):
    pass


class _Strict(BaseModel):
    """Base for every config section: an unknown (e.g. misspelt) key is an error."""
    model_config = ConfigDict(extra="forbid")


def _substitute_env(value: Any) -> Any:
    """Recursively replace ${VAR} tokens with os.environ values.

    An unset variable substitutes to "" rather than raising — this keeps
    the module a true no-op when e.g. an OpenAI-compatible endpoint's
    settings are left blank because that provider isn't in use. Pydantic validation
    downstream is what actually enforces "this field is required for the
    provider you selected", not this substitution step.
    """
    if isinstance(value, str):
        return _VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class PostgresConfig(_Strict):
    host: str
    port: int = 5432
    database: str
    user: str
    password: str = ""
    sslmode: str = "prefer"
    pool_size: int = 5
    pool_max_overflow: int = 5
    connect_timeout_seconds: int = 5

    @property
    def sqlalchemy_url(self) -> str:
        from urllib.parse import quote_plus
        return (
            f"postgresql+psycopg2://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}"
            f"?sslmode={self.sslmode or 'prefer'}&connect_timeout={self.connect_timeout_seconds}"
        )


# Source types with a built-in connection recipe (SQLAlchemy driver, default
# port). `mssql` uses its own native ODBC path; any other database works by
# giving the source a SQLAlchemy `url` and pip-installing that driver.
SOURCE_TYPES: Dict[str, tuple] = {
    "postgresql": ("postgresql+psycopg2", 5432),
    "mysql": ("mysql+pymysql", 3306),
    "mariadb": ("mysql+pymysql", 3306),
    "oracle": ("oracle+oracledb", 1521),
}


class SourceConfig(_Strict):
    name: str
    enabled: bool = True
    # mssql (default) | postgresql | mysql | mariadb | oracle | sqlite | any
    # other SQLAlchemy dialect, given `url`.
    type: str = "mssql"
    # SQLAlchemy URL, e.g. "sqlite:///data/shop.db" or
    # "oracle+oracledb://reader@db:1521/?service_name=ORCL". Replaces
    # host/port/user; keep the password in `password` (a ${VAR}), not here.
    url: str = ""
    driver: str = "ODBC Driver 17 for SQL Server"   # mssql only
    host: str = ""
    port: Optional[int] = None                       # default: the type's usual port
    user: str = ""
    password: str = ""
    trust_server_certificate: bool = True            # mssql only
    encrypt: bool = True                             # mssql only
    connect_timeout_seconds: int = 5
    databases_to_catalog: List[str] = Field(default_factory=list)
    # Non-mssql sources: schemas to sweep. Empty = every non-system schema
    # (PostgreSQL) or the connection's default schema (everything else).
    schemas: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _type_defaults(self) -> "SourceConfig":
        self.type = self.type.strip().lower()
        if self.port is None:
            self.port = 1433 if self.type == "mssql" else SOURCE_TYPES.get(self.type, ("", 0))[1]
        return self

    @property
    def is_configured(self) -> bool:
        """A source with no host (and no url) is enabled-but-unconfigured ->
        treated as absent rather than a startup error, so an example config
        never crashes the app."""
        return bool(self.host or self.url)

    @property
    def display_host(self) -> str:
        """Where the source lives, for display — never includes the password."""
        if self.host or not self.url:
            return self.host
        from sqlalchemy.engine import make_url
        u = make_url(self.url)
        return u.host or u.database or u.drivername

    @property
    def dialect(self) -> str:
        """SQL dialect of this source: mssql, postgresql, mysql (incl.
        MariaDB), oracle, sqlite, or whatever SQLAlchemy calls it."""
        if self.type == "mssql" and not self.url:
            return "mssql"
        try:
            name = self.sqlalchemy_url().get_backend_name()
        except ConfigError:
            return self.type
        return "mysql" if name == "mariadb" else name

    @property
    def display_driver(self) -> str:
        if self.type == "mssql" and not self.url:
            return self.driver
        try:
            return self.sqlalchemy_url().drivername
        except ConfigError:
            return self.type

    def sqlalchemy_url(self, database: Optional[str] = None):
        """Connection URL for the generic (non-mssql) connector."""
        from sqlalchemy.engine import URL, make_url

        if self.url:
            url = make_url(self.url)
        elif self.type in SOURCE_TYPES:
            url = URL.create(SOURCE_TYPES[self.type][0], username=self.user or None,
                             host=self.host, port=self.port or None)
        else:
            raise ConfigError(f"source {self.name!r}: type {self.type!r} needs a `url`")
        if self.password and url.password is None:
            url = url.set(password=self.password)
        if database:
            url = url.set(database=database)
        return url

    def odbc_connection_string(self, database: Optional[str] = None) -> str:
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.host},{self.port}",
            f"DATABASE={database or 'master'}",
            f"UID={self.user}",
            f"PWD={self.password}",
            f"TrustServerCertificate={'yes' if self.trust_server_certificate else 'no'}",
            f"Encrypt={'yes' if self.encrypt else 'no'}",
            f"Connection Timeout={self.connect_timeout_seconds}",
        ]
        return ";".join(parts)


class DiscoveryConfig(_Strict):
    interval_minutes: int = 60
    max_concurrent_sources: int = 2
    fingerprint_enabled: bool = True


class ValidationConfig(_Strict):
    interval_hours: int = 6


class CoverageConfig(_Strict):
    """THE data-volume knob: a percentage of discovered objects, ranked by
    relevance, that get Level-2 deep analysis. See DECISIONS.md."""
    deep_analysis_target_pct: float = 30.0
    min_deep_objects_per_source: int = 25
    max_deep_objects_per_source: int = 2000
    always_deep_categories: List[str] = Field(
        default_factory=lambda: ["BUSINESS_ENTITY", "RELATIONSHIP_TABLE"]
    )
    never_deep_categories: List[str] = Field(
        default_factory=lambda: ["SYSTEM_TECHNICAL", "TEMPORARY", "STAGING"]
    )
    reevaluate_on_change: bool = True

    # ---- Demand-driven ("self-correcting") coverage feedback loop ----
    # See engine/coverage.py and answer/grounding.py. Every time /v1/ask
    # finds an entity via search but can't ground on it because it isn't
    # TRUSTED yet, that entity's unanswered_hit_count goes up (shared/
    # models.py). This is the requirement's own definition of "self
    # correcting": "too many queries come for the same thing, the system
    # will decide to sweep it if its not available." never_deep_categories
    # still wins over demand — a SYSTEM_TECHNICAL table never gets forced
    # into deep analysis just because someone kept asking about it.
    demand_promote_threshold: int = 10
    demand_rank_boost: float = 0.03
    demand_rank_boost_cap: float = 0.5

    @field_validator("deep_analysis_target_pct")
    @classmethod
    def _clamp_pct(cls, v: float) -> float:
        return max(0.0, min(100.0, v))


class DomainRule(_Strict):
    name: str
    keywords: List[str] = Field(default_factory=list)


def _default_domain_rules() -> List[DomainRule]:
    # Ordered: FIRST keyword match wins, so specific phrases come before
    # broad words (see shared/domains.py).
    rules = [
        ("Configuration", ["configuration", "settings", "config"]),
        ("Human Resources", ["human resources", "hr", "personnel", "employee", "staff", "workforce",
                             "organization", "organisation", "payroll"]),
        ("Security & Access", ["access", "identity", "security", "authentication", "authorization",
                               "permission", "user management", "credential", "cyber"]),
        ("Data Integration", ["integration", "transfer", "migration", "synchroni", "replication", "etl"]),
        ("Analytics & AI", ["analytics", "data science", "machine learning", "reporting", "intelligence"]),
        ("Data Management", ["data management", "data processing", "master data", "file processing",
                             "storage", "data quality"]),
        ("Documents & Content", ["document", "content", "file", "archive", "records management"]),
        ("Business Processes", ["business process", "workflow", "business operations", "process",
                                "request", "approval"]),
        ("IT Operations", ["it operations", "information technology", "it infrastructure", "infrastructure",
                           "network", "system administration", "monitoring", "server"]),
        ("Applications", ["application", "software", "user interface", "navigation"]),
        ("Media & Communications", ["media", "news", "communication"]),
        ("Governance", ["governance", "compliance", "legal", "audit"]),
        ("Finance", ["finance", "financial", "accounting", "budget", "billing"]),
        ("3D & Simulation", ["3d", "computer graphics", "game", "simulation", "model instance"]),
        ("Environment & Weather", ["meteorolog", "weather", "environment", "climate"]),
        ("Quality Assurance", ["quality assurance", "testing", "test"]),
    ]
    return [DomainRule(name=n, keywords=k) for n, k in rules]


class DomainsConfig(_Strict):
    """Canonical business domains the free-text LLM domain is merged into
    — see shared/domains.py. Edit freely; the engine re-normalizes every
    entity's domain on its next discovery cycle."""
    enabled: bool = True
    canonical: List[DomainRule] = Field(default_factory=_default_domain_rules)


class ProfilingConfig(_Strict):
    enabled: bool = True
    max_sample_rows: int = 500
    statement_timeout_seconds: int = 5
    max_concurrent_queries_per_source: int = 1
    bounded_value_overlap_sample_rows: int = 200
    forbid_full_table_scans: bool = True
    forbid_select_star: bool = True


class InferenceConfig(_Strict):
    """LLM use by the engine (semantic proposals) and the answer service (question
    understanding, SQL generation, answer synthesis). Provider, endpoint, model,
    timeout and thinking control are the `ai.default` profile in config.yaml
    (shared/ai_providers.py); this section only switches LLM use on or off."""
    enabled: bool = True
    profile: str = "default"

    def _profile(self) -> Optional[dict]:
        return ai_providers.profile(self.profile) if ai_providers.enabled(self.profile) else None

    @property
    def effectively_enabled(self) -> bool:
        return self.enabled and self._profile() is not None

    @property
    def provider(self) -> str:
        p = self._profile()
        return p["provider"] if p else "disabled"

    @property
    def endpoint(self) -> Optional[str]:
        p = self._profile()
        return p["base_url"] if p else None

    @property
    def model(self) -> Optional[str]:
        p = self._profile()
        return p["model"] if p else None


class ConfidenceConfig(_Strict):
    trusted_threshold: float = 0.90
    suspect_threshold: float = 0.55
    weights: Dict[str, float] = Field(default_factory=lambda: {
        # explicit_fk is deliberately high enough to cross trusted_threshold
        # on its own — a SQL-Server-enforced FK constraint is closer to
        # ground truth than any inferred signal. See engine/confidence.py's
        # module docstring for the full status-transition reasoning.
        "explicit_fk": 0.92,
        "pk_fk_datatype_compatible": 0.12,
        "value_overlap_high": 0.35,
        "value_overlap_low_penalty": -0.20,
        "uniqueness_cardinality_match": 0.10,
        "view_definition_confirms": 0.20,
        "procedure_usage_confirms": 0.15,
        "executed_join_evidence": 0.25,
        "naming_similarity": 0.15,
        "llm_semantic_interpretation": 0.08,
        "contradictory_evidence_penalty": -0.40,
        # entity-level evidence (see engine/confidence.py's
        # record_entity_structural_evidence): without these, an entity's
        # ONLY possible evidence is llm_semantic_interpretation, capped
        # at 0.08 — meaning no table/view could EVER reach
        # trusted_threshold (0.90), or even suspect_threshold (0.55), no
        # matter how many validation cycles ran. These close that gap
        # with deterministic, non-LLM signals: a primary key + typed
        # columns (structural_completeness), and confirmed participation
        # in TRUSTED relationships (relationship_participation) — the
        # entity-side equivalent of explicit_fk being treated as
        # near-ground-truth on the relationship side.
        "structural_completeness": 0.50,
        "relationship_participation": 0.45,
    })


class ClassificationConfig(_Strict):
    """Provisional (level, category) shim (see shared/models.py's
    CatalogEntity.access_classification_provisional docstring). Deliberately simple — a sensitivity_flag ->
    (level, category) lookup, highest level wins when an object carries
    several flags ("if uncertain, prefer more restrictive" — same rule
    engine/semantic.py already applies to sensitivity_flags themselves).
    This is NOT the real governance-driven permission system planned
    for later; it only keeps the schema shape that
    system will use populated with a reasonable guess in the meantime."""
    enabled: bool = True
    # Superseded by the lattice file's default_level/default_category
    # (catalog.access.lattice_file) — kept only so an older config.yaml
    # that still sets them keeps validating.
    default_level: int = 10
    default_category: str = "A"
    # The "AI" layer of data bucketing (shared/access/labeling.py): the
    # engine's sensitivity flags on a table or column PROPOSE a level
    # (highest flag wins, snapped UP to the nearest lattice tier) and, where
    # listed, a category. A proposal never overrides a human decision
    # (manual placement, rule, or a label inherited from one) — when it
    # disagrees, the object goes to the Data access review list instead.
    level_by_sensitivity_flag: Dict[str, int] = Field(default_factory=lambda: {
        "PII": 20,
        "HR": 20,
        "FINANCIAL": 20,
        "HEALTH": 30,
        "DISCIPLINARY": 30,
        "SECURITY": 30,
        "CREDENTIALS": 50,
    })
    category_by_sensitivity_flag: Dict[str, str] = Field(default_factory=lambda: {
        "PII": "C",
        "HR": "C",
        "HEALTH": "C",
        "DISCIPLINARY": "C",
    })


def _check_cors_origins(v: List[str]) -> List[str]:
    """Browsers send the session cookie cross-origin (allow_credentials),
    so "*" would let ANY website make signed-in requests. Refused: list
    the UI's own origin(s) instead. Empty entries (an unset env var) are
    dropped."""
    out = [o.strip().rstrip("/") for o in v or [] if o and o.strip()]
    if "*" in out:
        raise ValueError('cors_origins may not be "*" (cookies are allowed cross-origin); '
                         "list the web UI origin(s), e.g. https://catalog.example.local")
    return out


class AnswerConfig(_Strict):
    """The 'catalog answerer' — a small, separate, fully autonomous
    service (its own process, see DECISIONS.md fault-isolation pattern)
    that turns a natural-language question into a bounded, read-only SQL
    query and returns the result as context chunks. This is the piece a
    trusted server caller (another application that resolves its own
    users) can optionally call as a fallback data source — see
    docs/integration.md for the integration contract.
    `enabled: false` (the default) keeps it fully inert: no route
    accepts a question, matching this project's "never a hard
    dependency" posture for every optional module.

    SQL generation reuses catalog.inference (the same ai.default profile)
    rather than a second LLM config: one LLM connection to configure."""
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8200
    # The web UI's origin(s) — the browser calls this service from the Ask
    # page. A server caller calls server-to-server and needs none.
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5180", "http://127.0.0.1:5180", "http://localhost:8180"])

    @field_validator("cors_origins")
    @classmethod
    def _cors(cls, v):
        return _check_cors_origins(v)

    # Access-grant enforcement in answer/grounding.py's ground_question():
    # by default an exact-match (access_level, access_category) check
    # against the caller's supplied grants — see grounding.py's module
    # docstring. Setting this to false is a DELIBERATE, REVERSIBLE
    # TESTING KNOB that bypasses that one check so Ask can be exercised
    # before this app's own security/role infrastructure exists — it
    # does NOT touch guardrails.py's SQL-safety layer (still enforced:
    # single-statement, SELECT-only, deny-keywords, table-whitelist,
    # row-cap), and every call is still fully written to
    # catalog_ask_audit either way. The bypass is also surfaced back in
    # GroundingResult.notes and GET /health's require_grants field so
    # it's visible in the Ask tab and the audit trail, never silent.
    # Revert to true once real grant/role enforcement is in place.
    require_grants: bool = True
    # When catalog.auth.enabled is true, /v1/ask never trusts grants sent
    # by a browser: a signed-in user's cells are resolved from their
    # session token. A trusted SERVER-side caller (another application
    # that resolves its own users) may still pass grants in the body, but
    # only together with this shared secret in an X-Service-Key header.
    # Name of the ENV VAR holding it — never the secret itself. Unset =
    # no service callers accepted while auth is on.
    service_key_env_var: str = "CATALOG_ANSWER_SERVICE_KEY"

    # TRUST is no longer a
    # hard gate on whether an entity is usable at all (see grounding.py's
    # module docstring) — access grants above are the only real gate.
    # This is the user-facing dial instead: a 0-10 level, DEFAULT HERE,
    # but the live value a running process actually uses is read from
    # catalog_settings (shared/settings.py, key "min_trust_level") so it
    # can be changed from the web UI (Security tab) without editing YAML
    # or restarting anything — this value only matters the very first
    # time (before anyone has touched the UI control) or if that DB row
    # is ever missing. 0 = use everything, labeled by trust score
    # (today's default). 10 = require confidence >= catalog.confidence.
    # trusted_threshold — equivalent to the old hard TRUSTED-only gate.
    # Values between interpolate linearly toward trusted_threshold; see
    # grounding.py's _min_confidence_for_level().
    min_trust_level: int = 0

    # Grounding: how many entities (+ their join-path neighbors) a
    # single question may pull into the LLM's schema context /
    # whitelist. Small on purpose — this is meant to answer "tell me
    # about employee X", not open-ended warehouse queries.
    max_entities_in_context: int = 12
    max_fields_per_entity: int = 40
    # The same kind of data (employees, applications, statuses) often
    # exists in several cataloged databases. When the best-matching one
    # can't answer, Ask moves on to the next — up to this many in total.
    max_databases_per_question: int = 3

    # Execution guardrails, layered ON TOP OF (never instead of)
    # engine.connectors.mssql's own read-only statement-verb guard and
    # the source login's own DB-level read-only grant (see
    # README.md) — defense in depth, not the only layer.
    max_rows: int = 200
    statement_timeout_seconds: int = 10
    max_concurrent_queries_per_source: int = 1
    deny_keywords: List[str] = Field(default_factory=lambda: [
        "insert", "update", "delete", "merge", "drop", "alter", "truncate", "create",
        "grant", "revoke", "exec", "execute", "sp_", "xp_", "openrowset", "opendatasource",
        "bulk", "shutdown", "backup", "restore", "into",
    ])

    # Every /v1/ask call is written to catalog_ask_audit — allowed,
    # rejected, or errored — never best-effort like CatalogEvidence is.
    # This flag exists only to let a test harness disable the write, not
    # as a "turn off the audit trail" production knob.
    audit_enabled: bool = True
    # Delete question-audit records (catalog_ask_log / _audit / _exports)
    # older than this many days. 0 = keep forever (the default).
    audit_retention_days: int = 0


class LdapConfig(_Strict):
    """AD/LDAP settings for this app's login. server_url == "" means "not configured";
    authenticate() then always returns False rather than raising, same
    graceful-degradation posture as every other optional module here."""
    server_url: str = ""  # "ldap://host:port" or "ldaps://host:port"
    bind_template: str = "{username}@{domain}"
    domain: str = ""
    search_base: str = ""
    user_filter: str = "(sAMAccountName={username})"
    use_starttls: bool = False
    # Verify the AD server's TLS certificate (ldaps:// or STARTTLS). Off =
    # the password is encrypted but the server isn't authenticated, so a
    # machine in the middle could read it. On in production. With no
    # ca_certs_file, the operating system's trusted CAs are used; for an
    # internal CA give its PEM file here.
    tls_verify: bool = False
    ca_certs_file: str = ""
    connect_timeout_s: int = 10
    # Directory search for Security -> People -> "Add person" (look a
    # person up in AD instead of typing their account name). Needs a
    # read-only service account: its bind name here, its password in the
    # env var named below. Empty = search disabled; admins then type the
    # account name by hand, exactly as before.
    search_bind_user: str = ""
    search_bind_password_env_var: str = "CATALOG_AUTH_LDAP_BIND_PASSWORD"
    directory_filter: str = (
        "(&(objectCategory=person)(objectClass=user)"
        "(|(sAMAccountName=*{q}*)(displayName=*{q}*)(mail=*{q}*)(givenName=*{q}*)(sn=*{q}*)))"
    )
    directory_max_results: int = 25


class AuthConfig(_Strict):
    """Login + a (level, category) permission gate for THIS project's
    OWN web UI. Separate lattice from CatalogEntity.access_level/
    access_category (ClassificationConfig's provisional shim) — the two
    deliberately share the same (int, str) shape so a resolved
    principal's grants here can be handed straight to the answer/
    process's POST /v1/ask (see docs/integration.md) without translation.

    `enabled: false` (the code default; the development config enables
    it) is a hard requirement: the web UI skips the login page entirely and every
    request is treated as an unrestricted principal — matching this
    project's "never a hard dependency" posture for every optional
    module. Flip it on only once real users/roles have been created
    (see shared/auth/ and DECISIONS.md's "no self-provisioning" note —
    an AD account with no matching row here is refused, not
    auto-registered).

    `mode` picks how people sign in:
      local - accounts and passwords kept in the catalog database (the
              default). The first administrator is created on a one-time
              setup screen, unlocked by a code printed in the API log.
      ldap  - Active Directory / LDAP passwords (catalog.auth.ldap must
              name a server); accounts are still registered here first.
      none  - no sign-in at all, everyone is an unrestricted admin. For a
              single-user demo on localhost only.
    Older configs without `mode` keep working: `enabled: true` means ldap,
    `enabled: false` means none. `enabled` is derived from `mode`."""
    mode: Optional[Literal["local", "ldap", "none"]] = None
    enabled: bool = False
    cookie_name: str = "ai_catalog_session"
    # Send the session cookie over HTTPS only. On in production; off for
    # plain-http localhost development.
    cookie_secure: bool = False
    expiry_minutes: int = 480
    algorithm: str = "HS256"
    # Name of the ENV VAR (not the secret itself — never put a secret in
    # config.yaml, see the module docstring) holding the JWT signing key.
    # shared/auth/tokens.py refuses to start signing tokens if this env
    # var is unset or under 16 characters — no silent fallback secret.
    jwt_secret_env_var: str = "CATALOG_AUTH_JWT_SECRET"
    ldap: LdapConfig = Field(default_factory=LdapConfig)
    # First-time setup: comma-separated account names (any form —
    # DOMAIN\\user, user@domain, user). When one of these signs in with a
    # valid AD password and isn't registered yet (or holds no roles), it is
    # registered automatically and added to the built-in Administrator
    # role. Typically set via ${CATALOG_BOOTSTRAP_ADMINS} in .env and
    # cleared once real admins exist. Nobody else is ever auto-registered.
    bootstrap_admins: str = ""
    # The (level, category) grid the Security tab's permission-matrix
    # view and role editor render from — config-driven (not hardcoded)
    # so an operator can point it at the exact same scheme another
    # system already uses, if they choose to mirror it. Values are display metadata only; the
    # actual grant checks are exact-match ints/strings (shared/auth/grants.py).
    levels: List[Dict[str, Any]] = Field(default_factory=lambda: [
        {"value": 10, "key": "public", "name": "Public"},
        {"value": 40, "key": "internal", "name": "Internal"},
        {"value": 50, "key": "confidential", "name": "Confidential"},
        {"value": 70, "key": "restricted", "name": "Restricted"},
        {"value": 80, "key": "secret", "name": "Secret"},
    ])
    categories: List[Dict[str, Any]] = Field(default_factory=lambda: [
        {"key": "A", "name": "General", "enabled": True},
        {"key": "B", "name": "Financial", "enabled": True},
        {"key": "C", "name": "Personnel / HR", "enabled": True},
        {"key": "D", "name": "Security", "enabled": True},
    ])

    @model_validator(mode="after")
    def _resolve_mode(self) -> "AuthConfig":
        if self.mode is None:
            if "enabled" in self.model_fields_set:
                self.mode = "ldap" if self.enabled else "none"
            else:
                self.mode = "local"
        self.enabled = self.mode != "none"
        if self.mode == "ldap" and not self.ldap.server_url:
            raise ValueError("catalog.auth.mode is 'ldap' but catalog.auth.ldap.server_url is empty — set the "
                             "LDAP server (see config/config.example.yaml) or use mode: local")
        return self


class AccessConfig(_Strict):
    """Data access realm: which (level, category) cell every database,
    schema, table and column sits in, and how that is decided — see
    shared/access/labeling.py. The matrix itself (level names, colours,
    categories, default cell) lives in lattice_file (config/levels.yaml
    format)."""
    # Relative paths resolve against the working directory first, then
    # the repo root. Missing file = fall back to catalog.auth.levels /
    # categories with a default of the highest level in the last category.
    lattice_file: str = "config/levels.yaml"
    # Administration is not clearance:
    # false = an admin manages everything but, in Ask and the catalog
    # screens, sees only the cells their own roles grant. true = anyone in
    # an admin role sees ALL data whatever the cells (testing only).
    admins_see_all_data: bool = False
    # "auto" layer: apply the lattice file's category_folders /
    # folder_conventions to database, schema and table names.
    naming_conventions: bool = True
    # AI layer, category from the inferred business domain (see
    # catalog.domains) — only for domains listed here.
    category_by_domain: Dict[str, str] = Field(default_factory=lambda: {"Human Resources": "C"})
    # Which screens someone WITHOUT an admin role may open (sign-in on).
    # Admins always see the whole app. "ask" is always available. Catalog
    # screens: overview, sources, entities, relationships, glossary,
    # suggestions, integrity, health. Data access and Security are
    # admin-only whatever is listed here. Enforced by the API as well as
    # hidden in the UI; opening a catalog screen still only ever shows
    # tables inside the person's own cells.
    non_admin_screens: List[str] = Field(default_factory=lambda: ["ask"])


class SearchConfig(_Strict):
    """No settings: search is the catalog Postgres itself (api/search/postgres_search.py)."""


class ApiConfig(_Strict):
    host: str = "0.0.0.0"
    port: int = 8100
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5180", "http://127.0.0.1:5180", "http://localhost:8180"])

    @field_validator("cors_origins")
    @classmethod
    def _cors(cls, v):
        return _check_cors_origins(v)


class WebConfig(_Strict):
    dev_port: int = 5180
    api_base_url: str = "http://localhost:8100"
    # Browser-facing URL for the answer/ process (POST /v1/ask) — only
    # used by the web UI's Ask tab, and only meaningful once
    # catalog.answer.enabled: true. Kept separate from api_base_url
    # (different port, different process — see DECISIONS.md) rather than
    # the web UI trying to derive one from the other.
    answer_base_url: str = "http://localhost:8200"


class HealthConfig(_Strict):
    source_failure_threshold: int = 3
    catalog_db_retry_backoff_seconds: List[int] = Field(
        default_factory=lambda: [5, 15, 30, 60, 120, 300]
    )


class CatalogConfig(_Strict):
    enabled: bool = False
    database: PostgresConfig
    sources: List[SourceConfig] = Field(default_factory=list)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    coverage: CoverageConfig = Field(default_factory=CoverageConfig)
    domains: DomainsConfig = Field(default_factory=DomainsConfig)
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    access: AccessConfig = Field(default_factory=AccessConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    answer: AnswerConfig = Field(default_factory=AnswerConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)

    @property
    def enabled_sources(self) -> List[SourceConfig]:
        return [s for s in self.sources if s.enabled and s.is_configured]


class ServiceConfig(_Strict):
    name: str = "ai-catalog"
    log_level: str = "INFO"
    # Rotating file logging (shared/logging_setup.py) — every process
    # (engine/api/answer, plus one-shot scripts like
    # scripts/trigger_validation.py) writes its own <log_dir>/<name>.log
    # in ADDITION to the console, so a problem is still there to read
    # afterward even if nobody was watching the terminal when it
    # happened. Deliberately capped low: this answers "what went wrong
    # recently," not a permanent audit trail — that's catalog_ask_audit
    # / catalog_history, which are never subject to this rotation.
    log_to_file: bool = True
    log_dir: str = "logs"
    log_max_bytes: int = 5 * 1024 * 1024  # 5 MB per active file before rotating
    log_backup_count: int = 1              # + 1 rotated backup = 10 MB total cap per process


class AppConfig(_Strict):
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    # AI profiles, validated by shared/ai_providers.py (see config.yaml `ai:`).
    ai: Dict[str, Any] = Field(default_factory=dict)
    catalog: CatalogConfig


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _read_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge dicts; any other value (lists included) is replaced whole."""
    out = dict(base)
    for key, value in overlay.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _local_overlay(config_path: str) -> Dict[str, Any]:
    """Private, git-ignored settings: <config dir>/local/config.yaml, then
    local/config.prod.yaml when APP_ENV=prod. Missing files are skipped."""
    local_dir = os.path.join(os.path.dirname(config_path), "local")
    names = ["config.yaml"]
    if os.environ.get("APP_ENV", "dev").strip().lower() == "prod":
        names.append("config.prod.yaml")
    out: Dict[str, Any] = {}
    for name in names:
        path = os.path.join(local_dir, name)
        if os.path.exists(path):
            out = _merge(out, _read_yaml(path))
    return out


def _check_secrets(node: Any, path: str = "") -> None:
    """Secrets live in the environment only. A secret-named key may hold a whole
    ${VAR} reference (or nothing); no other key may reference the environment."""
    if isinstance(node, dict):
        for key, value in node.items():
            name = f"{path}{key}"
            lowered = str(key).lower()
            if any(w in lowered for w in _SECRET_WORDS) and not lowered.endswith(("_env", "_env_var")):
                if value not in (None, "") and not (isinstance(value, str) and _VAR_PATTERN.fullmatch(value.strip())):
                    raise ConfigError(f"{name}: secrets belong in .env / .env.prod; "
                                      f"write a ${{VAR}} reference here, not the value")
                continue
            _check_secrets(value, f"{name}.")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _check_secrets(item, f"{path}[{i}].")
    elif isinstance(node, str) and _VAR_PATTERN.search(node):
        raise ConfigError(f"{path.rstrip('.')}: ${{VAR}} references are allowed only for secrets; "
                          f"write the value in config.yaml")


def _load_uncached(config_path: Optional[str] = None, env_file: Optional[str] = None) -> AppConfig:
    env_file = env_file or os.environ.get("ENV_FILE", ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file, override=False)
    config_path = config_path or os.environ.get("CONFIG_PATH", "config/config.yaml")
    raw = _read_yaml(config_path)
    if os.environ.get("APP_ENV", "dev").strip().lower() == "prod":
        prod_path = os.path.join(os.path.dirname(config_path), "config.prod.yaml")
        if not os.path.exists(prod_path):
            raise ConfigError(f"APP_ENV=prod but {prod_path} is missing")
        raw = _merge(raw, _read_yaml(prod_path))
    raw = _merge(raw, _local_overlay(config_path))
    _check_secrets(raw)
    cfg = AppConfig(**_substitute_env(raw))
    ai_providers.configure(cfg.ai, base_dir=_REPO_ROOT)
    return cfg


@lru_cache(maxsize=1)
def _cached(config_path: str, env_file: str) -> AppConfig:
    return _load_uncached(config_path, env_file)


def load_config(config_path: Optional[str] = None, env_file: Optional[str] = None,
                 force_reload: bool = False) -> AppConfig:
    """Load and validate the app config. Cached by (config_path, env_file)
    unless force_reload=True (used by tests / the API's config-reload hook,
    if ever added)."""
    resolved_config_path = config_path or os.environ.get("CONFIG_PATH", "config/config.yaml")
    resolved_env_file = env_file or os.environ.get("ENV_FILE", ".env")
    if force_reload:
        _cached.cache_clear()
    return _cached(resolved_config_path, resolved_env_file)
