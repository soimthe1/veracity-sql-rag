import { useEffect, useState } from "react";
import { api, EntitiesFilter, EntityDetailOut, EntityOut, RelationshipOut } from "../api";
import { useI18n } from "../i18n";
import { fmtDate } from "../lib/format";
import Icon from "./Icon";
import StatusBadge from "./StatusBadge";
import EntityDiagram from "./EntityDiagram";
import { Pager, Panel, Pill, statusTone } from "./ui";
import { CellChip } from "../lib/lattice";
import { SourcePair } from "./AccessBits";

const STATUSES = ["", "TRUSTED", "LEARNING", "SUSPECT", "UNKNOWN", "STALE", "DEGRADED", "DISABLED"];
const OBJECT_TYPES = ["", "TABLE", "VIEW", "PROCEDURE", "FUNCTION", "SYNONYM"];
// Mirrors api/routers/status.py's _CLASSIFICATION_LABELS keys.
const CLASSIFICATIONS = [
  "", "BUSINESS_ENTITY", "TRANSACTIONAL_DATA", "REFERENCE_DATA", "RELATIONSHIP_TABLE", "CONFIGURATION",
  "AUDIT_HISTORY", "LOGGING", "ARCHIVE", "STAGING", "TEMPORARY", "SYSTEM_TECHNICAL", "PROCEDURE", "FUNCTION",
  "SYNONYM", "UNKNOWN",
];
const PAGE_SIZE = 25;

export function objectIcon(objectType: string): string {
  return objectType === "VIEW" ? "list" : objectType === "TABLE" ? "table" : objectType === "SYNONYM" ? "copy" : "gear";
}

