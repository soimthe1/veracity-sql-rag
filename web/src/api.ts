/**
 * Thin typed client for the catalog API. Mirrors api/schemas.py exactly —
 * if you change a field there, change it here too. Deliberately has no
 * retry/caching cleverness: this UI polls on an interval (see App.tsx),
 * so a failed call just waits for the next tick rather than needing its
 * own backoff logic.
 */

// Resolution order: window.__ENV__.API_BASE_URL (set at container startup
// by docker-entrypoint-web.sh from the API_BASE_URL env var — this is how
// the built image is pointed at a different API per environment without a
// rebuild) > VITE_API_BASE_URL (set by dev.ps1 from config.yaml's
// catalog.web.api_base_url at dev-server start) > the documented default,
// for a bare `npm run dev` with nothing else configured.
const API_BASE = window.__ENV__?.API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "http://localhost:8100";
// Same resolution order, for the optional answer/ process (Ask tab) —
// see catalog.web.answer_base_url.
const ANSWER_BASE = window.__ENV__?.ANSWER_BASE_URL || import.meta.env.VITE_ANSWER_BASE_URL || "http://localhost:8200";

export class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

let authToken: string | null = null;

function setToken(token: string | null) {
  authToken = token;
}

/** FastAPI error bodies carry `detail` as a string, OR (for request-
 * validation errors) as a list of {loc, msg} objects — rendering that
 * list directly is what produced a bare "[object Object]" banner. */
