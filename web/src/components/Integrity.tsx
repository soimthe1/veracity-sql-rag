import { api, CatalogSummaryOut, EntitiesFilter, StatusOut } from "../api";
import { usePolling } from "../hooks";
import { useI18n } from "../i18n";
import { BarRow, Kpi, Panel, pct } from "./ui";

const SUMMARY_POLL_MS = 30_000;

/**
 * Data-quality/trust posture of the catalog itself — distinct from the
 * Health tab (the infrastructure running it). Every figure is read
 * straight from /v1/status and /v1/status/summary; nothing is estimated
 * client-side beyond percentages and sums over numbers the backend
 * already computed.
 */
const TRUST_SEGMENTS: { key: "trusted" | "learning" | "suspect" | "unknown" | "stale"; color: string; labelKey: string }[] = [
  { key: "trusted", color: "var(--green)", labelKey: "status.TRUSTED" },
  { key: "learning", color: "var(--yellow)", labelKey: "status.LEARNING" },
  { key: "suspect", color: "var(--orange)", labelKey: "status.SUSPECT" },
  { key: "unknown", color: "var(--gray)", labelKey: "status.UNKNOWN" },
  { key: "stale", color: "var(--red)", labelKey: "status.STALE" },
];

function confidenceLabel(v: number | null, t: (p: string) => string): string {
  if (v == null) return t("common.dash");
  if (v >= 0.8) return t("integrity.confidenceStrong");
  if (v >= 0.55) return t("integrity.confidenceDeveloping");
  return t("integrity.confidenceWeak");
}

