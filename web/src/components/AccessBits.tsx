/**
 * Shared pieces of the data-access realm UI: where a cell came from
 * (SourceTag), why an object sits in its cell (ExplainDrawer), moving
 * objects (MoveDialog) and the levels x categories matrix editor used by
 * roles and by "View as".
 */
import { ReactNode, useEffect, useState } from "react";
import { AccessObject, AccessSource, AccessTarget, api, ExplainOut } from "../api";
import { useI18n } from "../i18n";
import { CellChip, cellToken, useLattice } from "../lib/lattice";
import Icon from "./Icon";
import { Tone } from "./ui";

export const SOURCE_TONE: Record<string, Tone> = {
  manual: "blue",
  rule: "cyan",
  inherited: "gray",
  ai: "violet",
  auto: "amber",
  default: "red",
  none: "gray",
};

export function SourceTag({ source, title }: { source: string | null | undefined; title?: string }) {
  const { t } = useI18n();
  if (!source) return null;
  return (
    <span className={`src-tag tone-${SOURCE_TONE[source] ?? "gray"}`} title={title}>
      {t(`access.src.${source}`)}
    </span>
  );
}

/** Level source and category source; one tag when they're the same. */
export function SourcePair({ obj }: { obj: Pick<AccessObject, "level_source" | "category_source" | "detail"> }) {
  const { t } = useI18n();
  const lv = obj.level_source;
  const cat = obj.category_source;
  const lvTip = obj.detail?.level?.via;
  const catTip = obj.detail?.category?.via;
  if (lv === cat) return <SourceTag source={lv} title={lvTip === catTip ? lvTip : `${lvTip ?? ""} · ${catTip ?? ""}`} />;
  return (
    <span className="src-pair">
      <SourceTag source={lv} title={`${t("access.levelWord")}: ${lvTip ?? ""}`} />
      <SourceTag source={cat} title={`${t("access.categoryWord")}: ${catTip ?? ""}`} />
    </span>
  );
}

export function targetOf(o: AccessObject): AccessTarget {
  if (o.kind === "schema") return { kind: "schema", database_id: o.database_id, schema_name: o.schema };
  return { kind: o.kind, id: o.id };
}

export function objectLabel(o: AccessObject): string {
  if (o.kind === "database") return o.database;
  if (o.kind === "schema") return `${o.database}.${o.schema}`;
  if (o.kind === "table") return `${o.database}.${o.schema}.${o.name}`;
  return `${o.database}.${o.schema}.${o.table}.${o.name}`;
}

function Overlay({ children, onClose, side }: { children: ReactNode; onClose: () => void; side?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className={`overlay${side ? " side" : ""}`} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ explain
const LAYER_ORDER: AccessSource[] = ["manual", "rule", "inherited", "ai", "auto", "default"];

export function ExplainDrawer({ target, onClose }: { target: AccessTarget; onClose: () => void }) {
  const { t } = useI18n();
  const lat = useLattice();
  const [data, setData] = useState<ExplainOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.accessExplain(target).then(setData).catch((e) => setError(e.message));
  }, [target]);

  function axis(which: "level" | "category") {
    if (!data) return null;
    const a = data[which];
    const cands = [...a.candidates].sort((x, y) => LAYER_ORDER.indexOf(x.layer) - LAYER_ORDER.indexOf(y.layer));
    return (
      <div style={{ marginBottom: 18 }}>
        <div className="kv-row" style={{ paddingTop: 0 }}>
          <span className="k">{which === "level" ? t("access.levelWord") : t("access.categoryWord")}</span>
          <span className="v">
            <b>{which === "level" ? `${a.value ?? "—"} · ${lat.levelName(a.value as number)}` : `${a.value ?? "—"} · ${lat.categoryName(a.value as string)}`}</b>{" "}
            <SourceTag source={a.source} />
          </span>
        </div>
        {cands.length === 0 ? (
          <div className="muted small">{t("access.noCandidates")}</div>
        ) : (
          cands.map((c, i) => (
            <div key={i} className={`why-layer ${c.won ? "won" : "lost"}`}>
              <SourceTag source={c.layer} />
              <span style={{ flex: 1 }}>{c.via}</span>
              <span className="v">{c.won ? <Icon name="check" size={15} style={{ color: "var(--green)" }} /> : String(c.value)}</span>
            </div>
          ))
        )}
      </div>
    );
  }

  return (
    <Overlay onClose={onClose} side>
      <aside className="drawer" role="dialog" aria-label={t("access.whyTitle")}>
        <div className="dialog-head">
          <h3>
            {t("access.whyTitle")}
            <div className="muted small mono" style={{ fontWeight: 400, marginTop: 4 }}>{data?.target}</div>
          </h3>
          <button className="icon-btn sm" onClick={onClose} aria-label={t("common.close")}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <div className="dialog-body">
          {error && <div className="banner error">{error}</div>}
          {!data && !error && <div className="empty">{t("common.loading")}</div>}
          {data && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
                <CellChip level={data.level.value as number} category={data.category.value as string} showLabel />
                <span className="muted small">{t("access.whyIntro")}</span>
              </div>
              {data.pin && (
                <div className="banner info">
                  {t("access.pinnedBy", { who: data.pin.set_by ?? "?", when: data.pin.set_at ? new Date(data.pin.set_at).toLocaleString() : "" })}
                  {data.pin.reason ? ` — ${data.pin.reason}` : ""}
                </div>
              )}
              {data.review_reason && (
                <div className={`banner ${data.review ? "warn" : "info"}`}>
                  {data.review ? t("access.reviewPendingNote") : t("access.reviewKeptNote")} {data.review_reason}
                </div>
              )}
              {axis("level")}
              {axis("category")}
              <div className="muted small">{t("access.whyFooter")}</div>
            </>
          )}
        </div>
      </aside>
    </Overlay>
  );
}

