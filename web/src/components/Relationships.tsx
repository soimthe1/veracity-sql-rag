import { useEffect, useMemo, useState } from "react";
import { api, EntityOut, RelationshipOut } from "../api";
import { useI18n } from "../i18n";
import Icon from "./Icon";
import StatusBadge from "./StatusBadge";
import EntityDiagram from "./EntityDiagram";
import SchemaGraph from "./SchemaGraph";
import { Pager, Panel, Segmented } from "./ui";

// Entities most likely to be a useful starting point for "how does this
// connect to things" — surfaced first in the browse list, since a
// SYSTEM_TECHNICAL or STAGING table almost never has relationships worth
// looking at.
const PRIORITY_CLASSIFICATIONS = new Set(["BUSINESS_ENTITY", "RELATIONSHIP_TABLE"]);
const PAGE_SIZE = 25;

type BrowseMode = "browse" | "schema";

function Legend() {
  const { t } = useI18n();
  return (
    <section className="panel">
      <div className="panel-body" style={{ paddingTop: 16, paddingBottom: 16 }}>
        <div className="legend" style={{ marginTop: 0, alignItems: "center" }}>
          <b style={{ color: "var(--text)", fontSize: 12 }}>{t("relationships.legend")}</b>
          <span className="legend-item">
            <span className="swatch line" />
            {t("relationships.legendDeclared")}
          </span>
          <span className="legend-item">
            <span className="swatch line dashed" />
            {t("relationships.legendInferred")}
          </span>
          <span className="legend-item">
            <span className="swatch" style={{ background: "var(--green)" }} />
            {t("status.TRUSTED")}
          </span>
          <span className="legend-item">
            <span className="swatch" style={{ background: "var(--yellow)" }} />
            {t("status.LEARNING")}
          </span>
          <span className="legend-item">
            <span className="swatch" style={{ background: "var(--orange)" }} />
            {t("status.SUSPECT")} / {t("status.STALE")}
          </span>
          <span className="legend-item">
            <span className="swatch" style={{ background: "var(--gray)" }} />
            {t("status.UNKNOWN")}
          </span>
        </div>
      </div>
    </section>
  );
}

