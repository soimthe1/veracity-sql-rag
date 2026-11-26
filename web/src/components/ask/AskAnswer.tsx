/**
 * An answer: classification, the narrated answer, trust, the data as
 * tables, the tables it relied on — and Create (email / Word / PowerPoint /
 * PDF). "Queries run" (the exact SQL) appears only when the server sent it:
 * administrators and roles with "Can see generated SQL".
 */
import { ReactNode, useState } from "react";
import type { AskQuery, AskResult, AskResultSet, RecentQuestion, SuggestedQuestion } from "../../api";
import { useI18n } from "../../i18n";
import { CellChip, ClassificationBanner } from "../../lib/lattice";
import AskGraph from "../AskGraph";
import Icon from "../Icon";
import StatusBadge from "../StatusBadge";
import { Panel, Pill, statusTone } from "../ui";
import CreateMenu from "./CreateMenu";
import { AskInput, kindMeta, TrustScore } from "./shared";

export type DetailTab = "data" | "tables" | "how" | "sql";

export interface AskAnswerProps {
  asked: string;
  result: AskResult | null;
  loading: boolean;
  error: string | null;
  followUp: string;
  setFollowUp: (v: string) => void;
  database: string;
  setDatabase: (v: string) => void;
  databases: string[];
  onAsk: (q: string) => void;
  onNew: () => void;
  suggested: SuggestedQuestion[];
  recent: RecentQuestion[];
  askedBefore: Set<string>;
  onOpenEntity?: (id: string) => void;
  /** which detail tab is open (null = all collapsed) — kept across answers */
  detailTab: DetailTab | null;
  setDetailTab: (t: DetailTab | null) => void;
  corner?: ReactNode;
  info?: ReactNode;
  extra?: ReactNode;
}

/**
 * The answer leads: classification, then the answer itself in large type,
 * then one line of what it rests on. Everything else — the rows, the
 * tables used, how it was answered, the SQL — sits in collapsed tabs that
 * show their counts, so it's discoverable without being in the way. The
 * side column carries the facts at a glance, Create and what to ask next.
 */
export default function AskAnswer(p: AskAnswerProps) {
  const { t } = useI18n();
  const r = p.result;
  const results = (r?.results ?? []).filter((x) => x.rows.length > 0);
  const totalRows = results.reduce((n, x) => n + x.rows.length, 0);

  return (
    <div className="askx-page askx-answer">
      <div className="askx-main">
        <section className="panel askx-qbar">
          <Icon name="sparkle" size={18} style={{ color: "var(--violet)", flexShrink: 0 }} />
          <span className="askx-qbar-text" dir="auto">{p.asked}</span>
          {p.corner}
          {r && <CreateMenu question={p.asked} result={r} />}
          <button className="btn-ghost btn-sm" onClick={p.onNew}>{t("ask.newQuestion")}</button>
        </section>
        {p.info}

        {p.loading && (
          <section className="panel">
            <div className="ask-thinking"><span className="spinner" />{t("ask.thinking")}</div>
          </section>
        )}
        {p.error && <div className="banner error">{p.error}</div>}

        {r && (
          <section className="panel askx-result">
            {r.access?.label_level != null && r.access.label_category && (
              <ClassificationBanner level={r.access.label_level} category={r.access.label_category} />
            )}
            {r.answer ? (
              <div className="askx-answer-text" dir="auto">{r.answer}</div>
            ) : r.chunks.length > 0 ? (
              <div className="banner warn" style={{ marginBottom: 0 }}>{t("ask.noWrittenAnswer")}</div>
            ) : (
              <div className="askx-answer-text declined" dir="auto">{r.notes}</div>
            )}
            <div className="askx-answer-meta">
              {r.trust_label && (
                <Pill tone={statusTone(r.trust_label)} solid title={t("ask.trustHint")}>
                  {t("ask.trustLabel", {
                    label: t(`status.${r.trust_label}`) || r.trust_label,
                    score: r.trust_score != null ? `${Math.round(r.trust_score * 100)}%` : "?",
                  })}
                </Pill>
              )}
              {r.database_name && <span className="askx-meta-item"><Icon name="database" size={13} /> {r.database_name}</span>}
              {r.access && r.access.columns_withheld > 0 && (
                <span className="chip warn-chip">{t("ask.columnsWithheld", { n: r.access.columns_withheld })}</span>
              )}
            </div>
            <Details r={r} results={results} totalRows={totalRows} tab={p.detailTab} setTab={p.setDetailTab} onOpenEntity={p.onOpenEntity} />
          </section>
        )}

        <Panel title={t("ask.askAnother")} bodyClass="panel-body">
          <AskInput compact value={p.followUp} onChange={p.setFollowUp} onSubmit={() => p.onAsk(p.followUp)}
            database={p.database} onDatabase={p.setDatabase} databases={p.databases} disabled={p.loading} />
          {p.extra}
        </Panel>
      </div>

      <aside className="askx-rail">
        {r && <GlanceCard r={r} totalRows={totalRows} />}
        {r && (
          <section className="askx-card">
            <div className="askx-card-head"><h4><Icon name="wand" size={17} /> {t("ask.create.title")}</h4></div>
            <CreateMenu question={p.asked} result={r} variant="card" />
          </section>
        )}
        <NextCard suggested={p.suggested} recent={p.recent} current={p.asked} askedBefore={p.askedBefore}
          disabled={p.loading} onAsk={p.onAsk} />
      </aside>
    </div>
  );
}

