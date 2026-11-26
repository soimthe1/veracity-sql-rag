/**
 * The Ask landing page — the app's home. Everything on it is real:
 * suggestions come from the catalog (answer/suggestions.py), recent
 * questions from the question log, access / catalog health from the API.
 */
import { ReactNode, useMemo, useState } from "react";
import type { QuestionKind, RecentQuestion, ScopeOut, StatusOut, SuggestedQuestion } from "../../api";
import { useI18n } from "../../i18n";
import { relTime } from "../../lib/format";
import { CellChip, parseCell, useLattice } from "../../lib/lattice";
import Icon from "../Icon";
import { AskInput, KINDS, kindMeta, OutcomeTag, TrustScore } from "./shared";

const TRY_COUNT = 4;

export interface AskHomeProps {
  question: string;
  setQuestion: (v: string) => void;
  database: string;
  setDatabase: (v: string) => void;
  databases: string[];
  onAsk: (q: string) => void;
  suggested: SuggestedQuestion[];
  recent: RecentQuestion[];
  scope: ScopeOut | null;
  status: StatusOut | null;
  onOpenHistory: () => void;
  onOpenHealth?: () => void;
  extra?: ReactNode;      // grant picker (sign-in off) / info
  corner?: ReactNode;
}

export default function AskHome(p: AskHomeProps) {
  const { t } = useI18n();
  const [offset, setOffset] = useState(0);
  const [openKind, setOpenKind] = useState<QuestionKind | "all" | null>(null);

  const tryList = useMemo(() => {
    const s = p.suggested;
    if (s.length <= TRY_COUNT) return s;
    return Array.from({ length: TRY_COUNT }, (_, i) => s[(offset + i) % s.length]);
  }, [p.suggested, offset]);

  const byKind = useMemo(() => {
    const m = new Map<string, SuggestedQuestion[]>();
    p.suggested.forEach((s) => m.set(s.kind ?? "count", [...(m.get(s.kind ?? "count") ?? []), s]));
    return m;
  }, [p.suggested]);
  const kinds = KINDS.filter((k) => (byKind.get(k.kind)?.length ?? 0) > 0);
  const openList = openKind === "all" ? p.suggested : openKind ? byKind.get(openKind) ?? [] : [];

  return (
    <div className="askx-page">
      <div className="askx-main">
        <section className="askx-hero">
          {p.corner && <div className="askx-hero-corner">{p.corner}</div>}
          <span className="askx-powered"><Icon name="sparkle" size={13} /> {t("ask.home.powered")}</span>
          <h2>
            {t("ask.home.title1")} <span className="askx-grad">{t("ask.home.title2")}</span>
          </h2>
          <p className="askx-sub">{t("ask.home.sub")}</p>
          <div className="askx-props">
            <span><Icon name="shield" size={16} /> {t("ask.home.prop1")}</span>
            <Icon name="chevron" size={13} className="askx-props-sep flip-rtl" />
            <span><Icon name="table" size={16} /> {t("ask.home.prop2")}</span>
            <Icon name="chevron" size={13} className="askx-props-sep flip-rtl" />
            <span><Icon name="chart" size={16} /> {t("ask.home.prop3")}</span>
          </div>

          <AskInput value={p.question} onChange={p.setQuestion} onSubmit={() => p.onAsk(p.question)}
            database={p.database} onDatabase={p.setDatabase} databases={p.databases} autoFocus />
          {p.extra}

          {tryList.length > 0 && (
            <div className="askx-try">
              <div className="askx-row-head">
                <span className="askx-label">{t("ask.tryAsking")}</span>
                {p.suggested.length > TRY_COUNT && (
                  <button type="button" className="askx-link" onClick={() => setOffset((o) => o + TRY_COUNT)}>
                    <Icon name="shuffle" size={14} /> {t("ask.home.shuffle")}
                  </button>
                )}
              </div>
              <div className="askx-try-grid">
                {tryList.map((s) => {
                  const k = kindMeta(s.kind);
                  return (
                    <button key={s.question} type="button" className="askx-try-card" onClick={() => p.onAsk(s.question)}
                      title={t("ask.suggestionHint", { table: `${s.database_name}.${s.table}`, status: t(`status.${s.trust_label}`) || s.trust_label })}>
                      <span className={`askx-kind-icon tone-${k.tone}`}><Icon name={k.icon} size={17} /></span>
                      <span className="askx-clamp" dir="auto">{s.question}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </section>

        {kinds.length > 0 && (
          <section className="askx-section">
            <div className="askx-section-head">
              <h3><Icon name="bulb" size={18} /> {t("ask.home.suggested")}</h3>
              <button type="button" className="askx-link" onClick={() => setOpenKind(openKind === "all" ? null : "all")}>
                {openKind === "all" ? t("ask.showFewer") : t("ask.home.viewAll")}
              </button>
            </div>
            <div className="askx-kind-grid">
              {kinds.map((k) => {
                const list = byKind.get(k.kind) ?? [];
                const active = openKind === k.kind;
                return (
                  <button key={k.kind} type="button" className={`askx-kind-card${active ? " active" : ""}`}
                    onClick={() => setOpenKind(active ? null : k.kind)} aria-expanded={active}>
                    <span className={`askx-kind-icon lg tone-${k.tone}`}><Icon name={k.icon} size={20} /></span>
                    <span className="askx-kind-text">
                      <b>{t(`ask.kind.${k.kind}.title`)}</b>
                      <span>{t(`ask.kind.${k.kind}.desc`)}</span>
                    </span>
                    <span className="askx-kind-count">{list.length}</span>
                    <Icon name="chevron" size={15} className={`askx-kind-chev flip-rtl${active ? " open" : ""}`} />
                  </button>
                );
              })}
            </div>
            {openList.length > 0 && (
              <div className="askx-kind-list">
                {openList.map((s) => {
                  const k = kindMeta(s.kind);
                  return (
                    <button key={s.question} type="button" className="askx-q-row" onClick={() => p.onAsk(s.question)}>
                      <span className={`askx-kind-icon sm tone-${k.tone}`}><Icon name={k.icon} size={14} /></span>
                      <span className="askx-q-text" dir="auto">{s.question}</span>
                      <span className="askx-q-meta">{s.domain ?? s.database_name}</span>
                      <Icon name="chevron" size={14} className="flip-rtl muted" />
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        )}

        <section className="askx-section">
          <div className="askx-section-head">
            <h3><Icon name="clock" size={18} /> {t("ask.recent")}</h3>
            {p.recent.length > 0 && (
              <button type="button" className="askx-link" onClick={p.onOpenHistory}>{t("ask.home.viewAll")}</button>
            )}
          </div>
          {p.recent.length === 0 ? (
            <div className="askx-empty">{t("ask.home.noRecent")}</div>
          ) : (
            <RecentTable items={p.recent} onAsk={p.onAsk} />
          )}
        </section>
      </div>

      <aside className="askx-rail">
        {p.status ? <CatalogHealthCard status={p.status} onOpen={p.onOpenHealth} /> : <YourAccessCard scope={p.scope} />}
        <HowCard />
        <TipsCard onPick={(example) => p.setQuestion(example)} />
      </aside>
    </div>
  );
}

export function RecentTable({ items, onAsk }: { items: RecentQuestion[]; onAsk: (q: string) => void }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState<string | null>(null);
  function copy(q: string) {
    navigator.clipboard?.writeText(q).then(() => {
      setCopied(q);
      setTimeout(() => setCopied(null), 1400);
    }).catch(() => void 0);
  }
  return (
    <div className="table-wrap askx-recent">
      <table>
        <thead>
          <tr>
            <th>{t("ask.home.colQuestion")}</th>
            <th>{t("ask.home.colStatus")}</th>
            <th>{t("ask.home.colTrust")}</th>
            <th>{t("ask.home.colSources")}</th>
            <th>{t("ask.home.colTime")}</th>
            <th aria-label={t("common.actions")} />
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.ask_id ?? r.question} className="clickable" onClick={() => onAsk(r.question)} title={t("ask.home.askAgain")}>
              <td>
                <div className="askx-recent-q">
                  <Icon name="doc" size={15} className="muted" />
                  <span dir="auto">{r.question}</span>
                </div>
              </td>
              <td><OutcomeTag outcome={r.outcome} answered={r.answered} /></td>
              <td><TrustScore score={r.trust_score} /></td>
              <td className="muted nowrap">
                {r.tables ? <><Icon name="table" size={13} /> {t("ask.home.nTables", { n: r.tables })}</> : "—"}
              </td>
              <td className="muted nowrap">{relTime(r.asked_at, t)}</td>
              <td className="nowrap" onClick={(e) => e.stopPropagation()}>
                <button className="icon-btn sm" title={t("ask.home.askAgain")} aria-label={t("ask.home.askAgain")} onClick={() => onAsk(r.question)}>
                  <Icon name="refresh" size={14} />
                </button>{" "}
                <button className="icon-btn sm" title={copied === r.question ? t("ask.home.copied") : t("ask.home.copyQuestion")}
                  aria-label={t("ask.home.copyQuestion")} onClick={() => copy(r.question)}>
                  <Icon name={copied === r.question ? "check" : "copy"} size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RailCard({ icon, title, action, children }: { icon: string; title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="askx-card">
      <div className="askx-card-head">
        <h4><Icon name={icon} size={17} /> {title}</h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function Ring({ pct, tone }: { pct: number; tone: string }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <svg width="88" height="88" viewBox="0 0 88 88" className={`askx-ring tone-${tone}`} aria-hidden="true">
      <circle cx="44" cy="44" r={r} className="track" />
      <circle cx="44" cy="44" r={r} className="bar" strokeDasharray={`${(c * Math.min(100, pct)) / 100} ${c}`}
        transform="rotate(-90 44 44)" />
      <text x="44" y="49" textAnchor="middle">{pct.toFixed(1)}%</text>
    </svg>
  );
}

function CatalogHealthCard({ status, onOpen }: { status: StatusOut; onOpen?: () => void }) {
  const { t } = useI18n();
  const all = status.entities_total;
  const deep = all ? (status.deep_analysis_entities / all) * 100 : 0;
  const good = status.enabled && status.catalog_db_connected && deep >= status.coverage_deep_analysis_target_pct;
  return (
    <RailCard icon="activity" title={t("ask.home.healthTitle")}
      action={onOpen && <button type="button" className="askx-link" onClick={onOpen}>{t("ask.home.viewDetails")} <Icon name="chevron" size={13} className="flip-rtl" /></button>}>
      <div className="askx-health">
        <Ring pct={deep} tone={good ? "green" : "amber"} />
        <div>
          <div className="askx-health-state"><span className={`dot ${good ? "on" : "warn"}`} /> {good ? t("ask.home.healthGood") : t("ask.home.healthAttention")}</div>
          <div className="muted small">
            {status.catalog_db_connected ? t("ask.home.healthConnected", { time: relTime(status.last_discovery_at, t) }) : t("rail.catalogUnreachable")}
          </div>
          <div className="muted small">{t("ask.home.healthRing")}</div>
        </div>
      </div>
      <div className="askx-stats">
        <div><Icon name="table" size={15} /><b>{status.tables.toLocaleString()}</b><span>{t("ask.home.statTables")}</span></div>
        <div><Icon name="database" size={15} /><b>{status.databases.toLocaleString()}</b><span>{t("ask.home.statDatabases")}</span></div>
        <div><Icon name="book" size={15} /><b>{status.entities_understood.toLocaleString()}</b><span>{t("ask.home.statUnderstood")}</span></div>
        <div><Icon name="shield" size={15} /><b>{status.trusted.toLocaleString()}</b><span>{t("ask.home.statTrusted")}</span></div>
      </div>
    </RailCard>
  );
}

function YourAccessCard({ scope }: { scope: ScopeOut | null }) {
  const { t } = useI18n();
  const lat = useLattice();
  if (!scope) return null;
  return (
    <RailCard icon="key" title={t("ask.home.accessTitle")}>
      {scope.full_access ? (
        <div className="muted small">{scope.auth_enabled ? t("ask.home.accessAll") : t("ask.home.accessOpen")}</div>
      ) : (
        <>
          <div className="askx-access-big">
            <b>{scope.tables_visible}</b> {t("ask.home.accessTables")}
            <span className="muted"> · {t("ask.home.accessDbs", { n: scope.databases_visible })}</span>
          </div>
          {scope.cells.length > 0 ? (
            <div className="askx-cells">
              {scope.cells.map((c) => {
                const pc = parseCell(c);
                const lv = pc?.level ?? null;
                const cat = pc?.category ?? null;
                const label = pc ? lat.cellLabel(lv, cat) : null;
                return (
                  <div key={c} className="askx-cell-row">
                    <CellChip token={c} size="sm" />
                    <span>{pc ? label ?? `${lat.levelName(lv)} · ${lat.categoryName(cat)}` : t("cell.invalid")}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="banner warn" style={{ margin: "10px 0 0" }}>{t("ask.accessNone")}</div>
          )}
          {scope.columns_hidden > 0 && <div className="muted small" style={{ marginTop: 10 }}>{t("ask.home.accessHidden", { n: scope.columns_hidden })}</div>}
          {scope.databases.length > 0 && (
            <div className="askx-dbs">
              {scope.databases.slice(0, 6).map((d) => (
                <span key={d.database} className="chip neutral"><Icon name="database" size={12} /> {d.database} · {d.visible}</span>
              ))}
            </div>
          )}
        </>
      )}
    </RailCard>
  );
}

function HowCard() {
  const { t } = useI18n();
  const steps = [
    { icon: "sparkle", tone: "violet", k: "1" },
    { icon: "search", tone: "blue", k: "2" },
    { icon: "database", tone: "cyan", k: "3" },
    { icon: "doc", tone: "green", k: "4" },
  ];
  return (
    <RailCard icon="book" title={t("ask.home.howTitle")}>
      <ol className="askx-steps">
        {steps.map((s) => (
          <li key={s.k}>
            <span className={`askx-kind-icon tone-${s.tone}`}><Icon name={s.icon} size={16} /></span>
            <span>
              <b>{t(`ask.home.how${s.k}Title`)}</b>
              <span className="muted small">{t(`ask.home.how${s.k}Desc`)}</span>
            </span>
          </li>
        ))}
      </ol>
    </RailCard>
  );
}

function TipsCard({ onPick }: { onPick: (example: string) => void }) {
  const { t } = useI18n();
  const tips = ["1", "2", "3", "4", "5"];
  return (
    <RailCard icon="bulb" title={t("ask.home.tipsTitle")}>
      <div className="askx-tips">
        {tips.map((k) => (
          <button key={k} type="button" className="askx-tip" onClick={() => onPick(t(`ask.home.tip${k}Example`))}
            title={t("ask.home.tipUse")}>
            <span>
              <b>{t(`ask.home.tip${k}`)}</b>
              <span className="muted small" dir="auto">{t(`ask.home.tip${k}Example`)}</span>
            </span>
            <Icon name="chevron" size={14} className="flip-rtl muted" />
          </button>
        ))}
      </div>
    </RailCard>
  );
}