export default function Relationships({
  initialEntityId,
  onOpenEntity,
}: {
  initialEntityId?: string | null;
  onOpenEntity?: (id: string) => void;
}) {
  const { t } = useI18n();
  const [all, setAll] = useState<EntityOut[] | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<EntityOut | null>(null);
  const [trustedOnly, setTrustedOnly] = useState(false);
  const [view, setView] = useState<"diagram" | "table">("diagram");
  const [browseMode, setBrowseMode] = useState<BrowseMode>("browse");
  const [rels, setRels] = useState<RelationshipOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A browsable list up front — nobody should need to know a table name
  // in advance. Fetched once; the search box filters what's in hand.
  useEffect(() => {
    api
      .allEntities({})
      .then((rows) => setAll(rows.filter((e) => e.object_type === "TABLE" || e.object_type === "VIEW")))
      .catch((e) => setError(e.message));
  }, []);

  // A link from elsewhere (Entities' "Open in Relationships") can jump
  // straight here with an entity already chosen.
  useEffect(() => {
    if (initialEntityId && all) {
      const match = all.find((e) => e.id === initialEntityId);
      if (match) setSelected(match);
      else selectById(initialEntityId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialEntityId, all]);

  useEffect(() => {
    if (!selected) {
      setRels(null);
      return;
    }
    setRels(null);
    api.entityRelationships(selected.id).then(setRels).catch((e) => setError(e.message));
  }, [selected]);

  const browseList = useMemo(() => {
    if (!all) return [];
    const q = query.trim().toLowerCase();
    const matches = !q
      ? all
      : all.filter(
          (e) =>
            e.object_name.toLowerCase().includes(q) ||
            (e.semantic_name ?? "").toLowerCase().includes(q) ||
            (e.domain ?? "").toLowerCase().includes(q) ||
            e.schema_name.toLowerCase().includes(q) ||
            e.database_name.toLowerCase().includes(q),
        );
    return [...matches].sort((a, b) => {
      const pa = PRIORITY_CLASSIFICATIONS.has(a.classification) ? 0 : 1;
      const pb = PRIORITY_CLASSIFICATIONS.has(b.classification) ? 0 : 1;
      if (pa !== pb) return pa - pb;
      return a.object_name.localeCompare(b.object_name);
    });
  }, [all, query]);

  useEffect(() => setPage(0), [query]);

  const visibleRels = rels?.filter((r) => !trustedOnly || r.status === "TRUSTED") ?? null;

  async function selectById(id: string) {
    const match = all?.find((e) => e.id === id);
    if (match) {
      setSelected(match);
      return;
    }
    try {
      setSelected(await api.entity(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (selected) {
    return (
      <div>
        <div className="crumbs">
          <button onClick={() => setSelected(null)}>{t("nav.Relationships")}</button>
          <span>/</span>
          <span>{selected.database_name}</span>
          <span>/</span>
          <span className="current">{selected.object_name}</span>
        </div>
        <Panel
          headerOnly
          title={t("relationships.diagramTitle", { name: selected.object_name })}
          subtitle={visibleRels ? t("relationships.relationshipsFor", { n: visibleRels.length }) : t("common.loading")}
          actions={
            <>
              <label className="toggle">
                <input type="checkbox" checked={trustedOnly} onChange={(e) => setTrustedOnly(e.target.checked)} />
                {t("relationships.trustedOnlyLabel")}
              </label>
              <Segmented
                value={view}
                onChange={setView}
                options={[
                  { value: "diagram", label: t("relationships.diagram"), icon: "network" },
                  { value: "table", label: t("relationships.table"), icon: "list" },
                ]}
              />
              {onOpenEntity && (
                <button className="btn-ghost btn-sm" onClick={() => onOpenEntity(selected.id)}>
                  <Icon name="table" size={14} />
                  {t("relationships.openEntity")}
                </button>
              )}
            </>
          }
        />

        {error && <div className="banner error">{error}</div>}

        {!visibleRels ? (
          <div className="panel">
            <div className="empty">{t("common.loading")}</div>
          </div>
        ) : visibleRels.length === 0 ? (
          <div className="panel">
            <div className="empty">{trustedOnly ? t("relationships.noTrustedYet") : t("relationships.noneForEntity")}</div>
          </div>
        ) : view === "diagram" ? (
          <>
            <section className="panel">
              <div className="panel-body" style={{ paddingTop: 20 }}>
                <EntityDiagram
                  center={{ id: selected.id, name: `${selected.schema_name}.${selected.object_name}` }}
                  relationships={visibleRels}
                  onSelectEntity={selectById}
                />
              </div>
            </section>
            <Legend />
          </>
        ) : (
          <div className="panel">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("relationships.colFrom")}</th>
                    <th></th>
                    <th>{t("relationships.colTo")}</th>
                    <th>{t("relationships.colRelationType")}</th>
                    <th className="num">{t("relationships.colConfidence")}</th>
                    <th>{t("relationships.colStatus2")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRels.map((r) => {
                    const other = r.from_entity_id === selected.id ? r.to_entity_id : r.from_entity_id;
                    return (
                      <tr key={r.id} className="clickable" onClick={() => selectById(other)} title={t("relationships.drillIn")}>
                        <td className="strong">
                          {r.from_entity_name}
                          <span className="cell-sub mono">{r.from_field ?? t("common.dash")}</span>
                        </td>
                        <td className="muted">
                          <Icon name="chevron" size={14} className="flip-rtl" />
                        </td>
                        <td className="strong">
                          {r.to_entity_name}
                          <span className="cell-sub mono">{r.to_field ?? t("common.dash")}</span>
                        </td>
                        <td className="strong">{r.is_declared_fk ? t("relationships.typeDeclared") : t("relationships.typeInferred")}</td>
                        <td className="num">{Math.round(r.confidence * 100)}%</td>
                        <td>
                          <StatusBadge status={r.status} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    );
  }

  const pageRows = browseList.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <Panel
        headerOnly
        title={t("relationships.explorerTitle")}
        subtitle={t("relationships.explorerSub")}
        actions={
          <Segmented
            value={browseMode}
            onChange={setBrowseMode}
            options={[
              { value: "browse", label: t("relationships.browseTables"), icon: "list" },
              { value: "schema", label: t("relationships.schemaMap"), icon: "network" },
            ]}
          />
        }
      />

      {error && <div className="banner error">{error}</div>}

      {browseMode === "schema" ? (
        <Panel title={t("schemaGraph.title")} subtitle={t("schemaGraph.subtitle")}>
          <SchemaGraph onSelectEntity={selectById} />
        </Panel>
      ) : !all ? (
        <div className="empty">{t("relationships.loadingEntities")}</div>
      ) : all.length === 0 ? (
        <div className="panel">
          <div className="empty">{t("relationships.noneYet")}</div>
        </div>
      ) : (
        <div className="panel">
          <div className="panel-body" style={{ paddingBottom: 14 }}>
            <div className="toolbar">
              <label className="search-field" style={{ flex: "1 1 320px", maxWidth: 520 }}>
                <Icon name="search" size={15} strokeWidth={1.9} />
                <input type="text" placeholder={t("relationships.filterPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} />
              </label>
              <span className="grow" />
              <span className="muted small">{t("relationships.pickTitle")}</span>
            </div>
          </div>
          {browseList.length === 0 ? (
            <div className="empty">{t("relationships.noMatch", { query })}</div>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("relationships.colObject")}</th>
                      <th>{t("relationships.colSemanticClass")}</th>
                      <th>{t("entities.colSource")}</th>
                      <th>{t("relationships.colStatus")}</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((m) => (
                      <tr key={m.id} className="clickable" onClick={() => setSelected(m)}>
                        <td className="strong">
                          {m.object_name}
                          <span className="cell-sub mono">{m.schema_name}</span>
                        </td>
                        <td>
                          {m.semantic_name ?? <span className="muted">{t("common.dash")}</span>}
                          <span className="cell-sub">{m.classification}</span>
                        </td>
                        <td className="strong">{m.database_name}</td>
                        <td>
                          <StatusBadge status={m.status} />
                        </td>
                        <td className="muted" style={{ width: 30 }}>
                          <Icon name="chevron" size={14} className="flip-rtl" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager page={page} pageSize={PAGE_SIZE} total={browseList.length} onPage={setPage} />
            </>
          )}
        </div>
      )}
      {browseMode === "browse" && all && all.length > 0 && (
        <section className="panel">
          <div className="panel-body muted small" style={{ paddingTop: 14, paddingBottom: 14 }}>
            {t("relationships.tip")}
          </div>
        </section>
      )}
    </div>
  );
}