function Details({ r, results, totalRows, tab, setTab, onOpenEntity }: {
  r: AskResult;
  results: AskResultSet[];
  totalRows: number;
  tab: DetailTab | null;
  setTab: (t: DetailTab | null) => void;
  onOpenEntity?: (id: string) => void;
}) {
  const { t } = useI18n();
  const nodes = r.graph_nodes;
  const tabs: { key: DetailTab; icon: string; label: string; count?: string }[] = [];
  if (results.length) tabs.push({ key: "data", icon: "table", label: t("ask.detail.data"), count: t("ask.nRows", { n: totalRows }) });
  if (nodes.length) tabs.push({ key: "tables", icon: "network", label: t("ask.detail.tables"), count: String(nodes.length) });
  if (r.notes) tabs.push({ key: "how", icon: "info", label: t("ask.detail.how") });
  if (r.queries?.length) tabs.push({ key: "sql", icon: "code", label: t("ask.detail.sql"), count: String(r.queries.length) });
  if (!tabs.length) return null;
  const open = tabs.some((x) => x.key === tab) ? tab : null;

  return (
    <div className="askx-details">
      <div className="askx-tabs" role="tablist">
        {tabs.map((x) => (
          <button key={x.key} type="button" role="tab" aria-selected={open === x.key}
            className={`askx-tab${open === x.key ? " active" : ""}`} onClick={() => setTab(open === x.key ? null : x.key)}>
            <Icon name={x.icon} size={14} />
            {x.label}
            {x.count && <span className="askx-tab-count">{x.count}</span>}
          </button>
        ))}
        <span className="askx-tabs-hint">{open ? "" : t("ask.detail.hint")}</span>
        {open && (
          <button type="button" className="icon-btn sm askx-tabs-close" onClick={() => setTab(null)} title={t("ask.detail.collapse")}
            aria-label={t("ask.detail.collapse")}>
            <Icon name="x" size={13} />
          </button>
        )}
      </div>

      {open === "data" && <ResultTables results={results} />}
      {open === "tables" && (
        <div className="askx-tables-tab">
          <div className="graph-canvas" style={{ background: "var(--surface-2)" }}>
            <AskGraph nodes={nodes} edges={r.graph_edges} onSelect={onOpenEntity} />
          </div>
          <div className="asset-list">
            {nodes.map((n) => onOpenEntity ? (
              <button key={n.id} className="asset-row" onClick={() => onOpenEntity(n.id)}>
                <Icon name="table" size={16} />
                <span className="name mono">{n.label}</span>
                <StatusBadge status={n.status} />
                <Icon name="chevron" size={14} className="chev flip-rtl" />
              </button>
            ) : (
              <div key={n.id} className="asset-row static">
                <Icon name="table" size={16} />
                <span className="name mono">{n.label}</span>
                <StatusBadge status={n.status} />
              </div>
            ))}
          </div>
        </div>
      )}
      {open === "how" && (
        <div className="askx-how">
          <p dir="auto">{r.notes}</p>
          <p className="muted small">{t("ask.graphCaption")}</p>
        </div>
      )}
      {open === "sql" && r.queries && <QueriesRun queries={r.queries} />}
    </div>
  );
}

