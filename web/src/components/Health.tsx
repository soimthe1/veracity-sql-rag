import { answerApi, api, CatalogSummaryOut, SourceConfigOut, StatusOut, SystemHealthOut } from "../api";
import { usePolling } from "../hooks";
import { useI18n } from "../i18n";
import StatusBadge from "./StatusBadge";
import { Kpi, Panel } from "./ui";

const POLL_MS = 30_000;

/** Countdown text relative to `nowMs`, using the server's own clock
 * (summary.generated_at) rather than the browser's, so a skewed local
 * clock never shows a misleading "due now"/"overdue". */
function fmtCountdown(
  iso: string | null,
  nowMs: number,
  t: (path: string, vars?: Record<string, string | number>) => string,
): { text: string; state: "pending" | "due" | "scheduled" } {
  if (!iso) return { text: t("health.notRunYet"), state: "pending" };
  const diffMs = new Date(iso).getTime() - nowMs;
  if (diffMs <= 0) return { text: t("health.dueNow"), state: "due" };
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return { text: t("health.dueNow"), state: "due" };
  if (mins < 60) return { text: t("health.inMinutes", { m: mins }), state: "scheduled" };
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return { text: remMins ? t("health.inHoursMinutes", { h: hours, m: remMins }) : t("health.inHours", { h: hours }), state: "scheduled" };
}

function fmtHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(1)}h`;
}

/**
 * catalog_health.detail is a free-form JSON column (see
 * engine/health.py's set_component_health) — the engine has always sent
 * an object here (a per-database discovery breakdown, or {"error": "..."}
 * on failure, or {} for components with nothing to report), never a
 * plain string. Rendering it required knowing its shape; this renders
 * every shape we know about AND falls back to a raw (but safe) dump for
 * anything unrecognized, so a future detail shape can never blank the
 * whole app again the way an unguarded {c.detail} did.
 */
interface DiscoveryDatabaseDetail {
  database: string;
  objects_seen?: number;
  objects_changed?: number;
  error?: string | null;
}

function isDiscoveryDetail(d: unknown): d is { databases: DiscoveryDatabaseDetail[] } {
  return !!d && typeof d === "object" && Array.isArray((d as { databases?: unknown }).databases);
}

function isErrorDetail(d: unknown): d is { error: string } {
  return !!d && typeof d === "object" && typeof (d as { error?: unknown }).error === "string";
}

function DetailCell({ detail }: { detail: unknown }) {
  const { t } = useI18n();
  if (detail == null) return <span className="muted">{t("common.dash")}</span>;

  if (typeof detail === "string") return <span className="muted">{detail || t("common.dash")}</span>;

  if (typeof detail === "object") {
    if (isDiscoveryDetail(detail)) {
      const dbs = detail.databases;
      if (dbs.length === 0) return <span className="muted">{t("common.dash")}</span>;
      const seen = dbs.reduce((n, d) => n + (d.objects_seen ?? 0), 0);
      const changed = dbs.reduce((n, d) => n + (d.objects_changed ?? 0), 0);
      const failed = dbs.filter((d) => d.error);
      return (
        <div>
          <span className="muted">
            {t("health.databasesScanned", { n: dbs.length, plural: dbs.length === 1 ? "" : "s", seen })}
            {changed > 0 ? t("health.changedSuffix", { n: changed }) : ""}
            {failed.length > 0 && <span style={{ color: "var(--red)" }}>{t("health.failedSuffix", { n: failed.length })}</span>}
          </span>
          {failed.length > 0 && (
            <div style={{ marginTop: 4 }}>
              {failed.map((d) => (
                <div key={d.database} style={{ color: "var(--red)", fontSize: 11.5 }}>
                  {d.database}: {d.error}
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }

    if (isErrorDetail(detail)) {
      return <span style={{ color: "var(--red)" }}>{detail.error}</span>;
    }

    if (Object.keys(detail as object).length === 0) return <span className="muted">{t("common.dash")}</span>;

    // Unrecognized-but-safe fallback — still just data, never crashes.
    return <span className="muted mono">{JSON.stringify(detail)}</span>;
  }

  return <span className="muted">{String(detail)}</span>;
}

function fmtDate(iso: string | null, never: string) {
  if (!iso) return never;
  return new Date(iso).toLocaleString();
}

export default function Health({ status }: { status: StatusOut }) {
  const { t } = useI18n();
  const components = Object.entries(status.components);
  const { data: system } = usePolling<SystemHealthOut>(() => api.systemHealth(), POLL_MS);
  const { data: sources, error: sourcesError } = usePolling<SourceConfigOut[]>(() => api.sourceConfig(), POLL_MS);
  const { data: answerHealth, error: answerError } = usePolling(() => answerApi.health(), POLL_MS);
  const { data: summary } = usePolling<CatalogSummaryOut>(() => api.summary(), POLL_MS);

  // Next-due discovery/validation timing lives in the summary endpoint
  // (api.summary()), keyed by source name — folded into the Sources
  // table below rather than a second table, so this tab has one place
  // for "what's running", not two overlapping ones.
  const cycleByName = new Map((summary?.sources ?? []).map((s) => [s.name, s]));
  const nowMs = summary ? new Date(summary.generated_at).getTime() : Date.now();

  // The answer/ process is a SEPARATE process from this API, so "is it
  // even running" can only be known by pinging it directly (answerError
  // means the fetch itself failed — nothing is listening) — distinct
  // from catalog.answer.enabled, which only governs behavior once that
  // process IS up (see api/routers/status.py's status_system docstring).
  const answerRunning = !answerError && answerHealth != null;

  const failing = (sources ?? []).filter((s) => s.consecutive_failures > 0).length;

  return (
    <>
      <div className="kpis">
        <Kpi
          icon="database"
          tone={system ? (system.postgres.connected ? "green" : "red") : "gray"}
          tag={system ? `${system.postgres.host}:${system.postgres.port}` : ""}
          textValue
          value={system ? (system.postgres.connected ? t("health.connected") : t("health.unreachable")) : t("common.loading")}
          label={t("health.catalogDatabase")}
          hint={system ? `${system.postgres.database} · sslmode=${system.postgres.sslmode}` : undefined}
        />
        <Kpi
          icon="brain"
          tone={system ? (system.llm.effectively_enabled ? "green" : system.llm.enabled ? "amber" : "gray") : "gray"}
          tag={system?.llm.model ?? system?.llm.provider ?? ""}
          textValue
          value={system ? (system.llm.effectively_enabled ? t("health.enabled") : system.llm.enabled ? t("health.misconfigured") : t("health.disabled")) : t("common.loading")}
          label={t("health.llmInference")}
          hint={system ? `${t("health.provider")} ${system.llm.provider}${system.llm.endpoint ? ` · ${system.llm.endpoint}` : ""}` : undefined}
        />
        <Kpi
          icon="sparkle"
          tone={answerRunning ? (answerHealth && !answerHealth.require_grants ? "amber" : "green") : "red"}
          tag={t("health.port", { n: system?.answer.port ?? t("common.dash") })}
          textValue
          value={answerRunning ? t("health.processRunning") : t("health.notRunning")}
          label={t("health.answerService")}
          hintWarn={answerRunning && !!answerHealth && !answerHealth.require_grants}
          hint={
            !answerRunning
              ? t("health.answerStartHint", { cmd: "dev.ps1", flag: "-SkipAnswer" })
              : answerHealth && !answerHealth.require_grants
                ? t("health.requireGrantsFalse")
                : system
                  ? system.answer.enabled_in_config
                    ? t("health.answerEnabledTrue")
                    : t("health.answerEnabledFalse")
                  : undefined
          }
        />
        <Kpi
          icon="activity"
          tone={failing ? "red" : "blue"}
          tag={failing ? t("health.failingTag", { n: failing }) : summary ? t("health.everyTag", { n: summary.discovery_interval_minutes }) : ""}
          value={sources?.length ?? t("common.dash")}
          label={t("health.sourcesLabel")}
        />
        <Kpi
          icon="key"
          tone={system?.auth_enabled ? "green" : "amber"}
          tag={system?.search_engine ?? status.search_engine}
          textValue
          value={system?.auth_enabled ? t("health.enabled") : t("health.disabled")}
          label={t("health.appLoginLabel")}
          hint={`${t("health.searchEngine")} ${system?.search_engine ?? status.search_engine}`}
        />
      </div>

      <Panel
        title={t("health.sourcesHeading", { n: sources?.length ?? 0 })}
        subtitle={
          summary
            ? t("health.cycleTiming", { disc: summary.discovery_interval_minutes, valid: fmtHours(summary.validation_interval_hours) })
            : t("health.loadingCycleTiming")
        }
        bodyClass="panel-body flush"
      >
        {sourcesError ? (
          <div className="empty">{t("health.sourceDetailsUnavailable", { err: sourcesError })}</div>
        ) : !sources ? (
          <div className="empty">{t("common.loading")}</div>
        ) : sources.length === 0 ? (
          <div className="empty">{t("health.noSourcesConfigured")}</div>
        ) : (
          <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("health.colSource")}</th>
                <th>{t("health.colHost")}</th>
                <th>{t("health.colStatus")}</th>
                <th>{t("health.colDatabases")}</th>
                <th>{t("health.colFailures")}</th>
                <th>{t("health.colLastDiscovery")}</th>
                <th>{t("health.colNextDiscovery")}</th>
                <th>{t("health.colLastValidation")}</th>
                <th>{t("health.colNextValidation")}</th>
                <th>{t("health.colLastError")}</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => {
                const cycle = cycleByName.get(s.name);
                const nextDisc = fmtCountdown(cycle?.next_discovery_at ?? null, nowMs, t);
                const nextValid = fmtCountdown(cycle?.next_validation_at ?? null, nowMs, t);
                return (
                  <tr key={s.name}>
                    <td className="strong">{s.name}</td>
                    <td className="mono muted">
                      {s.is_configured ? `${s.host}:${s.port}` : <span className="muted">{t("common.notConfigured")}</span>}
                    </td>
                    <td>
                      {s.enabled ? (
                        s.live_status ? (
                          <StatusBadge status={s.live_status} />
                        ) : (
                          <span className="muted">{t("health.notYetRun")}</span>
                        )
                      ) : (
                        <span className="muted">{t("health.disabledState")}</span>
                      )}
                    </td>
                    <td>{s.live_database_count}</td>
                    <td>
                      {s.consecutive_failures > 0 ? <span style={{ color: "var(--red)" }}>{s.consecutive_failures}</span> : "0"}
                    </td>
                    <td className="muted">{fmtDate(s.last_discovery_at, t("common.never"))}</td>
                    <td className={`cycle-next ${nextDisc.state}`}>{s.enabled ? nextDisc.text : t("common.dash")}</td>
                    <td className="muted">{fmtDate(s.last_validation_at, t("common.never"))}</td>
                    <td className={`cycle-next ${nextValid.state}`}>{s.enabled ? nextValid.text : t("common.dash")}</td>
                    <td style={{ maxWidth: 260 }}>
                      {s.last_error ? <span style={{ color: "var(--red)" }}>{s.last_error}</span> : <span className="muted">{t("common.dash")}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </Panel>

      <Panel title={t("health.componentHealth", { n: components.length })} subtitle={t("health.componentSub")} bodyClass="panel-body flush">
        {components.length === 0 ? (
          <div className="empty">{t("health.noComponentsYet")}</div>
        ) : (
          <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("health.colComponent")}</th>
                <th>{t("health.colStatus")}</th>
                <th>{t("health.colDetail")}</th>
                <th>{t("health.colUpdated")}</th>
              </tr>
            </thead>
            <tbody>
              {components.map(([name, c]) => (
                <tr key={name}>
                  <td className="strong mono">{name}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>
                    <DetailCell detail={c.detail} />
                  </td>
                  <td className="muted">{new Date(c.updated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Panel>
    </>
  );
}
