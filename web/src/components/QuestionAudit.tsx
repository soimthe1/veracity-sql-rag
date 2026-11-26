/**
 * Security > Question audit (administrators): who asked what, what it
 * touched, and what to look at — blocked queries, withheld columns,
 * highly classified answers and documents created. Click a row for the
 * exact SQL that ran.
 */
import { useEffect, useState } from "react";
import { api, AuditPage, AuditQuestion, AuditQuestionDetail } from "../api";
import { useI18n } from "../i18n";
import { relTime } from "../lib/format";
import { CellChip, useLattice } from "../lib/lattice";
import Icon from "./Icon";
import { OutcomeTag, TrustScore } from "./ask/shared";
import { Kpi, Panel, Segmented } from "./ui";

type Period = "24h" | "7d" | "30d" | "90d" | "all";
const PAGE = 50;
const FORMAT_ICON: Record<string, string> = { email: "mail", docx: "doc", pptx: "slides", pdf: "printer" };

export default function QuestionAudit() {
  const { t } = useI18n();
  const lat = useLattice();
  const [period, setPeriod] = useState<Period>("7d");
  const [user, setUser] = useState("");
  const [outcome, setOutcome] = useState("");
  const [flag, setFlag] = useState("");
  const [minLevel, setMinLevel] = useState<string>("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => setOffset(0), [period, user, outcome, flag, minLevel, q]);
  useEffect(() => {
    const h = setTimeout(() => {
      api.auditQuestions({ period, user, outcome, flag, q, min_level: minLevel ? Number(minLevel) : null, limit: PAGE, offset })
        .then((p) => { setPage(p); setError(null); })
        .catch((e) => setError(e.message));
    }, 200);
    return () => clearTimeout(h);
  }, [period, user, outcome, flag, minLevel, q, offset]);

  const k = page?.kpis;
  return (
    <>
      <Panel headerOnly title={t("qaudit.title")} subtitle={t("qaudit.sub")}
        actions={<Segmented value={period} onChange={setPeriod} options={(["24h", "7d", "30d", "90d", "all"] as Period[]).map((p) => ({ value: p, label: t(`qaudit.period.${p}`) }))} />} />

      <div className="kpis">
        <Kpi icon="sparkle" tone="blue" value={k?.questions ?? "—"} label={t("qaudit.kQuestions")} tag={k ? t("qaudit.kAnswered", { n: k.answered }) : ""}
          onClick={() => { setFlag(""); setOutcome(""); }} />
        <Kpi icon="users" tone="cyan" value={k?.people ?? "—"} label={t("qaudit.kPeople")} />
        <Kpi icon="alert" tone="red" value={k?.blocked ?? "—"} label={t("qaudit.kBlocked")} tag={t("qaudit.kBlockedTag")}
          onClick={() => setFlag("blocked")} />
        <Kpi icon="lock" tone="amber" value={k?.withheld ?? "—"} label={t("qaudit.kWithheld")} tag={t("qaudit.kWithheldTag")}
          onClick={() => setFlag("withheld")} />
        <Kpi icon="doc" tone="violet" value={k?.exports ?? "—"} label={t("qaudit.kExports")} tag={t("qaudit.kExportsTag")}
          onClick={() => setFlag("exported")} />
      </div>

      <section className="panel">
        <div className="toolbar qa-filters">
          <div className="search-field grow">
            <Icon name="search" size={15} />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("qaudit.search")} />
          </div>
          <select value={user} onChange={(e) => setUser(e.target.value)} aria-label={t("qaudit.person")}>
            <option value="">{t("qaudit.everyone")}</option>
            {(page?.people ?? []).map((p) => <option key={p} value={p}>{p}</option>)}
            <option value="-">{t("qaudit.service")}</option>
          </select>
          <select value={flag} onChange={(e) => setFlag(e.target.value)} aria-label={t("qaudit.flag")}>
            <option value="">{t("qaudit.flags.all")}</option>
            {["attention", "blocked", "withheld", "exported"].map((f) => <option key={f} value={f}>{t(`qaudit.flags.${f}`)}</option>)}
          </select>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label={t("qaudit.outcome")}>
            <option value="">{t("qaudit.anyOutcome")}</option>
            {["ANSWERED", "NO_ROWS", "NOT_FOUND", "DECLINED", "BLOCKED", "ERROR"].map((o) => <option key={o} value={o}>{t(`ask.outcome.${o}`)}</option>)}
          </select>
          <select value={minLevel} onChange={(e) => setMinLevel(e.target.value)} aria-label={t("qaudit.minLevel")}>
            <option value="">{t("qaudit.anyLevel")}</option>
            {lat.levels.map((l) => <option key={l.value} value={l.value}>{t("qaudit.atLeast", { level: `${l.value} ${lat.levelName(l.value)}` })}</option>)}
          </select>
        </div>
        {error && <div className="banner error" style={{ margin: 16 }}>{error}</div>}
        {!page ? (
          <div className="empty">{t("common.loading")}</div>
        ) : page.items.length === 0 ? (
          <div className="empty">{t("qaudit.none")}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("qaudit.colWhen")}</th>
                  <th>{t("qaudit.colPerson")}</th>
                  <th>{t("qaudit.colQuestion")}</th>
                  <th>{t("qaudit.colOutcome")}</th>
                  <th>{t("qaudit.colLabel")}</th>
                  <th>{t("qaudit.colQueries")}</th>
                  <th className="num">{t("qaudit.colRows")}</th>
                  <th>{t("qaudit.colCreated")}</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((r) => <AuditRow key={r.id} r={r} onOpen={() => setOpen(r.id)} />)}
              </tbody>
            </table>
          </div>
        )}
        {page && page.total > PAGE && (
          <div className="toolbar" style={{ padding: "12px 16px", justifyContent: "space-between" }}>
            <span className="muted small">{t("qaudit.showing", { from: offset + 1, to: Math.min(offset + PAGE, page.total), total: page.total })}</span>
            <span>
              <button className="btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t("qaudit.prev")}</button>{" "}
              <button className="btn-ghost btn-sm" disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>{t("qaudit.next")}</button>
            </span>
          </div>
        )}
      </section>
      {open && <AuditDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function AuditRow({ r, onOpen }: { r: AuditQuestion; onOpen: () => void }) {
  const { t } = useI18n();
  return (
    <tr className="clickable" onClick={onOpen}>
      <td className="nowrap muted" title={r.created_at ? new Date(r.created_at).toLocaleString() : ""}>{relTime(r.created_at, t)}</td>
      <td className="strong mono nowrap">
        {r.asked_by ?? <span className="muted">{t("qaudit.service")}</span>}
        {r.mode === "service" && r.asked_by && <span className="chip neutral" style={{ marginInlineStart: 6 }} title={t("qaudit.viaKbHint")}>{t("qaudit.viaKb")}</span>}
      </td>
      <td className="qa-question"><span dir="auto">{r.question}</span>{r.database_name && <span className="cell-sub">{r.database_name}</span>}</td>
      <td><OutcomeTag outcome={r.outcome} /></td>
      <td>{r.label_level != null && r.label_category ? <CellChip level={r.label_level} category={r.label_category} size="sm" /> : <span className="muted">—</span>}</td>
      <td className="nowrap">
        {r.queries}
        {r.queries_blocked > 0 && <span className="chip sensitive" style={{ marginInlineStart: 6 }}>{t("qaudit.nBlocked", { n: r.queries_blocked })}</span>}
        {r.columns_withheld > 0 && <span className="chip warn-chip" style={{ marginInlineStart: 6 }} title={t("qaudit.withheldHint")}><Icon name="lock" size={11} /> {r.columns_withheld}</span>}
      </td>
      <td className="num">{r.row_count}</td>
      <td className="nowrap">
        {r.exports.length ? r.exports.map((f, i) => <span key={i} className="qa-export" title={t(`ask.create.${f}`)}><Icon name={FORMAT_ICON[f] ?? "doc"} size={14} /></span>) : <span className="muted">—</span>}
      </td>
    </tr>
  );
}

function AuditDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useI18n();
  const [d, setD] = useState<AuditQuestionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.auditQuestion(id).then(setD).catch((e) => setError(e.message));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [id, onClose]);

  return (
    <div className="overlay side" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" style={{ width: 620 }} role="dialog" aria-label={t("qaudit.detail")}>
        <div className="dialog-head">
          <h3 dir="auto">{d?.question ?? t("common.loading")}</h3>
          <button className="icon-btn sm" onClick={onClose} aria-label={t("common.close")}><Icon name="x" size={14} /></button>
        </div>
        <div className="dialog-body">
          {error && <div className="banner error">{error}</div>}
          {d && (
            <>
              <div className="kv-rows">
                <Kv k={t("qaudit.colPerson")} v={d.asked_by ?? t("qaudit.service")} />
                <Kv k={t("qaudit.colWhen")} v={d.created_at ? new Date(d.created_at).toLocaleString() : "—"} />
                <Kv k={t("qaudit.colOutcome")} v={<OutcomeTag outcome={d.outcome} />} />
                <Kv k={t("qaudit.colLabel")} v={d.label_level != null && d.label_category ? <CellChip level={d.label_level} category={d.label_category} showLabel /> : "—"} />
                <Kv k={t("qaudit.cellsHeld")} v={d.cells_held.length ? <span className="cells">{d.cells_held.map((c) => <CellChip key={c} token={c} size="sm" />)}</span> : t("qaudit.allCells")} />
                <Kv k={t("qaudit.cellsUsed")} v={d.cells_used.length ? <span className="cells">{d.cells_used.map((c) => <CellChip key={c} token={c} size="sm" />)}</span> : "—"} />
                <Kv k={t("qaudit.database")} v={`${d.database_name ?? "—"}${d.scope_database ? ` (${t("qaudit.scoped", { db: d.scope_database })})` : ""}`} />
                <Kv k={t("qaudit.trust")} v={<TrustScore score={d.trust_score} />} />
                <Kv k={t("qaudit.withheld")} v={d.columns_withheld} />
                <Kv k={t("qaudit.duration")} v={d.duration_ms != null ? `${d.duration_ms} ms` : "—"} />
              </div>

              {d.tables_list.length > 0 && (
                <>
                  <div className="suggest-label" style={{ margin: "16px 0 8px" }}>{t("qaudit.tables")}</div>
                  <div className="next-chips">{d.tables_list.map((x) => <span key={x} className="chip neutral mono">{x}</span>)}</div>
                </>
              )}

              <div className="suggest-label" style={{ margin: "18px 0 8px" }}>{t("qaudit.queriesRun", { n: d.queries_list.length })}</div>
              {d.queries_list.length === 0 && <div className="muted small">{t("qaudit.noQueries")}</div>}
              {d.queries_list.map((qq, i) => (
                <div key={i} className="askx-sql">
                  <div className="askx-sql-head">
                    <span className={`outcome-tag ${qq.verdict === "ALLOWED" ? "good" : "bad"}`}>{t(`ask.verdict.${qq.verdict}`)}</span>
                    {qq.rows != null && <span className="muted small">{t("ask.nRows", { n: qq.rows })}</span>}
                    {qq.duration_ms != null && <span className="muted small">{qq.duration_ms} ms</span>}
                  </div>
                  {qq.sql && <pre className="askx-sql-code" dir="ltr">{qq.sql}</pre>}
                  {(qq.reason || qq.error) && <div className="muted small" dir="auto">{qq.reason ?? qq.error}</div>}
                </div>
              ))}

              <div className="suggest-label" style={{ margin: "18px 0 8px" }}>{t("qaudit.created")}</div>
              {d.exports_list.length === 0 ? (
                <div className="muted small">{t("qaudit.noExports")}</div>
              ) : (
                <div className="list-rows">
                  {d.exports_list.map((e, i) => (
                    <div key={i} className="list-row" style={{ padding: "7px 0" }}>
                      <Icon name={FORMAT_ICON[e.format] ?? "doc"} size={15} />
                      <span className="list-row-main">{t(`ask.create.${e.format}`)}<span className="list-row-sub">{e.asked_by ?? ""}</span></span>
                      <span className="muted small">{e.created_at ? new Date(e.created_at).toLocaleString() : ""}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="kv-row">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}
