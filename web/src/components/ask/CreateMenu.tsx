/**
 * Create — turn an answer into an email draft, a Word document, a
 * PowerPoint deck or a PDF. Word/PowerPoint are built by the answer
 * service (answer/exports.py); the email draft and the PDF (a print-ready
 * page, "Save as PDF") are built here so Arabic renders exactly as on
 * screen. Every one is logged in the question audit, and every one
 * carries the answer's classification.
 */
import { useEffect, useRef, useState } from "react";
import { answerApi, AskResult, CreateFormat } from "../../api";
import { useAuth } from "../../auth";
import { useI18n } from "../../i18n";
import { cellToken, useLattice } from "../../lib/lattice";
import Icon from "../Icon";

const ITEMS: { format: CreateFormat; icon: string }[] = [
  { format: "email", icon: "mail" },
  { format: "docx", icon: "doc" },
  { format: "pptx", icon: "slides" },
  { format: "pdf", icon: "printer" },
];

function useMarking(result: AskResult) {
  const lat = useLattice();
  const { t, lang } = useI18n();
  const lv = result.access?.label_level ?? null;
  const cat = result.access?.label_category ?? null;
  if (lv == null || !cat) return { text: t("ask.create.noLabel"), color: "#6B7280" };
  const name = lat.levelName(lv);
  const parts = [lang === "ar" ? name : name.toUpperCase(), cellToken(lv, cat)];
  const label = lat.cellLabel(lv, cat);
  if (label) parts.push(label);
  return { text: parts.join(" · "), color: lat.color(lv) };
}

