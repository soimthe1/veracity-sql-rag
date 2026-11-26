import { api, EntitiesFilter, StatusOut, CatalogSummaryOut, LabeledCountOut } from "../api";
import { usePolling } from "../hooks";
import { useI18n } from "../i18n";
import TrustDonut from "./TrustDonut";
import { BarRow, Kpi, Panel, pct, Tone } from "./ui";

const SUMMARY_POLL_MS = 30_000;

const TRUST_KEYS = [
  ["TRUSTED", "trusted", "var(--green)"],
  ["LEARNING", "learning", "var(--yellow)"],
  ["SUSPECT", "suspect", "var(--orange)"],
  ["UNKNOWN", "unknown", "var(--gray)"],
  ["STALE", "stale", "var(--red)"],
] as const;

/** A card of clickable proportional bars (domains, object types, …). */
function Distribution({
  title,
  subtitle,
  items,
  tone,
  maxItems = 8,
  emptyText,
  onItemClick,
  itemTitle,
}: {
  title: string;
  subtitle: string;
  items: LabeledCountOut[];
  tone: Tone;
  maxItems?: number;
  emptyText: string;
  onItemClick?: (item: LabeledCountOut) => void;
  itemTitle?: (item: LabeledCountOut) => string;
}) {
  const { t } = useI18n();
  const shown = items.slice(0, maxItems);
  const rest = items.length - shown.length;
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <Panel title={title} subtitle={subtitle}>
      {shown.length === 0 ? (
        <div className="empty" style={{ padding: "18px 0" }}>{emptyText}</div>
      ) : (
        <div className="bars compact">
          {shown.map((item) => (
            <BarRow
              key={item.key}
              label={item.label}
              value={item.count}
              pct={(item.count / max) * 100}
              tone={tone}
              onClick={onItemClick ? () => onItemClick(item) : undefined}
              title={itemTitle ? itemTitle(item) : undefined}
            />
          ))}
          {rest > 0 && <div className="faint small">{t("overview.moreCount", { n: rest })}</div>}
        </div>
      )}
    </Panel>
  );
}