function EntityDetail({
  entity,
  onBack,
  onOpen,
  onViewRelationships,
}: {
  entity: EntityDetailOut;
  onBack: () => void;
  onOpen: (id: string) => void;
  onViewRelationships?: (entityId: string) => void;
}) {
  const { t } = useI18n();
  const [rels, setRels] = useState<RelationshipOut[] | null>(null);

  useEffect(() => {
    setRels(null);
    api.entityRelationships(entity.id).then(setRels).catch(() => setRels([]));
  }, [entity.id]);

  return (
    <div>
      <div className="crumbs">
        <button onClick={onBack}>{t("nav.Entities")}</button>
        <span>/</span>
        <span>{entity.database_name}</span>
        <span>/</span>
        <span className="current">{entity.object_name}</span>
      </div>

      <section className="panel">
        <div className="entity-head">
          <div className={`entity-icon tone-${entity.object_type === "VIEW" ? "cyan" : "blue"}`}>
            <Icon name={objectIcon(entity.object_type)} size={22} strokeWidth={1.8} />
          </div>
          <div className="entity-head-main">
            <h2 className="entity-head-title">{entity.semantic_name ?? entity.object_name}</h2>
            <div className="entity-head-meta mono">
              {entity.schema_name}.{entity.object_name} • {entity.object_type} • {entity.source_name}.{entity.database_name}
            </div>
            <div className="entity-head-desc">
              {entity.description ?? <span className="muted">{t("entities.notYetInferred")}</span>}
            </div>
          </div>
          <div className="entity-head-side">
            <Pill tone={statusTone(entity.status)} solid>
              {t(`status.${entity.status}`)}
            </Pill>
            <Pill tone="blue">
              {t("entities.confidencePill", { pct: Math.round(entity.confidence * 100) })}
            </Pill>
            {entity.deep_analysis && <Pill tone="violet">{t("entities.deepAnalysisPill")}</Pill>}
          </div>
        </div>
      </section>

      <div className="cols wide-left">
        <Panel title={t("entities.fieldsCount", { n: entity.fields.length })} subtitle={t("entities.columnsSub")}>
          {entity.fields.length === 0 ? (
            <div className="empty">{t("entities.noFieldsYet")}</div>
          ) : (
            <div className="inner-table table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("entities.colColumn")}</th>
                    <th>{t("entities.colType")}</th>
                    <th>{t("entities.colKey")}</th>
                    <th>{t("entities.colSemanticName")}</th>
                    <th>{t("entities.colAccess")}</th>
                    <th>{t("entities.colStatus")}</th>
                  </tr>
                </thead>
                <tbody>
                  {entity.fields.map((f) => (
                    <tr key={f.id}>
                      <td className="strong mono" title={f.description ?? undefined}>
                        {f.column_name}
                        {f.sensitivity_flags.length > 0 && (
                          <span className="chips" style={{ marginTop: 4 }}>
                            {f.sensitivity_flags.map((s) => (
                              <span key={s} className="chip sensitive">
                                {s}
                              </span>
                            ))}
                          </span>
                        )}
                      </td>
                      <td className="mono">
                        {f.data_type}
                        {f.is_nullable ? <span className="cell-sub">{t("entities.nullable")}</span> : null}
                      </td>
                      <td className="strong">{f.is_primary_key ? "PK" : ""}</td>
                      <td>
                        {f.semantic_name ?? <span className="muted">{t("common.dash")}</span>}
                        {f.description && <span className="cell-sub">{f.description}</span>}
                      </td>
                      <td>
                        {f.access_level != null && (f.access_level !== entity.access_level || f.access_category !== entity.access_category) ? (
                          <CellChip level={f.access_level} category={f.access_category} size="sm" />
                        ) : (
                          <span className="muted small">{t("entities.sameAsTable")}</span>
                        )}
                      </td>
                      <td>
                        <StatusBadge status={f.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title={t("entities.factsTitle")} subtitle={t("entities.factsSub")}>
          <div className="kv-rows">
            {entity.access_level != null && (
              <div className="kv-row">
                <span className="k">{t("entities.dataAccess")}</span>
                <span className="v" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                  <CellChip level={entity.access_level} category={entity.access_category} showLabel />
                  <SourcePair obj={{ level_source: (entity.access_level_source ?? null) as never, category_source: (entity.access_category_source ?? null) as never, detail: {} }} />
                </span>
              </div>
            )}
            <div className="kv-row">
              <span className="k">{t("entities.semanticName")}</span>
              <span className="v">{entity.semantic_name ?? <span className="muted">{t("entities.notYetInferred")}</span>}</span>
            </div>
            <div className="kv-row">
              <span className="k">{t("entities.domain")}</span>
              <span className="v">{entity.domain ?? t("common.dash")}</span>
            </div>
            <div className="kv-row">
              <span className="k">{t("entities.classificationLabel")}</span>
              <span className="v">{entity.classification}</span>
            </div>
            <div className="kv-row">
              <span className="k">{t("entities.deepAnalysis")}</span>
              <span className={`v ${entity.deep_analysis ? "good" : ""}`}>{entity.deep_analysis ? t("common.yes") : t("entities.deepAnalysisNo")}</span>
            </div>
            <div className="kv-row">
              <span className="k">{t("entities.lastVerified")}</span>
              <span className="v">{fmtDate(entity.last_verified_at, t("common.never"))}</span>
            </div>
            {entity.synonyms.length > 0 && (
              <div className="kv-row">
                <span className="k">{t("glossary.alsoKnownAs")}</span>
                <span className="v chips" style={{ justifyContent: "flex-end" }}>
                  {entity.synonyms.map((s) => (
                    <span key={s} className="chip">
                      {s}
                    </span>
                  ))}
                </span>
              </div>
            )}
            {entity.sensitivity_flags.length > 0 && (
              <div className="kv-row">
                <span className="k">{t("entities.sensitivity")}</span>
                <span className="v chips" style={{ justifyContent: "flex-end" }}>
                  {entity.sensitivity_flags.map((f) => (
                    <span key={f} className="chip sensitive">
                      {f}
                    </span>
                  ))}
                </span>
              </div>
            )}
          </div>
        </Panel>
      </div>

      <Panel
        className="mt"
        title={t("entities.relationshipMap")}
        subtitle={rels ? t("entities.relationshipMapSub", { n: rels.length }) : t("entities.loadingRelationships")}
        actions={
          onViewRelationships && (
            <button className="btn-ghost btn-sm" onClick={() => onViewRelationships(entity.id)}>
              <Icon name="network" size={14} />
              {t("entities.openInRelationships")}
            </button>
          )
        }
      >
        {!rels ? (
          <div className="empty">{t("entities.loadingRelationships")}</div>
        ) : rels.length === 0 ? (
          <div className="empty">{t("entities.noRelationshipsYet")}</div>
        ) : (
          <div className="graph-canvas" style={{ padding: "12px 8px" }}>
            <EntityDiagram center={{ id: entity.id, name: `${entity.schema_name}.${entity.object_name}` }} relationships={rels} onSelectEntity={onOpen} />
          </div>
        )}
      </Panel>
    </div>
  );
}

export default function Entities({
  initialFilter,
  openRequest,
  onViewRelationships,
}: {
  initialFilter?: EntitiesFilter | null;
  openRequest?: { id: string; n: number } | null;
  onViewRelationships?: (entityId: string) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [database, setDatabase] = useState("");
  const [classification, setClassification] = useState("");
  const [objectType, setObjectType] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [entities, setEntities] = useState<EntityOut[] | null>(null);
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<EntityDetailOut | null>(null);

  // A box on Overview links here with a preset filter — applied once per
  // navigation (a fresh object reference each time), never re-applied on
  // an unrelated remount because App.tsx clears it when the user leaves.
  useEffect(() => {
    if (!initialFilter) return;
    setStatus(initialFilter.status ?? "");
    setDomain(initialFilter.domain ?? "");
    setDatabase(initialFilter.database ?? "");
    setClassification(initialFilter.classification ?? "");
    setObjectType(initialFilter.object_type ?? "");
    setSelected(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialFilter]);

  // Search result / graph node / referenced asset from another tab.
  useEffect(() => {
    if (openRequest) openEntity(openRequest.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest]);

  useEffect(() => {
    const handle = setTimeout(() => {
      api
        .allEntities({
          query: query || undefined,
          status: status || undefined,
          domain: domain || undefined,
          database: database || undefined,
          classification: classification || undefined,
          object_type: objectType || undefined,
          sensitivity: initialFilter?.sensitivity,
          deep_analysis: initialFilter?.deep_analysis,
        })
        .then((rows) => {
          setEntities(rows);
          setPage(0);
        })
        .catch((e) => setError(e.message));
    }, 250); // debounce keystrokes
    return () => clearTimeout(handle);
    // sensitivity/deep_analysis only ever come from a one-shot Overview
    // navigation, so they ride along with whatever else changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, status, domain, database, classification, objectType]);

  function openEntity(id: string) {
    api
      .entity(id)
      .then((d) => {
        setSelected(d);
        window.scrollTo({ top: 0 });
      })
      .catch((e) => setError(e.message));
  }

  function clearFilters() {
    setQuery("");
    setStatus("");
    setDomain("");
    setDatabase("");
    setClassification("");
    setObjectType("");
  }

  const activeFilterCount =
    [status, domain, database, classification, objectType].filter(Boolean).length +
    (initialFilter?.sensitivity ? 1 : 0) +
    (initialFilter?.deep_analysis != null ? 1 : 0);

  if (selected) {
    return <EntityDetail entity={selected} onBack={() => setSelected(null)} onOpen={openEntity} onViewRelationships={onViewRelationships} />;
  }

  const rows = entities ?? [];
  const pageRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <section className="panel">
        <div className="panel-body" style={{ paddingTop: 14, paddingBottom: 14 }}>
          <div className="toolbar">
            <label className="search-field" style={{ flex: "1 1 320px", maxWidth: 420 }}>
              <Icon name="search" size={15} strokeWidth={1.9} />
              <input type="text" placeholder={t("entities.searchPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <button className={`btn-ghost${showFilters ? " active" : ""}`} onClick={() => setShowFilters((v) => !v)} aria-expanded={showFilters}>
              <Icon name="filter" size={15} />
              {t("entities.filters")}
              {activeFilterCount > 0 && <span className="chip">{activeFilterCount}</span>}
            </button>
            {database && (
              <span className="chip">
                {t("entities.databaseChip", { database })}
                <span className="x" onClick={() => setDatabase("")}>×</span>
              </span>
            )}
            {domain && (
              <span className="chip">
                {t("entities.domainChip", { domain })}
                <span className="x" onClick={() => setDomain("")}>×</span>
              </span>
            )}
            {initialFilter?.sensitivity && <span className="chip sensitive">{initialFilter.sensitivity}</span>}
            {initialFilter?.deep_analysis && <span className="chip">{t("entities.deepAnalysisPill")}</span>}
            <span className="grow" />
            {activeFilterCount > 0 && (
              <button className="btn-ghost btn-sm" onClick={clearFilters}>
                {t("entities.clearFilters")}
              </button>
            )}
            {entities && <span className="pill tone-blue">{t("entities.resultCount", { n: entities.length.toLocaleString() })}</span>}
          </div>
          {showFilters && (
            <div className="toolbar" style={{ marginTop: 12 }}>
              <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("entities.colStatus")}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s ? t(`status.${s}`) : t("entities.allStatuses")}
                  </option>
                ))}
              </select>
              <select value={objectType} onChange={(e) => setObjectType(e.target.value)} aria-label={t("entities.colType")}>
                {OBJECT_TYPES.map((ty) => (
                  <option key={ty} value={ty}>
                    {ty || t("entities.allObjectTypes")}
                  </option>
                ))}
              </select>
              <select value={classification} onChange={(e) => setClassification(e.target.value)} aria-label={t("entities.colClassification")}>
                {CLASSIFICATIONS.map((c) => (
                  <option key={c} value={c}>
                    {c || t("entities.allClassifications")}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </section>

      {error && <div className="banner error">{error}</div>}

      {!entities ? (
        <div className="empty">{t("entities.loading")}</div>
      ) : entities.length === 0 ? (
        <div className="panel">
          <div className="empty">{t("entities.noMatch")}</div>
        </div>
      ) : (
        <div className="panel">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("entities.colObject")}</th>
                  <th>{t("entities.colType")}</th>
                  <th>{t("entities.colSource")}</th>
                  <th>{t("entities.colClassification")}</th>
                  <th className="num">{t("entities.colConfidence")}</th>
                  <th>{t("entities.colDeep")}</th>
                  <th>{t("entities.colAiState")}</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => openEntity(e.id)}>
                    <td className="strong">
                      {e.object_name}
                      <span className="cell-sub">{e.semantic_name ?? `${e.schema_name}.${e.object_name}`}</span>
                    </td>
                    <td className="strong">{e.object_type}</td>
                    <td className="strong">{e.database_name}</td>
                    <td className="muted">{e.classification}</td>
                    <td className="num">{Math.round(e.confidence * 100)}%</td>
                    <td>{e.deep_analysis ? <Icon name="check" size={16} style={{ color: "var(--violet)" }} /> : ""}</td>
                    <td>
                      <StatusBadge status={e.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={rows.length} onPage={(p) => { setPage(p); window.scrollTo({ top: 0 }); }} />
        </div>
      )}
    </div>
  );
}