function content(result: AskResult) {
  return {
    answer: result.answer,
    notes: result.notes,
    results: result.results ?? [],
    tables: result.graph_nodes.map((n) => n.label),
    trust_label: result.trust_label,
    trust_score: result.trust_score,
  };
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

const TONE: Record<CreateFormat, string> = { email: "blue", docx: "violet", pptx: "amber", pdf: "red" };

/** `variant="menu"`: a Create button with a drop-down (the question bar).
 * `variant="card"`: the four formats as buttons (the answer's side column). */
export default function CreateMenu({ question, result, variant = "menu" }: { question: string; result: AskResult; variant?: "menu" | "card" }) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<CreateFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const marking = useMarking(result);
  const canCreate = !!result.ask_id && !!(result.answer || result.chunks.length);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => wrap.current && !wrap.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function run(format: CreateFormat) {
    if (!result.ask_id) return;
    setOpen(false);
    setError(null);
    setBusy(format);
    try {
      if (format === "docx" || format === "pptx") {
        const { blob, filename } = await answerApi.createFile({ ask_id: result.ask_id, format, lang, content: content(result) });
        saveBlob(blob, filename);
      } else if (format === "email") {
        await answerApi.logCreate(result.ask_id, "email");
        setEmail(true);
      } else {
        await answerApi.logCreate(result.ask_id, "pdf");
        printAnswer(question, result, marking, t, lang);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (variant === "card") {
    return (
      <div className="create-card">
        <div className="create-grid">
          {ITEMS.map((it) => (
            <button key={it.format} type="button" className="create-tile" disabled={!canCreate || !!busy} onClick={() => run(it.format)}
              title={t(`ask.create.${it.format}Desc`)}>
              <span className={`askx-kind-icon tone-${TONE[it.format]}`}>
                {busy === it.format ? <span className="spinner sm" /> : <Icon name={it.icon} size={16} />}
              </span>
              <b>{t(`ask.create.${it.format}`)}</b>
            </button>
          ))}
        </div>
        <div className="create-pop-foot" style={{ marginTop: 10, borderTop: "none", padding: 0 }}>
          <span className="create-mark" style={{ ["--cell" as string]: marking.color }} />
          {canCreate ? t("ask.create.marked", { mark: marking.text }) : t("ask.create.nothing")}
        </div>
        {error && <div className="banner error" style={{ marginTop: 10, marginBottom: 0 }}>{error}</div>}
        {email && <EmailDialog question={question} result={result} marking={marking.text} onClose={() => setEmail(false)} />}
      </div>
    );
  }

  return (
    <div className="create-menu" ref={wrap}>
      <button className="btn-primary btn-sm" onClick={() => setOpen((v) => !v)} disabled={!canCreate || !!busy}
        aria-haspopup="menu" aria-expanded={open} title={canCreate ? t("ask.create.hint") : t("ask.create.nothing")}>
        {busy ? <span className="spinner sm" /> : <Icon name="wand" size={15} />} {t("ask.create.button")}
        <Icon name="chevron" size={13} className="create-caret" />
      </button>
      {open && (
        <div className="popover create-pop" role="menu">
          <div className="create-pop-head">{t("ask.create.title")}</div>
          {ITEMS.map((it) => (
            <button key={it.format} type="button" role="menuitem" className="create-item" onClick={() => run(it.format)}>
              <span className={`askx-kind-icon tone-${TONE[it.format]}`}>
                <Icon name={it.icon} size={16} />
              </span>
              <span>
                <b>{t(`ask.create.${it.format}`)}</b>
                <span className="muted small">{t(`ask.create.${it.format}Desc`)}</span>
              </span>
            </button>
          ))}
          <div className="create-pop-foot">
            <span className="create-mark" style={{ ["--cell" as string]: marking.color }} />
            {t("ask.create.marked", { mark: marking.text })}
          </div>
        </div>
      )}
      {error && <div className="banner error create-error">{error}</div>}
      {email && <EmailDialog question={question} result={result} marking={marking.text} onClose={() => setEmail(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Email draft
// ---------------------------------------------------------------------------

function emailBody(question: string, result: AskResult, marking: string, t: (k: string, v?: Record<string, string | number>) => string, who: string) {
  const lines: string[] = [marking, "", `${t("ask.create.emailQuestion")}: ${question}`, ""];
  lines.push(result.answer || result.notes || "");
  const rs = (result.results ?? []).find((r) => r.rows.length);
  if (rs) {
    lines.push("", rs.purpose ? `${rs.purpose}:` : `${t("ask.create.emailData")}:`);
    lines.push(rs.columns.join(" | "));
    rs.rows.slice(0, 10).forEach((row) => lines.push(row.map((v) => v ?? "").join(" | ")));
    if (rs.rows.length > 10) lines.push(t("ask.create.emailMore", { n: rs.rows.length - 10 }));
  }
  const tables = result.graph_nodes.map((n) => n.label);
  if (tables.length) lines.push("", `${t("ask.create.emailSources")}: ${tables.join(", ")}`);
  if (result.trust_score != null) lines.push(`${t("ask.create.emailTrust")}: ${Math.round(result.trust_score * 100)}%`);
  lines.push(`${t("ask.create.emailRef")}: ${(result.ask_id ?? "").slice(0, 8)}${who ? ` · ${who}` : ""}`, "", marking);
  return lines.join("\n");
}

function EmailDialog({ question, result, marking, onClose }: { question: string; result: AskResult; marking: string; onClose: () => void }) {
  const { t } = useI18n();
  const { principal } = useAuth();
  const [subject, setSubject] = useState(`[${marking}] ${question}`);
  const [body, setBody] = useState(() => emailBody(question, result, marking, t, principal?.display_name ?? ""));
  const [copied, setCopied] = useState(false);
  const mailto = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body.slice(0, 1800))}`;
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" style={{ maxWidth: 680 }} role="dialog" aria-label={t("ask.create.email")}>
        <div className="dialog-head">
          <h3><Icon name="mail" size={18} /> {t("ask.create.emailTitle")}</h3>
          <button className="icon-btn sm" onClick={onClose} aria-label={t("common.close")}><Icon name="x" size={14} /></button>
        </div>
        <div className="dialog-body">
          <label className="stack-label">{t("ask.create.emailSubject")}
            <input type="text" dir="auto" value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="stack-label" style={{ marginTop: 12 }}>{t("ask.create.emailBody")}
            <textarea dir="auto" rows={14} value={body} onChange={(e) => setBody(e.target.value)} style={{ fontSize: 12.5, lineHeight: 1.5 }} />
          </label>
          <div className="muted small" style={{ marginTop: 8 }}>{t("ask.create.emailNote")}</div>
        </div>
        <div className="dialog-foot">
          <button className="btn-ghost" onClick={() => {
            navigator.clipboard?.writeText(`${subject}\n\n${body}`).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
          }}>
            <Icon name={copied ? "check" : "copy"} size={14} /> {copied ? t("ask.home.copied") : t("ask.create.emailCopy")}
          </button>
          <a className="btn-primary" href={mailto}><Icon name="mail" size={14} /> {t("ask.create.emailOpen")}</a>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PDF — a print-ready page in a hidden frame ("Save as PDF")
// ---------------------------------------------------------------------------

function esc(s: string | null | undefined) {
  return (s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string);
}

function printAnswer(question: string, result: AskResult, marking: { text: string; color: string },
  t: (k: string, v?: Record<string, string | number>) => string, lang: string) {
  const dir = lang === "ar" ? "rtl" : "ltr";
  const tables = (result.results ?? []).filter((r) => r.rows.length).map((r) => `
    <h2 dir="auto">${esc(r.purpose || t("ask.create.emailData"))}</h2>
    <table><thead><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${r.rows.slice(0, 500).map((row) => `<tr>${row.map((v) => `<td dir="auto">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`).join("");
  const meta = [
    [t("ask.create.emailRef"), (result.ask_id ?? "").slice(0, 8)],
    [t("ask.create.pdfDate"), new Date().toLocaleString()],
    ...(result.database_name ? [[t("ask.create.pdfDb"), result.database_name]] : []),
    ...(result.trust_score != null ? [[t("ask.create.emailTrust"), `${Math.round(result.trust_score * 100)}%`]] : []),
  ].map(([k, v]) => `<span><b>${esc(k)}:</b> ${esc(v)}</span>`).join("");
  const sources = result.graph_nodes.length
    ? `<h2>${esc(t("ask.create.emailSources"))}</h2><p class="src">${result.graph_nodes.map((n) => esc(n.label)).join(" · ")}</p>` : "";
  const html = `<!doctype html><html dir="${dir}" lang="${lang}"><head><meta charset="utf-8"><title>${esc(question)}</title>
<style>
  @page { margin: 22mm 16mm; }
  body { font-family: "Segoe UI", Tahoma, Arial, sans-serif; color: #0f172a; font-size: 11pt; margin: 0; }
  .mark { position: fixed; left: 0; right: 0; text-align: center; font-weight: 700; font-size: 9pt; color: ${marking.color};
          letter-spacing: .04em; }
  .mark.top { top: -14mm; } .mark.bottom { bottom: -14mm; }
  h1 { font-size: 18pt; margin: 0 0 6px; } h2 { font-size: 12.5pt; margin: 20px 0 8px; }
  .meta { color: #475569; font-size: 9pt; display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }
  .answer { font-size: 11.5pt; line-height: 1.6; white-space: pre-wrap; border-inline-start: 3px solid ${marking.color};
            padding-inline-start: 12px; }
  table { border-collapse: collapse; width: 100%; font-size: 9.5pt; } th, td { border: 1px solid #cbd5e1; padding: 4px 6px; text-align: start; }
  th { background: #f1f5f9; } .src { color: #475569; font-size: 9.5pt; }
  .foot { margin-top: 24px; color: #94a3b8; font-size: 8.5pt; }
</style></head><body>
  <div class="mark top">${esc(marking.text)}</div><div class="mark bottom">${esc(marking.text)}</div>
  <h1 dir="auto">${esc(question)}</h1><div class="meta">${meta}</div>
  <div class="answer" dir="auto">${esc(result.answer || result.notes)}</div>
  ${tables}${sources}
  <div class="foot">${esc(t("ask.create.pdfFoot"))}</div>
</body></html>`;
  const frame = document.createElement("iframe");
  frame.style.cssText = "position:fixed;width:0;height:0;border:0;right:0;bottom:0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument!;
  doc.open();
  doc.write(html);
  doc.close();
  setTimeout(() => {
    frame.contentWindow?.focus();
    frame.contentWindow?.print();
    setTimeout(() => frame.remove(), 2000);
  }, 250);
}