function describeDetail(detail: unknown): string | undefined {
  if (detail == null) return undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        const where = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "query" && x !== "body").join(".") : "";
        return where ? `${where}: ${item.msg ?? JSON.stringify(d)}` : item.msg ?? JSON.stringify(d);
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  try {
    res = await fetch(`${base}${path}`, {
      headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
      ...init,
    });
  } catch (err) {
    throw new ApiError(
      `Could not reach ${base}${path}. Is that service running? (dev.ps1 starts it, or ` +
        `\`uvicorn api.main:app\` / \`uvicorn answer.main:app\` by hand.)`,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = describeDetail(body.detail) ?? detail;
    } catch {
      /* body wasn't JSON — keep statusText */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const apiRequest = <T,>(path: string, init?: RequestInit) => request<T>(API_BASE, path, init);

export interface EntitiesParams {
  query?: string;
  status?: string;
  domain?: string;
  database?: string;
  classification?: string;
  object_type?: string;
  sensitivity?: string;
  deep_analysis?: boolean;
  source?: string;
  trusted_only?: boolean;
  limit?: number;
  offset?: number;
}
const answerRequest = <T,>(path: string, init?: RequestInit) => request<T>(ANSWER_BASE, path, init);

// ------------------------------------------------------------------
// Catalog types (unchanged surface)
// ------------------------------------------------------------------

export interface HealthOut {
  status: "HEALTHY" | "DEGRADED" | "DISABLED" | "UNAVAILABLE";
  catalog_enabled: boolean;
  catalog_db_connected: boolean;
}

export interface SourceOut {
  id: string;
  name: string;
  host: string;
  status: string;
  enabled: boolean;
  database_count: number;
  last_discovery_at: string | null;
  last_validation_at: string | null;
}

export interface SourceConfigOut {
  name: string;
  enabled: boolean;
  is_configured: boolean;
  host: string;
  port: number;
  type: string;
  driver: string;
  login_user: string;
  trust_server_certificate: boolean;
  encrypt: boolean;
  connect_timeout_seconds: number;
  databases_to_catalog: string[];
  autodiscover_databases: boolean;
  live_status: string | null;
  live_database_count: number;
  last_discovery_at: string | null;
  last_validation_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
}

export interface FieldOut {
  id: string;
  column_name: string;
  data_type: string;
  is_primary_key: boolean;
  is_nullable: boolean;
  semantic_name: string | null;
  description: string | null;
  confidence: number;
  status: string;
  sensitivity_flags: string[];
  null_ratio: number | null;
  distinct_estimate: number | null;
  access_level?: number | null;
  access_category?: string | null;
  access_level_source?: string | null;
  access_category_source?: string | null;
  access_review_pending?: boolean;
}

export interface EntityOut {
  id: string;
  source_name: string;
  database_name: string;
  schema_name: string;
  object_name: string;
  object_type: string;
  classification: string;
  semantic_name: string | null;
  description: string | null;
  domain: string | null;
  synonyms: string[];
  confidence: number;
  status: string;
  deep_analysis: boolean;
  sensitivity_flags: string[];
  last_verified_at: string | null;
  access_level?: number | null;
  access_category?: string | null;
  access_level_source?: string | null;
  access_category_source?: string | null;
  access_review_pending?: boolean;
}

export interface EntityDetailOut extends EntityOut {
  fields: FieldOut[];
}

export interface RelationshipOut {
  id: string;
  from_entity_id: string;
  from_entity_name: string;
  from_field: string | null;
  to_entity_id: string;
  to_entity_name: string;
  to_field: string | null;
  relationship_label: string | null;
  is_declared_fk: boolean;
  confidence: number;
  status: string;
  last_verified_at: string | null;
}

export interface HealthComponent {
  status: string;
  // catalog_health.detail is a free-form JSON column (see
  // engine/health.py) — the engine has always sent an object here, never
  // a plain string. `unknown` forces every consumer (Health.tsx) to
  // narrow before rendering, instead of assuming a string like this
  // interface previously (and incorrectly) did.
  detail: unknown;
  updated_at: string;
}

export interface StatusOut {
  enabled: boolean;
  catalog_db_connected: boolean;
  sources: number;
  databases: number;
  tables: number;
  entities_understood: number;
  trusted: number;
  learning: number;
  suspect: number;
  unknown: number;
  stale: number;
  avg_confidence: number | null;
  coverage_deep_analysis_target_pct: number;
  deep_analysis_entities: number;
  entities_total: number;
  last_discovery_at: string | null;
  last_validation_at: string | null;
  llm_enabled: boolean;
  search_engine: string;
  components: Record<string, HealthComponent>;
}

export interface SuggestionOut {
  id: string;
  source_name: string;
  database_name: string;
  schema_name: string;
  object_name: string;
  classification: string;
  status: string;
  deep_analysis: boolean;
  unanswered_hit_count: number;
  last_unanswered_at: string | null;
  will_force_deep_analysis: boolean;
}

// ------------------------------------------------------------------
// Catalog summary ("what has the catalog figured out" + live cycle)
// ------------------------------------------------------------------

export interface SourceCycleOut {
  name: string;
  status: string;
  last_discovery_at: string | null;
  next_discovery_at: string | null;
  last_validation_at: string | null;
  next_validation_at: string | null;
  last_error: string | null;
}

export interface LabeledCountOut {
  key: string;
  label: string;
  count: number;
}

export interface CatalogSummaryOut {
  generated_at: string;
  discovery_interval_minutes: number;
  validation_interval_hours: number;
  sources: SourceCycleOut[];
  domains: LabeledCountOut[];
  object_types: LabeledCountOut[];
  classifications: LabeledCountOut[];
  sensitivity: LabeledCountOut[];
  relationships_total: number;
  relationships_declared_fk: number;
  relationships_inferred: number;
  databases: DatabaseSummaryOut[];
}

export interface DatabaseSummaryOut {
  source_name: string;
  database_name: string;
  objects: number;
  tables: number;
  views: number;
  programmable: number;
  trusted: number;
  learning: number;
  unknown: number;
  avg_confidence: number | null;
  objects_seen_last_discovery: number | null;
  last_error: string | null;
  visibility_warning: "none" | "few" | null;
}

// ------------------------------------------------------------------
// Auth / permission-matrix types
// ------------------------------------------------------------------

export interface Grant {
  level: number;
  category: string;
}

export interface Principal {
  auth_enabled: boolean;
  auth_mode?: "local" | "ldap" | "none";
  user_id: string | null;
  username: string | null;
  display_name: string | null;
  is_admin: boolean;
  grants: Grant[];
  roles?: string[];
  full_access?: boolean;
  /** screens this person may open — see catalog.access.non_admin_screens */
  screens?: string[];
  /** sees the SQL generated for their answers ("Queries run") */
  can_view_sql?: boolean;
}

export interface LoginResult {
  token: string;
  principal: Principal;
}

export interface LevelDef {
  value: number;
  key: string;
  name: string;
  name_ar?: string;
  color?: string;
}

export interface CategoryDef {
  key: string;
  name: string;
  name_ar?: string;
  enabled: boolean;
  reserved?: boolean;
}

export interface MatrixShape {
  levels: LevelDef[];
  categories: CategoryDef[];
  all_categories?: CategoryDef[];
  grantable?: string[];
  default_level?: number;
  default_category?: string;
  cell_labels?: Record<string, { name: string; name_ar?: string }>;
}

export interface RoleOut {
  id: string;
  name: string;
  description: string | null;
  is_admin: boolean;
  grants: Grant[];
  user_count: number;
  is_system?: boolean;
  /** members see the SQL generated for their answers */
  can_view_sql?: boolean;
}

export interface UserOut {
  id: string;
  username: string;
  display_name: string | null;
  is_active: boolean;
  roles: string[];
  is_admin: boolean;
  last_login_at: string | null;
  role_ids?: string[];
  cells?: Grant[];
}

export interface DirectoryPerson {
  username: string;
  display_name: string;
  email: string | null;
  department: string | null;
  title: string | null;
  registered: boolean;
}

// ------------------------------------------------------------------
// Data access realm (api/routers/access.py)
// ------------------------------------------------------------------

export type AccessKind = "database" | "schema" | "table" | "column";
export type AccessSource = "manual" | "rule" | "inherited" | "ai" | "auto" | "default" | "none";

export interface ScopeOut {
  /** null for non-admins: they see what they can query, not the estate size */
  tables_total: number | null;
  tables_visible: number;
  databases_total: number | null;
  databases_visible: number;
  columns_hidden: number;
  databases: { database: string; visible: number; total: number }[];
  full_access: boolean;
  is_admin: boolean;
  auth_enabled: boolean;
  cells: string[];
  roles: string[];
}

export interface GridOut {
  kind: AccessKind;
  total: number;
  quarantine: number;
  review_pending: number;
  cells: { level: number | null; category: string | null; count: number }[];
  level_sources: Record<string, number>;
  category_sources: Record<string, number>;
}

export interface AccessObject {
  kind: AccessKind;
  id: string;
  database_id: string;
  database: string;
  source: string;
  schema?: string;
  name: string;
  table?: string;
  table_id?: string;
  object_type?: string;
  data_type?: string;
  semantic_name?: string | null;
  level: number | null;
  category: string | null;
  level_source: AccessSource | null;
  category_source: AccessSource | null;
  detail: {
    level?: { src: string; via: string; human: boolean };
    category?: { src: string; via: string; human: boolean };
    ai?: { level: number | null; category: string | null; via: string | null };
    review?: string;
  };
  ai_level?: number | null;
  ai_category?: string | null;
  review_pending: boolean;
  pinned: boolean;
  tables?: number;
  table_level?: number | null;
  table_category?: string | null;
}

export interface ExplainCandidate {
  layer: AccessSource;
  value: number | string;
  via: string;
  human: boolean;
  won: boolean;
}

export interface ExplainOut {
  target: string;
  level: { value: number | null; source: AccessSource; via: string; human: boolean; candidates: ExplainCandidate[] };
  category: { value: string | null; source: AccessSource; via: string; human: boolean; candidates: ExplainCandidate[] };
  ai: { level: number | null; category: string | null; via: string | null };
  review: boolean;
  review_reason: string | null;
  pin: { level: number | null; category: string | null; reason: string | null; set_by: string | null; set_at: string | null } | null;
}

export interface AccessTarget {
  kind: AccessKind;
  id?: string;
  database_id?: string;
  schema_name?: string;
}

export interface AccessRule {
  id?: string;
  name: string;
  enabled: boolean;
  priority: number;
  scope: "database" | "schema" | "table" | "column";
  source_pattern?: string | null;
  database_pattern?: string | null;
  schema_pattern?: string | null;
  table_pattern?: string | null;
  column_pattern?: string | null;
  object_type?: string | null;
  domain_pattern?: string | null;
  classification?: string | null;
  sensitivity_flag?: string | null;
  set_level?: number | null;
  set_category?: string | null;
  note?: string | null;
  created_by?: string | null;
  updated_at?: string | null;
}

export interface RulePreview {
  tables_changed: number;
  columns_changed: number;
  examples: { kind: string; name: string; from: string; to: string }[];
}

export interface SimulateOut
  extends Omit<ScopeOut, "full_access" | "is_admin" | "auth_enabled" | "roles" | "tables_total" | "databases_total"> {
  tables_total: number;
  databases_total: number;
  sample_tables: { database: string; table: string; cell: string }[];
}

export interface AccessAuditRow {
  id: string;
  actor: string | null;
  action: string;
  target_type: string | null;
  target: string | null;
  old_value: string | null;
  new_value: string | null;
  detail: Record<string, unknown>;
  created_at: string | null;
}

export interface LoginAuditOut {
  id: string;
  username_attempted: string;
  success: boolean;
  reason: string | null;
  created_at: string | null;
}

// ------------------------------------------------------------------
// Ask (answer/) types
// ------------------------------------------------------------------

export interface AskChunk {
  chunk_id: string;
  doc_id: string;
  text: string;
  level: number;
  category: string;
  score: number;
  title: string | null;
  source_uri: string | null;
  origin: string;
  // The catalog's own confidence in the weakest table/relationship this
  // chunk relied on (answer/grounding.py's worst_trust()) — reported,
  // not gated on: Ask no longer refuses to answer below TRUSTED, so
  // this is what tells the person how much to trust what they're
  // reading. One of TRUSTED/LEARNING/SUSPECT/UNKNOWN, same vocabulary
  // as the Entities/Integrity tabs.
  trust_label: string | null;
  trust_score: number | null;
}

export interface AskGraphNode {
  id: string;
  label: string;
  status: string;
  confidence: number;
}

export interface AskGraphEdge {
  from_id: string;
  to_id: string;
  status: string;
}

export interface AskResult {
  chunks: AskChunk[];
  took_ms: number;
  notes: string;
  // Ready-made natural-language sentence synthesized from the executed
  // rows (answer/synthesis.py) — null when the LLM was unavailable or
  // declined, in which case the raw chunk text below is still there.
  answer: string | null;
  // Mirrors chunks[0]'s trust fields — see AskChunk.trust_label.
  trust_label: string | null;
  trust_score: number | null;
  // Just the tables this question was grounded on, and the
  // relationships between them — see answer/grounding.py. Empty when
  // nothing was grounded.
  graph_nodes: AskGraphNode[];
  graph_edges: AskGraphEdge[];
  access?: {
    mode: "open" | "user" | "service";
    full_access: boolean;
    cells: string[];
    cells_used: string[];
    label_level: number | null;
    label_category: string | null;
    columns_withheld: number;
  } | null;
  /** this question's audit reference — used by Create */
  ask_id?: string | null;
  /** the database that answered */
  database_name?: string | null;
  /** the rows, structured (values are strings) */
  results?: AskResultSet[];
  /** only for admins and roles with "Can see generated SQL" */
  queries?: AskQuery[] | null;
}

export interface AskResultSet {
  purpose: string;
  columns: string[];
  rows: (string | null)[][];
}

export interface AskQuery {
  purpose: string | null;
  sql: string | null;
  verdict: "ALLOWED" | "REJECTED" | "ERROR";
  rows: number | null;
  reason: string | null;
}

export interface TrustLevelOut {
  level: number;
  min_confidence: number;
  trusted_threshold: number;
  source: "override" | "yaml_default";
}

export interface CoverageTargetOut {
  target_pct: number;
  min_deep_objects_per_source: number;
  max_deep_objects_per_source: number;
  source: "override" | "yaml_default";
}

export interface SchemaGraphOut {
  nodes: EntityOut[];
  edges: RelationshipOut[];
  total_entities: number;
}

export interface AnswerHealthOut {
  status: string;
  answer_enabled: boolean;
  catalog_db_connected: boolean;
  llm_enabled: boolean;
  require_grants: boolean;
}

export interface PostgresInfoOut {
  host: string;
  port: number;
  database: string;
  sslmode: string;
  connected: boolean;
}

export interface LlmInfoOut {
  enabled: boolean;
  effectively_enabled: boolean;
  provider: string;
  endpoint: string | null;
  model: string | null;
}

export interface AnswerServiceInfoOut {
  enabled_in_config: boolean;
  port: number;
}

export interface SystemHealthOut {
  postgres: PostgresInfoOut;
  llm: LlmInfoOut;
  answer: AnswerServiceInfoOut;
  auth_enabled: boolean;
  search_engine: string;
}

// Shared shape for "jump to Entities, pre-filtered" navigation from
// Overview's boxes (and anywhere else that wants to deep-link into the
// Entities tab) — kept here since it mirrors api.entities()'s own params.
export interface EntitiesFilter {
  status?: string;
  domain?: string;
  database?: string;
  classification?: string;
  object_type?: string;
  sensitivity?: string;
  deep_analysis?: boolean;
  trusted_only?: boolean;
}

export const api = {
  setToken,

  health: () => apiRequest<HealthOut>("/health"),
  status: () => apiRequest<StatusOut>("/v1/status"),
  summary: () => apiRequest<CatalogSummaryOut>("/v1/status/summary"),
  systemHealth: () => apiRequest<SystemHealthOut>("/v1/status/system"),
  sources: () => apiRequest<SourceOut[]>("/v1/sources"),
  sourceConfig: () => apiRequest<SourceConfigOut[]>("/v1/sources/config"),
  entities: (params: EntitiesParams = {}) => {
    const qs = new URLSearchParams();
    if (params.query) qs.set("query", params.query);
    if (params.status) qs.set("status", params.status);
    if (params.domain) qs.set("domain", params.domain);
    if (params.database) qs.set("database", params.database);
    if (params.classification) qs.set("classification", params.classification);
    if (params.object_type) qs.set("object_type", params.object_type);
    if (params.sensitivity) qs.set("sensitivity", params.sensitivity);
    if (params.deep_analysis != null) qs.set("deep_analysis", String(params.deep_analysis));
    if (params.source) qs.set("source", params.source);
    if (params.trusted_only) qs.set("trusted_only", "true");
    qs.set("limit", String(params.limit ?? 100));
    if (params.offset) qs.set("offset", String(params.offset));
    return apiRequest<EntityOut[]>(`/v1/entities?${qs.toString()}`);
  },
  /** Every matching entity, fetched page by page at the API's own
   * maximum page size — never silently cut off at one page's worth. */
  allEntities: async (params: Omit<EntitiesParams, "limit" | "offset"> = {}): Promise<EntityOut[]> => {
    const PAGE = 500; // api/routers/entities.py: limit le=500
    const out: EntityOut[] = [];
    for (let offset = 0; offset < 20000; offset += PAGE) {
      const page = await api.entities({ ...params, limit: PAGE, offset });
      out.push(...page);
      if (page.length < PAGE) break;
    }
    return out;
  },
  entity: (id: string) => apiRequest<EntityDetailOut>(`/v1/entities/${id}`),
  /** Fuzzy, typo-tolerant catalog search (api/search/) — the top bar's box. */
  search: (query: string, limit = 8) =>
    apiRequest<{ entity: EntityOut; score: number }[]>("/v1/search", { method: "POST", body: JSON.stringify({ query, limit }) }),
  entityRelationships: (id: string) => apiRequest<RelationshipOut[]>(`/v1/entities/${id}/relationships`),
  suggestions: (limit = 50) => apiRequest<SuggestionOut[]>(`/v1/suggestions?limit=${limit}`),
  schemaGraph: (
    params: { trusted_only?: boolean; classification?: string; domain?: string; source?: string; limit?: number } = {},
  ) => {
    const qs = new URLSearchParams();
    if (params.trusted_only) qs.set("trusted_only", "true");
    if (params.classification) qs.set("classification", params.classification);
    if (params.domain) qs.set("domain", params.domain);
    if (params.source) qs.set("source", params.source);
    qs.set("limit", String(params.limit ?? 300));
    return apiRequest<SchemaGraphOut>(`/v1/schema-graph?${qs.toString()}`);
  },

  // Auth / session
  login: (username: string, password: string) =>
    apiRequest<LoginResult>("/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => apiRequest<{ ok: boolean }>("/v1/auth/logout", { method: "POST" }),
  me: () => apiRequest<Principal>("/v1/auth/me"),
  setupStatus: () => apiRequest<{ mode: string; needed: boolean }>("/v1/auth/setup"),
  setup: (body: { code: string; username: string; password: string; display_name?: string }) =>
    apiRequest<LoginResult>("/v1/auth/setup", { method: "POST", body: JSON.stringify(body) }),
  changePassword: (current_password: string, new_password: string) =>
    apiRequest<{ ok: boolean }>("/v1/auth/me/password", {
      method: "POST", body: JSON.stringify({ current_password, new_password }),
    }),
  matrix: () => apiRequest<MatrixShape>("/v1/auth/matrix"),

  // Admin: roles/users/login-audit
  roles: () => apiRequest<RoleOut[]>("/v1/auth/roles"),
  createRole: (body: { name: string; description?: string; is_admin: boolean; grants: Grant[]; can_view_sql?: boolean }) =>
    apiRequest<RoleOut>("/v1/auth/roles", { method: "POST", body: JSON.stringify(body) }),
  updateRole: (id: string, body: { name: string; description?: string; is_admin: boolean; grants: Grant[]; can_view_sql?: boolean }) =>
    apiRequest<RoleOut>(`/v1/auth/roles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRole: (id: string) => apiRequest<{ ok: boolean }>(`/v1/auth/roles/${id}`, { method: "DELETE" }),

  users: () => apiRequest<UserOut[]>("/v1/auth/users"),
  createUser: (body: { username: string; display_name?: string; is_active: boolean; role_ids: string[]; password?: string }) =>
    apiRequest<UserOut>("/v1/auth/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (
    id: string,
    body: { username: string; display_name?: string; is_active: boolean; role_ids: string[]; password?: string },
  ) => apiRequest<UserOut>(`/v1/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteUser: (id: string) => apiRequest<{ ok: boolean }>(`/v1/auth/users/${id}`, { method: "DELETE" }),

  loginAudit: (limit = 100) => apiRequest<LoginAuditOut[]>(`/v1/auth/login-audit?limit=${limit}`),
  directory: (q: string) =>
    apiRequest<{ configured: boolean; results: DirectoryPerson[] }>(`/v1/auth/directory?q=${encodeURIComponent(q)}`),

  // Data access realm
  auditQuestions: (p: { period?: string; user?: string; outcome?: string; flag?: string; q?: string; min_level?: number | null;
    limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && qs.set(k, String(v)));
    return apiRequest<AuditPage>(`/v1/audit/questions?${qs.toString()}`);
  },
  auditQuestion: (id: string) => apiRequest<AuditQuestionDetail>(`/v1/audit/questions/${id}`),
  scope: () => apiRequest<ScopeOut>("/v1/access/scope"),
  accessGrid: (kind: AccessKind, database?: string) =>
    apiRequest<GridOut>(`/v1/access/grid?kind=${kind}${database ? `&database=${encodeURIComponent(database)}` : ""}`),
  accessObjects: (p: {
    kind: AccessKind; level?: number | null; category?: string | null; database?: string; q?: string;
    source?: string; review?: boolean; table_id?: string; limit?: number; offset?: number;
  }) => {
    const qs = new URLSearchParams({ kind: p.kind });
    if (p.level != null) qs.set("level", String(p.level));
    if (p.category) qs.set("category", p.category);
    if (p.database) qs.set("database", p.database);
    if (p.q) qs.set("q", p.q);
    if (p.source) qs.set("source", p.source);
    if (p.review != null) qs.set("review", String(p.review));
    if (p.table_id) qs.set("table_id", p.table_id);
    qs.set("limit", String(p.limit ?? 50));
    if (p.offset) qs.set("offset", String(p.offset));
    return apiRequest<{ total: number; items: AccessObject[] }>(`/v1/access/objects?${qs.toString()}`);
  },
  accessExplain: (t: AccessTarget) => {
    const qs = new URLSearchParams({ kind: t.kind });
    if (t.id) qs.set("id", t.id);
    if (t.database_id) qs.set("database_id", t.database_id);
    if (t.schema_name) qs.set("schema_name", t.schema_name);
    return apiRequest<ExplainOut>(`/v1/access/explain?${qs.toString()}`);
  },
  place: (targets: AccessTarget[], level: number | null, category: string | null, reason?: string) =>
    apiRequest<{ ok: boolean }>("/v1/access/placements", {
      method: "POST",
      body: JSON.stringify({ targets, level, category, reason }),
    }),
  resetPlacement: (targets: AccessTarget[]) =>
    apiRequest<{ ok: boolean; reset: number }>("/v1/access/placements/reset", { method: "POST", body: JSON.stringify({ targets }) }),
  accessRules: () => apiRequest<AccessRule[]>("/v1/access/rules"),
  createAccessRule: (r: AccessRule) => apiRequest<AccessRule>("/v1/access/rules", { method: "POST", body: JSON.stringify(r) }),
  updateAccessRule: (id: string, r: AccessRule) =>
    apiRequest<AccessRule>(`/v1/access/rules/${id}`, { method: "PATCH", body: JSON.stringify(r) }),
  deleteAccessRule: (id: string) => apiRequest<{ ok: boolean }>(`/v1/access/rules/${id}`, { method: "DELETE" }),
  previewAccessRule: (body: { rule?: AccessRule; rule_id?: string; delete?: boolean }) =>
    apiRequest<RulePreview>("/v1/access/rules/preview", { method: "POST", body: JSON.stringify(body) }),
  accessReview: (limit = 200) => apiRequest<{ total: number; items: AccessObject[] }>(`/v1/access/review?limit=${limit}`),
  reviewAccept: (targets: AccessTarget[]) =>
    apiRequest<{ ok: boolean }>("/v1/access/review/accept", { method: "POST", body: JSON.stringify({ targets }) }),
  reviewDismiss: (targets: AccessTarget[]) =>
    apiRequest<{ ok: boolean }>("/v1/access/review/dismiss", { method: "POST", body: JSON.stringify({ targets }) }),
  simulate: (cells: string[]) => apiRequest<SimulateOut>(`/v1/access/simulate?cells=${encodeURIComponent(cells.join(","))}`),
  recomputeAccess: () => apiRequest<Record<string, number>>("/v1/access/recompute", { method: "POST" }),
  accessAudit: (limit = 200) => apiRequest<AccessAuditRow[]>(`/v1/access/audit?limit=${limit}`),

  // Ask trust dial (Security tab) — see AnswerConfig.min_trust_level.
  trustLevel: () => apiRequest<TrustLevelOut>("/v1/settings/trust-level"),
  setTrustLevel: (level: number) =>
    apiRequest<TrustLevelOut>("/v1/settings/trust-level", { method: "PUT", body: JSON.stringify({ level }) }),
  coverageTarget: () => apiRequest<CoverageTargetOut>("/v1/settings/coverage-target"),
  setCoverageTarget: (target_pct: number) =>
    apiRequest<CoverageTargetOut>("/v1/settings/coverage-target", { method: "PUT", body: JSON.stringify({ target_pct }) }),
};

export type QuestionKind = "count" | "breakdown" | "latest" | "list";

export interface SuggestedQuestion {
  question: string;
  database_name: string;
  table: string;
  trust_label: string;
  kind?: QuestionKind;
  domain?: string | null;
}

export type AskOutcome = "ANSWERED" | "NO_ROWS" | "NOT_FOUND" | "DECLINED" | "BLOCKED" | "ERROR" | "DISABLED";

export interface RecentQuestion {
  question: string;
  asked_at: string | null;
  answered: boolean;
  ask_id?: string | null;
  outcome?: AskOutcome | null;
  trust_label?: string | null;
  trust_score?: number | null;
  tables?: number;
  database_name?: string | null;
  label_level?: number | null;
  label_category?: string | null;
}

export interface HistoryOut {
  items: RecentQuestion[];
  total: number;
}

export type CreateFormat = "docx" | "pptx" | "pdf" | "email";

export interface AuditQuestion {
  id: string;
  question: string;
  asked_by: string | null;
  mode: string | null;
  created_at: string | null;
  outcome: AskOutcome;
  database_name: string | null;
  scope_database: string | null;
  label_level: number | null;
  label_category: string | null;
  cells_used: string[];
  tables: number;
  queries: number;
  queries_blocked: number;
  queries_failed: number;
  row_count: number;
  columns_withheld: number;
  trust_label: string | null;
  trust_score: number | null;
  duration_ms: number | null;
  exports: string[];
}

export interface AuditQuestionDetail extends AuditQuestion {
  tables_list: string[];
  cells_held: string[];
  queries_list: { sql: string | null; verdict: string; reason: string | null; rows: number | null; error: string | null;
    database_name: string | null; duration_ms: number | null; created_at: string | null }[];
  exports_list: { format: string; asked_by: string | null; created_at: string | null }[];
}

export interface AuditPage {
  items: AuditQuestion[];
  total: number;
  kpis: { questions: number; people: number; blocked: number; withheld: number; answered: number; exports: number };
  people: string[];
}

/** POST that returns a file (Create: Word / PowerPoint). */
async function download(base: string, path: string, body: unknown): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const res = await fetch(`${base}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = describeDetail((await res.json()).detail) ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail, res.status);
  }
  const cd = res.headers.get("content-disposition") ?? "";
  const filename = /filename="?([^";]+)"?/.exec(cd)?.[1] ?? "answer";
  return { blob: await res.blob(), filename };
}

export const answerApi = {
  health: () => answerRequest<AnswerHealthOut>("/health"),
  suggestions: (limit = 8) => answerRequest<SuggestedQuestion[]>(`/v1/ask/suggestions?limit=${limit}`),
  recent: (limit = 8) => answerRequest<RecentQuestion[]>(`/v1/ask/recent?limit=${limit}`),
  history: (limit = 25, offset = 0, q = "") =>
    answerRequest<HistoryOut>(`/v1/ask/history?limit=${limit}&offset=${offset}&q=${encodeURIComponent(q)}`),
  ask: (question: string, grants: Grant[], limit = 5, database?: string | null) =>
    answerRequest<AskResult>("/v1/ask", {
      method: "POST",
      body: JSON.stringify({ question, grants, limit, ...(database ? { database } : {}) }),
    }),
  /** Word / PowerPoint: returns the file. */
  createFile: (body: CreateRequest) => download(ANSWER_BASE, "/v1/ask/create", body),
  /** PDF / email are made in the browser — this only records it. */
  logCreate: (ask_id: string, format: "pdf" | "email") =>
    answerRequest<{ ok: boolean }>("/v1/ask/create", { method: "POST", body: JSON.stringify({ ask_id, format }) }),
};

export interface CreateRequest {
  ask_id: string;
  format: CreateFormat;
  lang: string;
  content: {
    answer: string | null;
    notes: string | null;
    results: AskResultSet[];
    tables: string[];
    trust_label: string | null;
    trust_score: number | null;
  };
}
