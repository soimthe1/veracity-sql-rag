import { useEffect, useMemo, useState } from "react";
import { api, EntitiesFilter, EntityOut } from "../api";
import { useI18n } from "../i18n";
import Icon from "./Icon";
import StatusBadge from "./StatusBadge";
import { BarRow, Pager, Panel, Pill, statusTone } from "./ui";

/**
 * The catalog's business glossary: every entity the catalog has named and
 * described (any status except STALE — an UNKNOWN-trust term is still a
 * real, useful definition; its state says how confirmed it is), filtered
 * by business domain or searched by term, synonym or description.
 * Nothing here is invented: every term, definition and synonym comes
 * straight from semantic inference (EntityOut.semantic_name /
 * description / synonyms), the same fields the Entities tab reads.
 */
const HIDDEN_STATUSES = new Set(["STALE"]);
const PAGE_SIZE = 25;
const NONE = "__none__";

export default function Glossary({
  onGoToEntities,
  onOpenEntity,
}: {
  onGoToEntities: (filter: EntitiesFilter) => void;
  onOpenEntity?: (id: string) => void;
}) {
  const { t } = useI18n();
  const [entities, setEntities] = useState<EntityOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [domain, setDomain] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [selectedTerm, setSelectedTerm] = useState<EntityOut | null>(null);

  useEffect(() => {
    api
      .allEntities()
      .then(setEntities)
      .catch((e) => setError(e.message));
  }, []);

  const terms = useMemo(
    () =>
      (entities ?? [])
        .filter((e) => e.semantic_name && !HIDDEN_STATUSES.has(e.status))
        .sort((a, b) => (a.semantic_name ?? "").localeCompare(b.semantic_name ?? "")),
    [entities],
  );

  const domains = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of terms) counts.set(e.domain ?? NONE, (counts.get(e.domain ?? NONE) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [terms]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return terms.filter(
      (e) =>
        (domain === null || (e.domain ?? NONE) === domain) &&
        (!q ||
          (e.semantic_name ?? "").toLowerCase().includes(q) ||
          (e.description ?? "").toLowerCase().includes(q) ||
          e.synonyms.some((s) => s.toLowerCase().includes(q)) ||
          e.object_name.toLowerCase().includes(q)),
    );
  }, [terms, query, domain]);

  useEffect(() => setPage(0), [query, domain]);

  if (error) return <div className="banner error">{error}</div>;
  if (!entities) return <div className="empty">{t("common.loading")}</div>;

  const domainLabel = (key: string) => (key === NONE ? t("glossary.unclassified") : key);

  if (selectedTerm) {
    const e = selectedTerm;
    return (
      <div>
        <div className="crumbs">
          <button onClick={() => setSelectedTerm(null)}>{t("nav.Glossary")}</button>
          <span>/</span>
          <span>{domainLabel(e.domain ?? NONE)}</span>
          <span>/</span>
          <span className="current">{e.semantic_name}</span>
        </div>
        <section className="panel">
          <div className="entity-head">
            <div className="entity-icon tone-violet">
              <Icon name="book" size={22} strokeWidth={1.8} />
            </div>
            <div className="entity-head-main">
              <h2 className="entity-head-title">{e.semantic_name}</h2>
              <div className="entity-head-meta">{domainLabel(e.domain ?? NONE)}</div>
              <div className="entity-head-desc">{e.description ?? <span className="muted">{t("glossary.noDescription")}</span>}</div>
            </div>
            <div className="entity-head-side">
              <Pill tone={statusTone(e.status)} solid>
                {t(`status.${e.status}`)}
              </Pill>
              <Pill tone="blue">{t("entities.confidencePill", { pct: Math.round(e.confidence * 100) })}</Pill>
            </div>
          </div>
        </section>
        <Panel
          title={t("glossary.mappingTitle")}
          subtitle={t("glossary.mappingSub")}
          actions={
            <>
              {onOpenEntity && (
                <button className="btn-ghost btn-sm" onClick={() => onOpenEntity(e.id)}>
                  <Icon name="table" size={14} />
                  {t("glossary.openEntity")}
                </button>
              )}
              {e.domain && (
                <button className="btn-ghost btn-sm" onClick={() => onGoToEntities({ domain: e.domain ?? undefined })}>
                  {t("glossary.viewInEntities")}
                </button>
              )}
            </>
          }
        >
          <div className="kv-rows">
            <div className="kv-row">
              <span className="k">{t("glossary.technicalSource")}</span>
              <span className="v mono">
                {e.source_name}.{e.database_name}.{e.schema_name}.{e.object_name}
              </span>
            </div>
            <div className="kv-row">
              <span className="k">{t("entities.typeClassification")}</span>
              <span className="v">
                {e.object_type} · {e.classification}
              </span>
            </div>
            {e.synonyms.length > 0 && (
              <div className="kv-row">
                <span className="k">{t("glossary.alsoKnownAs")}</span>
                <span className="v chips" style={{ justifyContent: "flex-end" }}>
                  {e.synonyms.map((s) => (
                    <span key={s} className="chip">
                      {s}
                    </span>
                  ))}
                </span>
              </div>
            )}
          </div>
        </Panel>
      </div>
    );
  }

  const allObjects = entities.length;
  const coveragePct = allObjects ? (terms.length / allObjects) * 100 : 0;
  const trustedTerms = terms.filter((e) => e.status === "TRUSTED").length;
  const pageRows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <Panel
        headerOnly
        title={t("glossary.title")}
        subtitle={t("glossary.subtitle")}
        actions={
          <label className="search-field" style={{ width: 320 }}>
            <Icon name="search" size={15} strokeWidth={1.9} />
            <input type="text" placeholder={t("glossary.searchPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
        }
      />

      {terms.length === 0 ? (
        <div className="panel">
          <div className="empty">{t("glossary.empty")}</div>
        </div>
      ) : (
        <>
          <div className="toolbar" style={{ marginBottom: 14 }}>
            <button className={`filter-chip${domain === null ? " active" : ""}`} onClick={() => setDomain(null)}>
              {t("glossary.allDomains")} <span className="n">{terms.length}</span>
            </button>
            {domains.map(([key, n]) => (
              <button key={key} className={`filter-chip${domain === key ? " active" : ""}`} onClick={() => setDomain(domain === key ? null : key)}>
                {domainLabel(key)} <span className="n">{n}</span>
              </button>
            ))}
          </div>

          <div className="panel">
            {filtered.length === 0 ? (
              <div className="empty">{t("glossary.noMatches")}</div>
            ) : (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("glossary.colTerm")}</th>
                        <th>{t("glossary.definition")}</th>
                        <th>{t("entities.domain")}</th>
                        <th>{t("glossary.colAsset")}</th>
                        <th>{t("glossary.status")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map((e) => (
                        <tr key={e.id} className="clickable" onClick={() => setSelectedTerm(e)}>
                          <td className="strong" style={{ minWidth: 160 }}>
                            {e.semantic_name}
                            {e.synonyms.length > 0 && <span className="cell-sub">{e.synonyms.slice(0, 3).join(", ")}</span>}
                          </td>
                          <td style={{ maxWidth: 460 }}>{e.description ?? <span className="muted">{t("glossary.noDescription")}</span>}</td>
                          <td className="nowrap">{domainLabel(e.domain ?? NONE)}</td>
                          <td className="mono">
                            {e.object_name}
                            <span className="cell-sub">{e.database_name}</span>
                          </td>
                          <td>
                            <StatusBadge status={e.status} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager page={page} pageSize={PAGE_SIZE} total={filtered.length} onPage={setPage} />
              </>
            )}
          </div>
        </>
      )}

      <Panel title={t("glossary.coverageTitle")} subtitle={t("glossary.coverageSub")}>
        <BarRow label={t("glossary.coverageLabel")} pct={coveragePct} value={`${coveragePct.toFixed(0)}%`} tone="violet" />
        <div className="muted small" style={{ marginTop: 12 }}>
          {t("glossary.coverageMeta", { terms: terms.length, objects: allObjects, domains: domains.length, trusted: trustedTerms })}
        </div>
      </Panel>
    </div>
  );
}