function GlanceCard({ r, totalRows }: { r: AskResult; totalRows: number }) {
  const { t } = useI18n();
  const a = r.access;
  return (
    <section className="askx-card">
      <div className="askx-card-head"><h4><Icon name="activity" size={17} /> {t("ask.glance.title")}</h4></div>
      <div className="kv-rows askx-glance">
        <div className="kv-row">
          <span className="k">{t("ask.glance.trust")}</span>
          <span className="v"><TrustScore score={r.trust_score} /> <span className="muted small">{r.trust_label ? t(`status.${r.trust_label}`) || r.trust_label : ""}</span></span>
        </div>
        <div className="kv-row">
          <span className="k">{t("ask.glance.classification")}</span>
          <span className="v">{a?.label_level != null && a.label_category ? <CellChip level={a.label_level} category={a.label_category} showLabel /> : "—"}</span>
        </div>
        {a && a.cells_used.length > 0 && (
          <div className="kv-row">
            <span className="k">{t("ask.dataFrom")}</span>
            <span className="v cells" style={{ justifyContent: "flex-end" }}>{a.cells_used.map((c) => <CellChip key={c} token={c} size="sm" />)}</span>
          </div>
        )}
        {r.database_name && <div className="kv-row"><span className="k">{t("ask.glance.database")}</span><span className="v">{r.database_name}</span></div>}
        <div className="kv-row"><span className="k">{t("ask.glance.tables")}</span><span className="v">{r.graph_nodes.length}</span></div>
        <div className="kv-row"><span className="k">{t("ask.glance.rows")}</span><span className="v">{totalRows}</span></div>
        {a && a.columns_withheld > 0 && (
          <div className="kv-row"><span className="k">{t("ask.glance.withheld")}</span><span className="v warn">{a.columns_withheld}</span></div>
        )}
        <div className="kv-row"><span className="k">{t("ask.glance.took")}</span><span className="v">{(r.took_ms / 1000).toFixed(1)} s</span></div>
      </div>
    </section>
  );
}

/** "Try next" (side column): suggested questions not asked yet, then the
 * person's recent ones — one click asks it. */
