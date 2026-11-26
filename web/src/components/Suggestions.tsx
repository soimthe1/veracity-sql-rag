import { useEffect, useState } from "react";
import { api, SuggestionOut } from "../api";
import { useI18n } from "../i18n";
import { fmtDate, relTime } from "../lib/format";
import StatusBadge from "./StatusBadge";
import { Kpi, Panel } from "./ui";

/**
 * The demand-driven ("self-correcting") coverage loop, made visible:
 * tables that unanswered questions relied on while not yet TRUSTED. See
 * engine/coverage.py and answer/demand.py — this tab doesn't DO anything
 * itself, it's a window into what the next coverage cycle will already do
 * automatically (no approve/reject step exists, by design).
 */
export default function Suggestions({ onOpenEntity }: { onOpenEntity?: (id: string) => void }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<SuggestionOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.suggestions(100).then(setRows).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="banner error">{error}</div>;
  if (!rows) return <div className="empty">{t("common.loading")}</div>;

  const forced = rows.filter((r) => r.will_force_deep_analysis).length;
  const hits = rows.reduce((n, r) => n + r.unanswered_hit_count, 0);
  const alreadyDeep = rows.filter((r) => r.deep_analysis).length;

  return (
    <div>
      <div className="kpis">
        <Kpi icon="bulb" tone="violet" value={rows.length} label={t("suggestions.kpiTables")} tag={t("suggestions.kpiTablesTag")} />
        <Kpi icon="alert" tone="amber" value={hits} label={t("suggestions.kpiHits")} tag={t("suggestions.kpiHitsTag")} />
        <Kpi icon="brain" tone="red" value={forced} label={t("suggestions.kpiForced")} tag={t("suggestions.kpiForcedTag")} />
        <Kpi icon="refresh" tone="cyan" value={rows.length - forced} label={t("suggestions.kpiBoost")} tag={t("suggestions.kpiBoostTag")} />
        <Kpi icon="check" tone="green" value={alreadyDeep} label={t("suggestions.kpiDeep")} tag={t("suggestions.kpiDeepTag")} />
      </div>

      <Panel headerOnly title={t("suggestions.bannerStrong")} subtitle={t("suggestions.bannerRest", { trusted: t("status.TRUSTED"), cfg: "catalog.coverage.demand_promote_threshold" })} />

      {rows.length === 0 ? (
        <div className="panel">
          <div className="empty">{t("suggestions.empty", { cfg: "catalog.answer.enabled" })}</div>
        </div>
      ) : (
        rows.map((s) => (
          <section
            key={s.id}
            className={`panel demand-card tone-${s.will_force_deep_analysis ? "amber" : "violet"}${onOpenEntity ? " clickable" : ""}`}
            onClick={onOpenEntity ? () => onOpenEntity(s.id) : undefined}
          >
            <div className="demand-card-body">
              <div className="demand-card-main">
                <div className="demand-card-title">
                  {s.object_name}
                  <span className="muted mono" style={{ fontWeight: 400, fontSize: 11.5, marginInlineStart: 8 }}>
                    {s.database_name}.{s.schema_name}
                  </span>
                </div>
                <div className="demand-card-desc" title={fmtDate(s.last_unanswered_at, t("common.dash"))}>
                  {s.classification} · {t("suggestions.lastAsked", { when: relTime(s.last_unanswered_at, t) })}
                  {s.deep_analysis ? ` · ${t("suggestions.alreadyDeep")}` : ""}
                </div>
                <div className="toolbar" style={{ marginTop: 12 }}>
                  <StatusBadge status={s.status} />
                  {s.will_force_deep_analysis ? (
                    <span className="pill tone-amber solid">{t("suggestions.willForce")}</span>
                  ) : (
                    <span className="pill tone-violet">{t("suggestions.rankingBoostOnly")}</span>
                  )}
                </div>
              </div>
              <div className="demand-card-count">
                <b>{s.unanswered_hit_count}</b>
                <span>{t("suggestions.colUnansweredHits")}</span>
              </div>
            </div>
          </section>
        ))
      )}
    </div>
  );
}