export default function Integrity({ status, onGoToEntities }: { status: StatusOut; onGoToEntities: (filter: EntitiesFilter) => void }) {
  const { t } = useI18n();
  const { data: summary } = usePolling<CatalogSummaryOut>(() => api.summary(), SUMMARY_POLL_MS);

  // Share of ALL cataloged objects (every status), not of "understood"
  // ones — deep analysis also covers objects not yet named.
  const allObjects = status.entities_total;
  const total = Math.max(1, allObjects);
  const deepPct = pct(status.deep_analysis_entities, allObjects);
  const targetPct = status.coverage_deep_analysis_target_pct;
  const understoodPct = pct(status.entities_understood, allObjects);
  const relTotal = summary?.relationships_total ?? 0;
  const declaredPct = pct(summary?.relationships_declared_fk ?? 0, relTotal);

  // sensitivity_flags is unnested per (entity, flag) on the backend, so
  // this is a count of flag occurrences, not of entities.
  const sensitivityFlagCount = summary ? summary.sensitivity.reduce((s, x) => s + x.count, 0) : 0;
  const sensitivityCategoryCount = summary?.sensitivity.length ?? 0;
  const conf = status.avg_confidence;

  return (
    <div>
      <div className="kpis">
        <Kpi
          icon="brain"
          tone={conf == null ? "gray" : conf >= 0.8 ? "green" : conf >= 0.55 ? "amber" : "red"}
          tag={confidenceLabel(conf, t)}
          value={conf != null ? `${(conf * 100).toFixed(1)}%` : t("common.dash")}
          label={t("integrity.avgConfidence")}
        />
        <Kpi
          icon="refresh"
          tone="orange"
          tag={t("integrity.staleTag")}
          value={status.stale}
          label={t("status.STALE")}
          hint={t("integrity.staleHint")}
          onClick={() => onGoToEntities({ status: "STALE" })}
          title={t("integrity.viewStale")}
        />
        <Kpi
          icon="alert"
          tone="amber"
          tag={t("integrity.suspectTag")}
          value={status.suspect}
          label={t("status.SUSPECT")}
          hint={t("integrity.suspectHint")}
          onClick={() => onGoToEntities({ status: "SUSPECT" })}
          title={t("integrity.viewSuspect")}
        />
        <Kpi
          icon="lock"
          tone="red"
          tag={t("integrity.categoriesTag", { n: sensitivityCategoryCount })}
          value={sensitivityFlagCount}
          label={t("integrity.sensitivityExposure")}
          hint={t("integrity.sensitivityHint", { n: sensitivityCategoryCount })}
        />
        <Kpi
          icon="network"
          tone="cyan"
          tag={t("integrity.declaredTag", { pct: declaredPct.toFixed(0) })}
          value={summary ? relTotal : t("common.dash")}
          label={t("overview.relationships")}
        />
      </div>

      <Panel title={t("integrity.dimensionsTitle")} subtitle={t("integrity.dimensionsSub")}>
        <div className="bars" style={{ gap: 26 }}>
          <div>
            <div className="bar-row-head">
              <span className="bar-row-label">{t("integrity.trustDistribution")}</span>
              <span className="bar-row-value" style={{ color: "var(--green)" }}>
                {t("integrity.trustedShare", { pct: pct(status.trusted, allObjects).toFixed(1) })}
              </span>
            </div>
            <div className="bar-row-desc">{t("integrity.trustDistributionDesc", { n: allObjects })}</div>
            <div className="stacked-bar">
              {TRUST_SEGMENTS.map((seg) => {
                const v = status[seg.key];
                if (v <= 0) return null;
                return (
                  <div
                    key={seg.key}
                    className="clickable"
                    style={{ width: `${(v / total) * 100}%`, background: seg.color }}
                    title={`${t(seg.labelKey)}: ${v}`}
                    onClick={() => onGoToEntities({ status: seg.key.toUpperCase() })}
                  />
                );
              })}
            </div>
            <div className="legend">
              {TRUST_SEGMENTS.map((seg) => (
                <span key={seg.key} className="legend-item clickable" onClick={() => onGoToEntities({ status: seg.key.toUpperCase() })}>
                  <span className="swatch" style={{ background: seg.color }} />
                  {t(seg.labelKey)} <b>{status[seg.key]}</b>
                </span>
              ))}
            </div>
          </div>

          <BarRow
            label={t("integrity.semanticCoverage")}
            description={t("integrity.semanticCoverageDesc", { n: status.entities_understood, total: allObjects })}
            pct={understoodPct}
            value={`${understoodPct.toFixed(1)}%`}
            tone="violet"
          />
          <BarRow
            label={t("integrity.deepAnalysisCoverage")}
            description={t("integrity.deepAnalysisDetail", { n: status.deep_analysis_entities, pct: deepPct.toFixed(0), target: targetPct })}
            pct={deepPct}
            value={`${deepPct.toFixed(1)}%`}
            tone="blue"
            marker={targetPct}
            markerTitle={t("integrity.targetLabel", { target: targetPct })}
            onClick={() => onGoToEntities({ deep_analysis: true })}
          />
          {summary && summary.relationships_total > 0 && (
            <BarRow
              label={t("integrity.relationshipIntegrity")}
              description={t("integrity.relationshipDetail", {
                declared: summary.relationships_declared_fk,
                inferred: summary.relationships_inferred,
                pct: declaredPct.toFixed(0),
              })}
              pct={declaredPct}
              value={`${declaredPct.toFixed(1)}%`}
              tone="cyan"
            />
          )}
        </div>
      </Panel>

      {summary && summary.databases.some((d) => d.visibility_warning || d.last_error) && (
        <div className="panel">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("sources.colDatabase")}</th>
                  <th>{t("integrity.colIssue")}</th>
                  <th>{t("integrity.colDetail")}</th>
                </tr>
              </thead>
              <tbody>
                {summary.databases
                  .filter((d) => d.visibility_warning || d.last_error)
                  .map((d) => (
                    <tr key={`${d.source_name}.${d.database_name}`}>
                      <td className="strong">{d.database_name}</td>
                      <td>
                        {d.last_error ? (
                          <span className="badge error">{t("sources.scanError")}</span>
                        ) : d.visibility_warning === "none" ? (
                          <span className="badge error">{t("sources.noneVisible")}</span>
                        ) : (
                          <span className="badge learning">{t("sources.fewVisible")}</span>
                        )}
                      </td>
                      <td className="muted">{d.last_error ?? t("integrity.visibilityDetail", { n: d.objects_seen_last_discovery ?? 0 })}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