export default function Overview({
  status,
  onGoToEntities,
  onGoToSources,
  onGoToRelationships,
}: {
  status: StatusOut;
  onGoToEntities: (filter: EntitiesFilter) => void;
  onGoToSources: () => void;
  onGoToRelationships: () => void;
}) {
  const { t } = useI18n();
  const { data: summary } = usePolling<CatalogSummaryOut>(() => api.summary(), SUMMARY_POLL_MS);

  if (!status.enabled) {
    return <div className="banner warn">{t("overview.disabledBanner", { cfg: "catalog.enabled: false" })}</div>;
  }
  if (!status.catalog_db_connected) {
    return <div className="banner error">{t("overview.dbUnreachableBanner", { cfg: "CATALOG_DB_*" })}</div>;
  }

  const allObjects = status.entities_total;
  const targetPct = status.coverage_deep_analysis_target_pct;
  // ACTUAL coverage (share of all objects deep-analyzed), not the target.
  const deepPct = pct(status.deep_analysis_entities, allObjects);
  const understoodPct = pct(status.entities_understood, allObjects);
  const trustedPct = pct(status.trusted, allObjects);
  const relTotal = summary?.relationships_total ?? 0;
  const declaredPct = pct(summary?.relationships_declared_fk ?? 0, relTotal);
  const trustCounts = {
    TRUSTED: status.trusted,
    LEARNING: status.learning,
    SUSPECT: status.suspect,
    UNKNOWN: status.unknown,
    STALE: status.stale,
  };

  return (
    <>
      <div className="kpis">
        <Kpi
          icon="database"
          tone="blue"
          tag={t("overview.tagDatabases", { n: status.databases })}
          value={status.sources}
          label={t("overview.sources")}
          onClick={onGoToSources}
          title={t("overview.viewInSources")}
        />
        <Kpi
          icon="table"
          tone="violet"
          tag={t("overview.tagUnderstood", { n: status.entities_understood })}
          value={status.tables.toLocaleString()}
          label={t("overview.tablesDiscovered")}
          onClick={() => onGoToEntities({ object_type: "TABLE" })}
          title={t("overview.viewTablesInEntities")}
        />
        <Kpi
          icon="network"
          tone="cyan"
          tag={summary ? t("overview.tagDeclared", { n: summary.relationships_declared_fk }) : ""}
          value={summary ? relTotal.toLocaleString() : t("common.dash")}
          label={t("overview.relationships")}
          onClick={onGoToRelationships}
          title={t("overview.viewInRelationships")}
        />
        <Kpi
          icon="shield"
          tone="green"
          tag={t("overview.tagOfObjects", { pct: trustedPct.toFixed(1) })}
          value={status.trusted.toLocaleString()}
          label={t("overview.trustedEntities")}
          onClick={() => onGoToEntities({ status: "TRUSTED" })}
          title={t("overview.viewStatusEntities", { status: t("status.TRUSTED") })}
        />
        <Kpi
          icon="brain"
          tone="amber"
          tag={t("overview.tagAcrossEntities")}
          value={status.avg_confidence != null ? `${(status.avg_confidence * 100).toFixed(1)}%` : t("common.dash")}
          label={t("overview.avgConfidence")}
          onClick={() => onGoToEntities({})}
          title={t("overview.viewInEntities")}
        />
      </div>

      <div className="cols wide-left">
        <Panel title={t("overview.trustStates")} subtitle={t("overview.trustStatesSub", { n: allObjects.toLocaleString() })}>
          <div style={{ display: "flex", alignItems: "center", gap: 32, flexWrap: "wrap", paddingTop: 6 }}>
            <TrustDonut counts={trustCounts} size={170} onSelect={(s) => onGoToEntities({ status: s })} unitLabel={t("overview.entitiesUnit")} />
            <div className="list-rows" style={{ flex: 1, minWidth: 220 }}>
              {TRUST_KEYS.map(([label, key, color]) => (
                <div
                  key={label}
                  className="list-row clickable"
                  onClick={() => onGoToEntities({ status: label })}
                  title={t("overview.viewStatusEntities", { status: t(`status.${label}`) })}
                >
                  <span className="swatch" style={{ background: color }} />
                  <span className="list-row-main">{t(`status.${label}`)}</span>
                  <span className="list-row-meta">{pct(status[key], allObjects).toFixed(1)}%</span>
                  <span className="list-row-val" style={{ minWidth: 48, textAlign: "end" }}>{status[key].toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel title={t("overview.coverageTitle")} subtitle={t("overview.coverageSub")}>
          <div className="bars">
            <BarRow
              label={t("overview.covUnderstood")}
              pct={understoodPct}
              value={`${understoodPct.toFixed(0)}%`}
              tone="green"
              onClick={() => onGoToEntities({})}
              title={t("overview.covUnderstoodHint", { n: status.entities_understood, total: allObjects })}
            />
            <BarRow
              label={t("overview.covTrusted")}
              pct={trustedPct}
              value={`${trustedPct.toFixed(0)}%`}
              tone="blue"
              onClick={() => onGoToEntities({ status: "TRUSTED" })}
            />
            <BarRow
              label={t("overview.deepAnalysisCoverage")}
              pct={deepPct}
              value={`${deepPct.toFixed(0)}%`}
              tone="violet"
              marker={targetPct}
              markerTitle={t("integrity.targetLabel", { target: targetPct })}
              onClick={() => onGoToEntities({ deep_analysis: true })}
              title={t("overview.deepAnalysisValue", { n: status.deep_analysis_entities, total: allObjects, share: deepPct.toFixed(0), pct: targetPct })}
            />
            <BarRow
              label={t("overview.covDeclared")}
              pct={declaredPct}
              value={summary ? `${declaredPct.toFixed(0)}%` : t("common.dash")}
              tone="cyan"
              onClick={onGoToRelationships}
              title={summary ? t("integrity.relationshipDetail", { declared: summary.relationships_declared_fk, inferred: summary.relationships_inferred, pct: declaredPct.toFixed(0) }) : undefined}
            />
          </div>
        </Panel>
      </div>

      {summary ? (
        <>
          <div className="cols wide-left">
            <Panel title={t("overview.databasesCard")} subtitle={t("overview.databasesCardSub", { n: summary.databases.length })}>
              {summary.databases.length === 0 ? (
                <div className="empty">{t("overview.objectTypesEmpty")}</div>
              ) : (
                <div className="list-rows">
                  {summary.databases.slice(0, 10).map((d) => {
                    const total = Math.max(1, d.trusted + d.learning + d.unknown);
                    const state = d.last_error ? "off" : d.visibility_warning ? "warn" : "on";
                    return (
                      <div
                        key={`${d.source_name}.${d.database_name}`}
                        className={`list-row${d.objects ? " clickable" : ""}`}
                        onClick={d.objects ? () => onGoToEntities({ database: d.database_name }) : undefined}
                        title={t("overview.databaseTooltip", { tables: d.tables, views: d.views, trusted: d.trusted, learning: d.learning })}
                      >
                        <span className={`dot ${state}`} />
                        <span className="list-row-main">
                          {d.database_name}
                          <span className="list-row-sub">
                            {d.last_error
                              ? t("sources.scanError")
                              : d.visibility_warning === "none"
                                ? t("sources.noneVisible")
                                : d.visibility_warning === "few"
                                  ? t("sources.fewVisible")
                                  : t("overview.dbObjects", { n: d.objects, tables: d.tables, views: d.views })}
                          </span>
                        </span>
                        <div className="mini-stack" style={{ width: 120 }}>
                          {d.trusted > 0 && <div style={{ width: `${(d.trusted / total) * 100}%`, background: "var(--green)" }} />}
                          {d.learning > 0 && <div style={{ width: `${(d.learning / total) * 100}%`, background: "var(--yellow)" }} />}
                          {d.unknown > 0 && <div style={{ width: `${(d.unknown / total) * 100}%`, background: "var(--gray)" }} />}
                        </div>
                        <span className="list-row-meta" style={{ minWidth: 44, textAlign: "end" }}>
                          {d.avg_confidence != null ? `${Math.round(d.avg_confidence * 100)}%` : t("common.dash")}
                        </span>
                      </div>
                    );
                  })}
                  {summary.databases.length > 10 && (
                    <div className="list-row clickable" onClick={onGoToSources}>
                      <span className="list-row-main muted">{t("overview.moreCount", { n: summary.databases.length - 10 })}</span>
                    </div>
                  )}
                </div>
              )}
            </Panel>
            <Distribution
              title={t("overview.domains")}
              subtitle={t("overview.domainsSub", { n: summary.domains.length, plural: summary.domains.length === 1 ? "" : "s" })}
              items={summary.domains}
              tone="violet"
              maxItems={9}
              emptyText={t("overview.domainsEmpty")}
              onItemClick={(item) => onGoToEntities({ domain: item.key })}
              itemTitle={(item) => t("overview.viewEntitiesInDomain", { label: item.label })}
            />
          </div>

          <div className="cols three">
            <Distribution
              title={t("overview.objectTypes")}
              subtitle={t("overview.objectTypesSub")}
              items={summary.object_types}
              tone="blue"
              emptyText={t("overview.objectTypesEmpty")}
              onItemClick={(item) => onGoToEntities({ object_type: item.key })}
            />
            <Distribution
              title={t("overview.classification")}
              subtitle={t("overview.classificationSub")}
              items={summary.classifications}
              tone="green"
              emptyText={t("overview.classificationEmpty")}
              onItemClick={(item) => onGoToEntities({ classification: item.key })}
            />
            <Distribution
              title={t("overview.sensitivity")}
              subtitle={t("overview.sensitivitySub")}
              items={summary.sensitivity}
              tone="red"
              emptyText={t("overview.sensitivityEmpty")}
              onItemClick={(item) => onGoToEntities({ sensitivity: item.key })}
            />
          </div>
        </>
      ) : (
        <div className="empty">{t("overview.loadingSummary")}</div>
      )}
    </>
  );
}