// -------------------------------------------------------------------- move
export function MoveDialog({
  count,
  initial,
  onCancel,
  onConfirm,
}: {
  count: number;
  initial?: { level: number | null; category: string | null };
  onCancel: () => void;
  onConfirm: (level: number | null, category: string | null, reason: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const lat = useLattice();
  const [level, setLevel] = useState<string>(initial?.level != null ? String(initial.level) : "");
  const [category, setCategory] = useState<string>(initial?.category ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(level ? Number(level) : null, category || null, reason);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Overlay onClose={onCancel}>
      <div className="dialog" role="dialog" aria-label={t("access.moveTitle")}>
        <div className="dialog-head">
          <h3>{t("access.moveTitle", { n: count })}</h3>
          <button className="icon-btn sm" onClick={onCancel} aria-label={t("common.close")}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <div className="dialog-body">
          <p className="muted small" style={{ marginTop: 0 }}>{t("access.moveIntro")}</p>
          <div className="field-grid">
            <label>
              {t("access.levelWord")}
              <select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">{t("access.keepAxis")}</option>
                {lat.levels.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.value} · {lat.levelName(l.value)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("access.categoryWord")}
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">{t("access.keepAxis")}</option>
                {lat.categories.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.key} · {lat.categoryName(c.key)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {(level || category) && (
            <div style={{ marginTop: 12 }} className="muted small">
              {t("access.moveTo")} <CellChip level={level ? Number(level) : null} category={category || null} size="sm" />
            </div>
          )}
          <label className="stack-label" style={{ marginTop: 14 }}>
            {t("access.reason")}
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("access.reasonPlaceholder")} />
          </label>
          {error && <div className="banner error" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
        </div>
        <div className="dialog-foot">
          <button className="btn-ghost" onClick={onCancel}>{t("common.cancel")}</button>
          <button className="btn-primary" onClick={go} disabled={busy || (!level && !category)}>
            {busy ? t("common.saving") : t("access.moveButton")}
          </button>
        </div>
      </div>
    </Overlay>
  );
}

export function ConfirmDialog({ title, body, confirm, onCancel, onConfirm, danger }: {
  title: string; body: ReactNode; confirm: string; onCancel: () => void; onConfirm: () => void; danger?: boolean;
}) {
  const { t } = useI18n();
  return (
    <Overlay onClose={onCancel}>
      <div className="dialog" role="alertdialog" aria-label={title}>
        <div className="dialog-head"><h3>{title}</h3></div>
        <div className="dialog-body muted" style={{ fontSize: 12.5 }}>{body}</div>
        <div className="dialog-foot">
          <button className="btn-ghost" onClick={onCancel}>{t("common.cancel")}</button>
          <button className={danger ? "btn-primary danger-fill" : "btn-primary"} onClick={onConfirm}>{confirm}</button>
        </div>
      </div>
    </Overlay>
  );
}

// ------------------------------------------------------------------ matrix
/** Levels (rows, top = least sensitive) x categories (columns). Click a
 * cell to toggle it, a row/column header to toggle the whole row/column.
 * The reserved quarantine category can never be selected. */
export function MatrixEditor({
  value,
  onChange,
  readOnly,
  allowReserved = false,
}: {
  value: Set<string>;
  onChange?: (next: Set<string>) => void;
  readOnly?: boolean;
  allowReserved?: boolean;
}) {
  const { t } = useI18n();
  const lat = useLattice();
  const cats = lat.categories;
  const selectable = (key: string) => allowReserved || lat.grantable.has(key);

  function toggle(tokens: string[]) {
    if (readOnly || !onChange) return;
    const next = new Set(value);
    const allOn = tokens.every((tk) => next.has(tk));
    for (const tk of tokens) allOn ? next.delete(tk) : next.add(tk);
    onChange(next);
  }

  return (
    <div className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th className="lvl">{t("access.levelWord")} \ {t("access.categoryWord")}</th>
            {cats.map((c) => (
              <th
                key={c.key}
                className={!readOnly && selectable(c.key) ? "clickable" : ""}
                title={lat.categoryName(c.key)}
                onClick={() => selectable(c.key) && toggle(lat.levels.map((l) => cellToken(l.value, c.key)))}
              >
                <span className="catkey" style={{ display: "block", fontWeight: 800, color: "var(--text)" }}>{c.key}</span>
                <span style={{ fontSize: 9.5 }}>{lat.categoryName(c.key)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lat.levels.map((l) => (
            <tr key={l.value}>
              <th
                className={`lvl${readOnly ? "" : " clickable"}`}
                onClick={() => toggle(cats.filter((c) => selectable(c.key)).map((c) => cellToken(l.value, c.key)))}
              >
                <span className="swatch" style={{ background: lat.color(l.value), borderRadius: 3, marginInlineEnd: 8, verticalAlign: -1 }} />
                <b style={{ color: "var(--text)" }}>{l.value}</b> {lat.levelName(l.value)}
              </th>
              {cats.map((c) => {
                const tk = cellToken(l.value, c.key);
                const on = value.has(tk);
                const label = lat.cellLabel(l.value, c.key);
                return (
                  <td key={c.key}>
                    <button
                      type="button"
                      className={`matrix-cell${on ? " on" : ""}`}
                      style={{ ["--cell" as string]: lat.color(l.value) }}
                      disabled={!selectable(c.key) || readOnly}
                      aria-pressed={on}
                      title={`${tk}${label ? ` — ${label}` : ""}${!selectable(c.key) ? ` — ${t("access.notGrantable")}` : ""}`}
                      onClick={() => toggle([tk])}
                    >
                      {tk}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