function NextCard({ suggested, recent, current, askedBefore, disabled, onAsk }: {
  suggested: SuggestedQuestion[];
  recent: RecentQuestion[];
  current: string | null;
  askedBefore: Set<string>;
  disabled: boolean;
  onAsk: (q: string) => void;
}) {
  const { t } = useI18n();
  const [all, setAll] = useState(false);
  const norm = (q: string) => q.trim().toLowerCase();
  const seen = new Set<string>(current ? [norm(current)] : []);
  const pick = <T extends { question: string }>(items: T[]) => items.filter((i) => {
    const k = norm(i.question);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const sug = pick(suggested.filter((q) => !askedBefore.has(norm(q.question))));
  const rec = pick(recent);
  if (!sug.length && !rec.length) return null;
  const shownSug = all ? sug : sug.slice(0, 5);
  const shownRec = all ? rec : rec.slice(0, 3);
  const hidden = sug.length - shownSug.length + (rec.length - shownRec.length);
  return (
    <section className="askx-card">
      <div className="askx-card-head"><h4><Icon name="bulb" size={17} /> {t("ask.tryNext")}</h4></div>
      <div className="askx-next">
        {shownSug.map((s) => {
          const k = kindMeta(s.kind);
          return (
            <button key={s.question} type="button" className="askx-next-row" disabled={disabled} onClick={() => onAsk(s.question)}>
              <span className={`askx-kind-icon sm tone-${k.tone}`}><Icon name={k.icon} size={13} /></span>
              <span dir="auto">{s.question}</span>
            </button>
          );
        })}
        {shownRec.length > 0 && <div className="suggest-label" style={{ margin: "10px 0 4px" }}>{t("ask.recent")}</div>}
        {shownRec.map((r) => (
          <button key={r.question} type="button" className="askx-next-row" disabled={disabled} onClick={() => onAsk(r.question)}>
            <span className="askx-kind-icon sm tone-gray"><Icon name="refresh" size={13} /></span>
            <span dir="auto">{r.question}</span>
          </button>
        ))}
      </div>
      {(hidden > 0 || all) && (
        <button type="button" className="askx-link" style={{ marginTop: 10 }} onClick={() => setAll((v) => !v)}>
          {all ? t("ask.showFewer") : t("ask.showMore", { n: hidden })}
        </button>
      )}
    </section>
  );
}

const PAGE_ROWS = 25;

function ResultTables({ results }: { results: AskResultSet[] }) {
  const { t } = useI18n();
  return (
    <div className="askx-results">
      {results.map((rs, i) => <ResultTable key={i} rs={rs} title={rs.purpose || t("ask.create.emailData")} />)}
    </div>
  );
}

function ResultTable({ rs, title }: { rs: AskResultSet; title: string }) {
  const { t } = useI18n();
  const [all, setAll] = useState(false);
  const rows = all ? rs.rows : rs.rows.slice(0, PAGE_ROWS);
  return (
    <div className="askx-result">
      <div className="askx-result-head">
        <Icon name="table" size={14} />
        <b dir="auto">{title}</b>
        <span className="muted small">{t("ask.nRows", { n: rs.rows.length })}</span>
      </div>
      <div className="table-wrap askx-result-scroll">
        <table>
          <thead><tr>{rs.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j} dir="auto" className={v === null ? "muted" : ""}>{v ?? "—"}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {rs.rows.length > PAGE_ROWS && (
        <button type="button" className="btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={() => setAll((v) => !v)}>
          {all ? t("ask.showFewer") : t("ask.showAllRows", { n: rs.rows.length })}
        </button>
      )}
    </div>
  );
}

function QueriesRun({ queries }: { queries: AskQuery[] }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState<number | null>(null);
  return (
    <div className="askx-sql-list">
      <div className="muted small" style={{ margin: "0 0 10px" }}>{t("ask.queriesRunHint")}</div>
      {queries.map((q, i) => (
        <div key={i} className="askx-sql">
          <div className="askx-sql-head">
            <span className={`outcome-tag ${q.verdict === "ALLOWED" ? "good" : "bad"}`}>{t(`ask.verdict.${q.verdict}`)}</span>
            <b dir="auto">{q.purpose ?? ""}</b>
            {q.rows != null && <span className="muted small">{t("ask.nRows", { n: q.rows })}</span>}
            {q.sql && (
              <button className="icon-btn sm" style={{ marginInlineStart: "auto" }} title={t("ask.copySql")} aria-label={t("ask.copySql")}
                onClick={() => navigator.clipboard?.writeText(q.sql ?? "").then(() => { setCopied(i); setTimeout(() => setCopied(null), 1400); })}>
                <Icon name={copied === i ? "check" : "copy"} size={13} />
              </button>
            )}
          </div>
          {q.sql && <pre className="askx-sql-code" dir="ltr">{q.sql}</pre>}
          {q.reason && q.verdict !== "ALLOWED" && <div className="muted small" dir="auto">{q.reason}</div>}
        </div>
      ))}
    </div>
  );
}
