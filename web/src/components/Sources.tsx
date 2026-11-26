import { useEffect, useState } from "react";
import { api, CatalogSummaryOut, CoverageTargetOut, DatabaseSummaryOut, EntitiesFilter, SourceConfigOut, SourceOut } from "../api";
import { useI18n } from "../i18n";
import { fmtDate, relTime } from "../lib/format";
import StatusBadge from "./StatusBadge";
import { Panel } from "./ui";

/**
 * Sources tab — deliberately INSTANCE-level only. Shows which SQL
 * Server (or Always-On listener — just a host:port to this app) each
 * source points at, the login configured for it, and TLS/timeout
 * settings — never a per-database control. Which databases and objects
 * get analyzed is delegated to autodiscovery + the catalog-depth knob
 * (Security tab), shown here only as read-only facts.
 */
function TrustBar({ d }: { d: DatabaseSummaryOut }) {
  const total = Math.max(1, d.trusted + d.learning + d.unknown);
  const seg = (n: number, color: string) => (n ? <div style={{ width: `${(n / total) * 100}%`, background: color }} /> : null);
  return (
    <div className="mini-stack" title={`TRUSTED ${d.trusted} · LEARNING ${d.learning} · UNKNOWN ${d.unknown}`}>
      {seg(d.trusted, "var(--green)")}
      {seg(d.learning, "var(--yellow)")}
      {seg(d.unknown, "var(--gray)")}
    </div>
  );
}

function fmtHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(1)}h`;
}

/** Every database on the instance(s): what's cataloged, how trusted it
 * is, what the last discovery scan could actually see — and a warning
 * where it saw (almost) nothing, which usually means the read-only login
 * lacks access there rather than an empty database. */
function DatabasesPanel({
  databases,
  onGoToEntities,
}: {
  databases: DatabaseSummaryOut[];
  onGoToEntities?: (filter: EntitiesFilter) => void;
}) {
  const { t } = useI18n();
  const warned = databases.filter((d) => d.visibility_warning);
  return (
    <Panel title={t("sources.databasesHeading", { n: databases.length })} subtitle={t("sources.databasesSub")} bodyClass="panel-body flush">
      {warned.length > 0 && (
        <div className="banner warn" style={{ margin: "0 20px 14px" }}>
          {t("sources.visibilityBanner", { names: warned.map((d) => d.database_name).join(", ") })}
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("sources.colDatabase")}</th>
              <th className="num">{t("sources.colTables")}</th>
              <th className="num">{t("sources.colViews")}</th>
              <th className="num">{t("sources.colProgrammable")}</th>
              <th>{t("sources.colTrust")}</th>
              <th className="num">{t("sources.colConfidence")}</th>
              <th className="num">{t("sources.colSeen")}</th>
              <th>{t("sources.colNote")}</th>
            </tr>
          </thead>
          <tbody>
            {databases.map((d) => (
              <tr
                key={`${d.source_name}.${d.database_name}`}
                className={onGoToEntities && d.objects ? "clickable" : ""}
                onClick={onGoToEntities && d.objects ? () => onGoToEntities({ database: d.database_name }) : undefined}
                title={onGoToEntities && d.objects ? t("sources.openInEntities") : undefined}
              >
                <td className="strong">{d.database_name}</td>
                <td className="num">{d.tables}</td>
                <td className="num">{d.views}</td>
                <td className="num">{d.programmable}</td>
                <td style={{ minWidth: 120 }}>
                  <TrustBar d={d} />
                </td>
                <td className="num">{d.avg_confidence != null ? `${Math.round(d.avg_confidence * 100)}%` : t("common.dash")}</td>
                <td className="num">{d.objects_seen_last_discovery ?? t("common.dash")}</td>
                <td>
                  {d.last_error ? (
                    <span className="chip sensitive" title={d.last_error}>{t("sources.scanError")}</span>
                  ) : d.visibility_warning === "none" ? (
                    <span className="chip sensitive">{t("sources.noneVisible")}</span>
                  ) : d.visibility_warning === "few" ? (
                    <span className="chip warn-chip">{t("sources.fewVisible")}</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SourceDetails({ c }: { c: SourceConfigOut }) {
  const { t } = useI18n();
  return (
    <Panel
      title={
        <>
          {c.name}
          {!c.enabled && <span className="chip neutral">{t("sources.disabled")}</span>}
        </>
      }
      subtitle={t("sources.connectionSub")}
    >
      <div className="kv-rows">
        <div className="kv-row">
          <span className="k">{t("sources.instance")}</span>
          <span className="v mono">{c.host ? `${c.host}:${c.port}` : t("common.notConfigured")}</span>
        </div>
        <div className="kv-row">
          <span className="k">{t("sources.driver")}</span>
          <span className="v mono">{c.driver}</span>
        </div>
        <div className="kv-row">
          <span className="k">{t("sources.login")}</span>
          <span className="v">
            <span className="mono">{c.login_user || t("common.dash")}</span> <span className="muted small">{t("sources.loginHint")}</span>
          </span>
        </div>
        <div className="kv-row">
          <span className="k">{t("sources.tls")}</span>
          <span className={`v ${c.encrypt ? "good" : "warn"}`}>
            {c.encrypt ? t("sources.encrypted") : t("sources.unencrypted")}
            {c.trust_server_certificate ? t("sources.trustingCert") : ""}
          </span>
        </div>
        <div className="kv-row">
          <span className="k">{t("sources.connectTimeout")}</span>
          <span className="v">{c.connect_timeout_seconds}s</span>
        </div>
        {c.consecutive_failures > 0 && (
          <div className="kv-row">
            <span className="k">{t("sources.consecutiveFailures")}</span>
            <span className="v bad" title={c.last_error ?? undefined}>
              {c.consecutive_failures}
              {c.last_error ? ` — ${c.last_error}` : ""}
            </span>
          </div>
        )}
      </div>
    </Panel>
  );
}

export default function Sources({ onGoToEntities }: { onGoToEntities?: (filter: EntitiesFilter) => void } = {}) {
  const { t } = useI18n();
  const [sources, setSources] = useState<SourceOut[] | null>(null);
  const [summary, setSummary] = useState<CatalogSummaryOut | null>(null);
  const [coverage, setCoverage] = useState<CoverageTargetOut | null>(null);
  const [config, setConfig] = useState<SourceConfigOut[] | "forbidden" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.sources().then(setSources).catch((e) => setError(e.message));
    api.summary().then(setSummary).catch(() => void 0);
    api.coverageTarget().then(setCoverage).catch(() => void 0);
    api
      .sourceConfig()
      .then(setConfig)
      .catch((e) => {
        if (e.status === 403) setConfig("forbidden");
        else setError(e.message);
      });
  }, []);

  if (error) return <div className="banner error">{error}</div>;
  if (!sources) return <div className="empty">{t("common.loading")}</div>;
  if (sources.length === 0 && (!config || config === "forbidden" || config.length === 0)) {
    return <div className="empty">{t("sources.noneConfigured", { cfg: "catalog.sources" })}</div>;
  }

  const configs = Array.isArray(config) ? config : [];
  const cfgByName = new Map(configs.map((c) => [c.name, c]));
  const cycleByName = new Map((summary?.sources ?? []).map((s) => [s.name, s]));
  const nowMs = summary ? new Date(summary.generated_at).getTime() : Date.now();
  const autodiscover = configs.length > 0 && configs.every((c) => c.autodiscover_databases);

  return (
    <div>
      <Panel headerOnly title={t("sources.connectedTitle")} subtitle={t("sources.connectedSub")} />
      {config === "forbidden" && <div className="banner warn">{t("sources.forbiddenBanner")}</div>}

      <div className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("sources.tableName")}</th>
                <th>{t("sources.colEngine")}</th>
                <th>{t("sources.tableHost")}</th>
                <th className="num">{t("sources.tableDatabases")}</th>
                <th>{t("sources.tableLastDiscovery")}</th>
                <th>{t("sources.tableLastValidation")}</th>
                <th>{t("sources.tableStatus")}</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const c = cfgByName.get(s.name);
                return (
                  <tr key={s.id}>
                    <td className="strong">{s.name}</td>
                    <td>{c ? <span className="mono">{c.driver}</span> : <span className="muted">—</span>}</td>
                    <td className="mono">{s.host}</td>
                    <td className="num">{s.database_count}</td>
                    <td title={fmtDate(s.last_discovery_at, t("common.never"))}>{relTime(s.last_discovery_at, t, nowMs)}</td>
                    <td title={fmtDate(s.last_validation_at, t("common.never"))}>{relTime(s.last_validation_at, t, nowMs)}</td>
                    <td>{s.enabled ? <StatusBadge status={s.status} /> : <span className="muted">{t("sources.disabled")}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className={configs.length > 0 ? "cols" : ""}>
        <Panel title={t("sources.sweepTitle")} subtitle={t("sources.sweepSub")}>
          <div className="kv-rows">
            {summary && (
              <>
                <div className="kv-row">
                  <span className="k">{t("sources.discoverySweep")}</span>
                  <span className="v info">{t("sources.everyMinutes", { n: summary.discovery_interval_minutes })}</span>
                </div>
                <div className="kv-row">
                  <span className="k">{t("sources.validationPass")}</span>
                  <span className="v info">{t("sources.everyInterval", { v: fmtHours(summary.validation_interval_hours) })}</span>
                </div>
              </>
            )}
            {configs.length > 0 && (
              <div className="kv-row">
                <span className="k">{t("sources.databaseScope")}</span>
                <span className={`v ${autodiscover ? "good" : ""}`}>
                  {autodiscover
                    ? t("sources.autodiscover")
                    : configs.map((c) => (c.autodiscover_databases ? `${c.name}: ${t("sources.autodiscover")}` : `${c.name}: ${c.databases_to_catalog.join(", ")}`)).join(" · ")}
                </span>
              </div>
            )}
            <div className="kv-row">
              <span className="k">{t("sources.depthOfAnalysis")}</span>
              <span className="v info" title={t("sources.depthOfAnalysisHint")}>
                {coverage ? t("sources.depthValue", { pct: Math.round(coverage.target_pct) }) : t("common.dash")}
              </span>
            </div>
            {sources.map((s) => {
              const cyc = cycleByName.get(s.name);
              if (!cyc?.next_discovery_at) return null;
              const mins = Math.max(0, Math.round((new Date(cyc.next_discovery_at).getTime() - nowMs) / 60000));
              return (
                <div className="kv-row" key={`next-${s.name}`}>
                  <span className="k">{t("sources.nextSweep", { name: s.name })}</span>
                  <span className="v">{mins <= 0 ? t("health.dueNow") : t("health.inMinutes", { m: mins })}</span>
                </div>
              );
            })}
          </div>
          <div className="knob-note faint">{t("sources.depthOfAnalysisHint")}</div>
        </Panel>

        {configs.length > 0 && (
        <div className="stack">
          {configs.map((c) => (
            <SourceDetails key={c.name} c={c} />
          ))}
        </div>
        )}
      </div>

      {summary && summary.databases.length > 0 && (
        <DatabasesPanel databases={summary.databases} onGoToEntities={onGoToEntities} />
      )}
    </div>
  );
}
