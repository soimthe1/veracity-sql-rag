import { useCallback, useEffect, useMemo, useState } from "react";
import { AccessKind, AccessObject, AccessRule, AccessAuditRow, api, GridOut, RulePreview, SimulateOut } from "../api";
import { useI18n } from "../i18n";
import { CellChip, cellToken, sortCellTokens, useLattice } from "../lib/lattice";
import { fmtDate, relTime } from "../lib/format";
import Icon from "./Icon";
import { ConfirmDialog, ExplainDrawer, MatrixEditor, MoveDialog, objectLabel, SourcePair, SourceTag, targetOf } from "./AccessBits";
import { Kpi, Pager, Panel, Segmented } from "./ui";

type Tab = "buckets" | "rules" | "review" | "simulate" | "history";
const PAGE = 50;

/**
 * Data access: where every database, schema, table and column sits in the
 * levels x categories matrix, how it got there, and the controls to change
 * it — move objects (manual placement), write rules, review AI
 * disagreements, and check what a set of cells can see.
 */
export default function DataAccess({ onOpenEntity }: { onOpenEntity?: (id: string) => void }) {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("buckets");
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [recomputing, setRecomputing] = useState(false);
  const [version, setVersion] = useState(0);

  const refreshReviewCount = useCallback(() => {
    api.accessReview(1).then((r) => setReviewCount(r.total)).catch(() => void 0);
  }, []);
  useEffect(refreshReviewCount, [refreshReviewCount, version]);

  async function recompute() {
    setRecomputing(true);
    try {
      await api.recomputeAccess();
      setVersion((v) => v + 1);
    } finally {
      setRecomputing(false);
    }
  }

  return (
    <div>
      <Panel
        headerOnly
        title={t("access.headerTitle")}
        subtitle={t("access.headerSub")}
        actions={
          <>
            <Segmented
              value={tab}
              onChange={setTab}
              options={[
                { value: "buckets", label: t("access.tabBuckets"), icon: "overview" },
                { value: "rules", label: t("access.tabRules"), icon: "list" },
                { value: "review", label: <>{t("access.tabReview")}{reviewCount ? <span className="nav-badge" style={{ marginInlineStart: 6 }}>{reviewCount}</span> : null}</>, icon: "alert" },
                { value: "simulate", label: t("access.tabSimulate"), icon: "users" },
                { value: "history", label: t("access.tabHistory"), icon: "activity" },
              ]}
            />
            <button className="btn-ghost btn-sm" onClick={recompute} disabled={recomputing} title={t("access.recomputeHint")}>
              <Icon name="refresh" size={14} />
              {recomputing ? t("access.recomputing") : t("access.recompute")}
            </button>
          </>
        }
      />
      {tab === "buckets" && <Buckets key={version} onOpenEntity={onOpenEntity} onChanged={() => { refreshReviewCount(); }} />}
      {tab === "rules" && <Rules key={version} onChanged={refreshReviewCount} />}
      {tab === "review" && <Review key={version} onChanged={refreshReviewCount} />}
      {tab === "simulate" && <Simulate />}
      {tab === "history" && <History key={version} />}
    </div>
  );
}

// ======================================================================= buckets
function Buckets({ onOpenEntity, onChanged }: { onOpenEntity?: (id: string) => void; onChanged: () => void }) {
  const { t } = useI18n();
  const lat = useLattice();
  const [kind, setKind] = useState<AccessKind>("table");
  const [database, setDatabase] = useState("");
  const [databases, setDatabases] = useState<string[]>([]);
  const [grid, setGrid] = useState<GridOut | null>(null);
  const [cell, setCell] = useState<{ level: number | null; category: string | null } | null>(null);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<AccessObject[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [why, setWhy] = useState<AccessObject | null>(null);
  const [move, setMove] = useState<{ ids: string[]; level: number | null; category: string | null } | null>(null);
  const [confirmReset, setConfirmReset] = useState<string[] | null>(null);
  const [dragIds, setDragIds] = useState<string[] | null>(null);
  const [dropCell, setDropCell] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    api.summary().then((s) => setDatabases(s.databases.map((d) => d.database_name).sort())).catch(() => void 0);
  }, []);

  const loadGrid = useCallback(() => {
    api.accessGrid(kind, database || undefined).then(setGrid).catch((e) => setError(e.message));
  }, [kind, database]);
  useEffect(loadGrid, [loadGrid]);

  const loadItems = useCallback(() => {
    if (!cell && !q.trim()) {
      setItems(null);
      return;
    }
    api
      .accessObjects({
        kind, level: cell?.level ?? undefined, category: cell?.category ?? undefined, database: database || undefined,
        q: q.trim() || undefined, limit: PAGE, offset: page * PAGE,
      })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => setError(e.message));
  }, [kind, cell, database, q, page]);
  useEffect(() => {
    const h = setTimeout(loadItems, 200);
    return () => clearTimeout(h);
  }, [loadItems]);

  useEffect(() => {
    setCell(null);
    setPage(0);
    setSelected(new Set());
  }, [kind, database]);
  useEffect(() => setPage(0), [cell, q]);

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of grid?.cells ?? []) m.set(cellToken(c.level, c.category), c.count);
    return m;
  }, [grid]);
  const max = Math.max(1, ...Array.from(counts.values()));
  const unresolved = counts.get("") ?? 0;
  const byId = new Map((items ?? []).map((i) => [i.id, i]));

  function refresh(msg?: string) {
    loadGrid();
    loadItems();
    setSelected(new Set());
    onChanged();
    if (msg) {
      setFlash(msg);
      setTimeout(() => setFlash(null), 3500);
    }
  }

  async function doMove(ids: string[], level: number | null, category: string | null, reason: string) {
    const targets = ids.map((id) => byId.get(id)).filter(Boolean).map((o) => targetOf(o as AccessObject));
    await api.place(targets, level, category, reason || undefined);
    setMove(null);
    refresh(t("access.movedFlash", { n: targets.length, cell: cellToken(level, category) }));
  }

  async function doReset(ids: string[]) {
    const targets = ids.map((id) => byId.get(id)).filter(Boolean).map((o) => targetOf(o as AccessObject));
    const r = await api.resetPlacement(targets);
    setConfirmReset(null);
    refresh(t("access.resetFlash", { n: r.reset }));
  }

  const layers: { src: string; desc: string }[] = [
    { src: "manual", desc: t("access.layerManual") },
    { src: "rule", desc: t("access.layerRule") },
    { src: "inherited", desc: t("access.layerInherited") },
    { src: "ai", desc: t("access.layerAi") },
    { src: "auto", desc: t("access.layerAuto") },
    { src: "default", desc: t("access.layerDefault", { cell: cellToken(lat.defaultLevel, lat.defaultCategory) }) },
  ];

  const selectedPinned = Array.from(selected).filter((id) => byId.get(id)?.pinned);

  return (
    <>
      <div className="kpis">
        <Kpi icon="table" tone="blue" value={grid ? grid.total.toLocaleString() : "—"} label={t(`access.kind.${kind}Plural`)} tag={t("access.inMatrix")} />
        <Kpi
          icon="lock" tone="red" value={grid ? grid.quarantine.toLocaleString() : "—"} label={t("access.kpiQuarantine")}
          tag={t("access.kpiQuarantineTag")}
          onClick={lat.defaultCategory ? () => setCell({ level: null, category: lat.defaultCategory }) : undefined}
        />
        <Kpi icon="alert" tone="amber" value={grid ? grid.review_pending : "—"} label={t("access.kpiReview")} tag={t("access.kpiReviewTag")} />
        <Kpi icon="users" tone="cyan" value={grid ? Math.max(grid.level_sources.manual ?? 0, grid.category_sources.manual ?? 0) : "—"} label={t("access.kpiManual")} tag={t("access.kpiManualTag")} />
        <Kpi icon="list" tone="violet" value={grid ? Math.max(grid.level_sources.rule ?? 0, grid.category_sources.rule ?? 0) : "—"} label={t("access.kpiRuled")} tag={t("access.kpiRuledTag")} />
      </div>

      <div className="cols bucket-cols">
        <Panel title={t("access.gridTitle")} subtitle={t("access.gridSub")}>
          <div className="toolbar" style={{ marginBottom: 12 }}>
            <Segmented
              value={kind}
              onChange={setKind}
              options={[
                { value: "table", label: t("access.kind.tablePlural") },
                { value: "column", label: t("access.kind.columnPlural") },
                { value: "schema", label: t("access.kind.schemaPlural") },
                { value: "database", label: t("access.kind.databasePlural") },
              ]}
            />
            <select value={database} onChange={(e) => setDatabase(e.target.value)} aria-label={t("access.allDatabases")}>
              <option value="">{t("access.allDatabases")}</option>
              {databases.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>
          {error && <div className="banner error">{error}</div>}
          {!grid ? (
            <div className="empty">{t("common.loading")}</div>
          ) : (
            <div className="table-wrap">
              <table className="bucket-grid">
                <thead>
                  <tr>
                    <th className="lvl">{t("access.levelWord")} \ {t("access.categoryWord")}</th>
                    {lat.categories.map((c) => (
                      <th key={c.key} title={lat.categoryName(c.key)}>
                        <span className="catkey">{c.key}</span>
                        {lat.categoryName(c.key)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {lat.levels.map((l) => (
                    <tr key={l.value}>
                      <th className="lvl">
                        <i className="sw" style={{ background: lat.color(l.value) }} />
                        <span>{l.value}</span> {lat.levelName(l.value)}
                      </th>
                      {lat.categories.map((c) => {
                        const tk = cellToken(l.value, c.key);
                        const n = counts.get(tk) ?? 0;
                        const heat = n ? 10 + Math.round((n / max) * 75) : 0;
                        const isSel = cell?.level === l.value && cell?.category === c.key;
                        return (
                          <td key={c.key}>
                            <button
                              type="button"
                              className={`bucket-cell${n ? "" : " is-empty"}${heat > 45 ? " hot" : ""}${c.reserved ? " quarantine" : ""}${isSel ? " selected" : ""}${dropCell === tk ? " drop" : ""}`}
                              style={{ ["--cell" as string]: lat.color(l.value), ["--heat" as string]: `${heat}%` }}
                              title={`${tk} — ${lat.levelName(l.value)} · ${lat.categoryName(c.key)}${lat.cellLabel(l.value, c.key) ? ` — ${lat.cellLabel(l.value, c.key)}` : ""}: ${n}`}
                              onClick={() => setCell(isSel ? null : { level: l.value, category: c.key })}
                              onDragOver={(e) => {
                                if (!dragIds) return;
                                e.preventDefault();
                                setDropCell(tk);
                              }}
                              onDragLeave={() => setDropCell((d) => (d === tk ? null : d))}
                              onDrop={(e) => {
                                e.preventDefault();
                                setDropCell(null);
                                if (dragIds?.length) setMove({ ids: dragIds, level: l.value, category: c.key });
                                setDragIds(null);
                              }}
                            >
                              {n || "·"}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              {unresolved > 0 && <div className="muted small" style={{ marginTop: 8 }}>{t("access.unresolvedNote", { n: unresolved })}</div>}
              <div className="muted small" style={{ marginTop: 10 }}>{t("access.dragHint")}</div>
            </div>
          )}
        </Panel>

        <Panel title={t("access.methodTitle")} subtitle={t("access.methodSub")}>
          <div className="layer-list">
            {layers.map((ly, i) => {
              const n = grid ? grid.level_sources[ly.src] ?? 0 : 0;
              return (
                <div key={ly.src} className="layer-row">
                  <span className={`layer-num tone-${["blue", "cyan", "gray", "violet", "amber", "red"][i]}`}>{i + 1}</span>
                  <span style={{ minWidth: 0 }}>
                    <SourceTag source={ly.src} />
                    <span className="desc">{ly.desc}</span>
                  </span>
                  {grid && kind !== "database" && kind !== "schema" ? <span className="n">{n.toLocaleString()}</span> : null}
                </div>
              );
            })}
          </div>
          <div className="knob-note faint" style={{ marginTop: 14 }}>{t("access.methodFooter")}</div>
        </Panel>
      </div>

      <Panel
        title={cell ? t("access.cellObjects", { cell: cellToken(cell.level, cell.category), name: `${lat.levelName(cell.level)} · ${lat.categoryName(cell.category)}` }) : t("access.findTitle")}
        subtitle={cell ? t("access.cellObjectsSub") : t("access.findSub")}
        actions={
          <label className="search-field" style={{ width: 280 }}>
            <Icon name="search" size={15} strokeWidth={1.9} />
            <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("access.searchPlaceholder")} />
          </label>
        }
        bodyClass="panel-body flush"
      >
        {flash && <div className="banner info" style={{ margin: "0 20px 12px" }}>{flash}</div>}
        {selected.size > 0 && (
          <div className="toolbar" style={{ padding: "0 20px 12px" }}>
            <b style={{ fontSize: 12 }}>{t("access.selectedN", { n: selected.size })}</b>
            <button className="btn-primary btn-sm" onClick={() => setMove({ ids: Array.from(selected), level: null, category: null })}>
              {t("access.moveSelected")}
            </button>
            {selectedPinned.length > 0 && (
              <button className="btn-ghost btn-sm" onClick={() => setConfirmReset(selectedPinned)}>
                {t("access.resetSelected", { n: selectedPinned.length })}
              </button>
            )}
            <button className="btn-ghost btn-sm" onClick={() => setSelected(new Set())}>{t("access.clearSelection")}</button>
          </div>
        )}
        {!items ? (
          <div className="empty">{t("access.pickCellHint")}</div>
        ) : items.length === 0 ? (
          <div className="empty">{t("access.noObjects")}</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 32 }}>
                      <input
                        type="checkbox"
                        aria-label={t("access.selectAll")}
                        checked={items.every((i) => selected.has(i.id))}
                        onChange={(e) => setSelected(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())}
                      />
                    </th>
                    <th>{t(`access.kind.${kind}`)}</th>
                    <th>{t("access.cellWord")}</th>
                    <th>{t("access.decidedBy")}</th>
                    <th>{t("access.reasonCol")}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((o) => (
                    <tr
                      key={o.id}
                      className={`draggable${dragIds?.includes(o.id) ? " dragging" : ""}`}
                      draggable
                      onDragStart={(e) => {
                        const ids = selected.has(o.id) ? Array.from(selected) : [o.id];
                        setDragIds(ids);
                        e.dataTransfer.effectAllowed = "move";
                        e.dataTransfer.setData("text/plain", ids.join(","));
                      }}
                      onDragEnd={() => {
                        setDragIds(null);
                        setDropCell(null);
                      }}
                    >
                      <td>
                        <input
                          type="checkbox"
                          aria-label={objectLabel(o)}
                          checked={selected.has(o.id)}
                          onChange={(e) => {
                            const next = new Set(selected);
                            e.target.checked ? next.add(o.id) : next.delete(o.id);
                            setSelected(next);
                          }}
                        />
                      </td>
                      <td className="strong">
                        {o.kind === "column" ? `${o.table}.${o.name}` : o.name}
                        <span className="cell-sub mono">
                          {o.kind === "database" ? t("access.tablesN", { n: o.tables ?? 0 }) : o.kind === "schema" ? `${o.database} · ${t("access.tablesN", { n: o.tables ?? 0 })}` : `${o.database}.${o.schema}${o.data_type ? ` · ${o.data_type}` : ""}`}
                        </span>
                      </td>
                      <td>
                        <CellChip level={o.level} category={o.category} size="sm" />
                        {o.review_pending && <span className="chip warn-chip" style={{ marginInlineStart: 6 }}>{t("access.reviewChip")}</span>}
                      </td>
                      <td><SourcePair obj={o} /></td>
                      <td className="muted" style={{ maxWidth: 380, fontSize: 11.5 }}>
                        {o.detail?.level?.via === o.detail?.category?.via ? o.detail?.level?.via : `${o.detail?.level?.via ?? ""} · ${o.detail?.category?.via ?? ""}`}
                      </td>
                      <td style={{ whiteSpace: "nowrap", textAlign: "end" }}>
                        <button className="btn-ghost btn-sm" onClick={() => setWhy(o)}>{t("access.why")}</button>{" "}
                        <button className="btn-ghost btn-sm" onClick={() => setMove({ ids: [o.id], level: o.level, category: o.category })}>{t("access.move")}</button>
                        {o.kind === "table" && onOpenEntity && (
                          <>
                            {" "}
                            <button className="icon-btn sm" title={t("access.openTable")} onClick={() => onOpenEntity(o.id)}>
                              <Icon name="chevron" size={14} className="flip-rtl" />
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager page={page} pageSize={PAGE} total={total} onPage={setPage} />
          </>
        )}
      </Panel>

      {why && <ExplainDrawer target={targetOf(why)} onClose={() => setWhy(null)} />}
      {move && (
        <MoveDialog
          count={move.ids.length}
          initial={{ level: move.level, category: move.category }}
          onCancel={() => setMove(null)}
          onConfirm={(lv, cat, reason) => doMove(move.ids, lv, cat, reason)}
        />
      )}
      {confirmReset && (
        <ConfirmDialog
          title={t("access.resetTitle")}
          body={t("access.resetBody", { n: confirmReset.length })}
          confirm={t("access.resetConfirm")}
          onCancel={() => setConfirmReset(null)}
          onConfirm={() => doReset(confirmReset)}
        />
      )}
    </>
  );
}

// ========================================================================= rules
const SCOPE_FIELDS: Record<AccessRule["scope"], (keyof AccessRule)[]> = {
  database: ["source_pattern", "database_pattern"],
  schema: ["source_pattern", "database_pattern", "schema_pattern"],
  table: ["database_pattern", "schema_pattern", "table_pattern", "object_type", "domain_pattern", "classification", "sensitivity_flag", "source_pattern"],
  column: ["database_pattern", "schema_pattern", "table_pattern", "column_pattern", "sensitivity_flag", "source_pattern"],
};

const EMPTY_RULE: AccessRule = { name: "", enabled: true, priority: 100, scope: "database", set_level: null, set_category: null };

function ruleSummary(r: AccessRule, t: (k: string) => string): string {
  const parts = SCOPE_FIELDS[r.scope].filter((f) => r[f]).map((f) => `${t(`access.rule.${f}`)} = ${r[f]}`);
  return parts.join(" · ") || t("access.rule.matchAll");
}

function Rules({ onChanged }: { onChanged: () => void }) {
  const { t } = useI18n();
  const lat = useLattice();
  const [rules, setRules] = useState<AccessRule[] | null>(null);
  const [editing, setEditing] = useState<AccessRule | null>(null);
  const [preview, setPreview] = useState<RulePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<AccessRule | null>(null);

  const load = useCallback(() => {
    api.accessRules().then(setRules).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  function clean(r: AccessRule): AccessRule {
    const out: AccessRule = { ...r };
    for (const f of ["source_pattern", "database_pattern", "schema_pattern", "table_pattern", "column_pattern", "object_type", "domain_pattern", "classification", "sensitivity_flag"] as (keyof AccessRule)[]) {
      if (!SCOPE_FIELDS[r.scope].includes(f) || !r[f]) (out as unknown as Record<string, unknown>)[f] = null;
    }
    return out;
  }

  async function runPreview() {
    if (!editing) return;
    setError(null);
    try {
      setPreview(await api.previewAccessRule({ rule: clean(editing), rule_id: editing.id }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function save() {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      if (editing.id) await api.updateAccessRule(editing.id, clean(editing));
      else await api.createAccessRule(clean(editing));
      setEditing(null);
      setPreview(null);
      load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(r: AccessRule) {
    await api.updateAccessRule(r.id!, { ...r, enabled: !r.enabled });
    load();
    onChanged();
  }

  async function remove(r: AccessRule) {
    await api.deleteAccessRule(r.id!);
    setDeleting(null);
    load();
    onChanged();
  }

  const set = (patch: Partial<AccessRule>) => {
    setEditing((e) => (e ? { ...e, ...patch } : e));
    setPreview(null);
  };

  return (
    <>
      <Panel
        title={t("access.rulesTitle")}
        subtitle={t("access.rulesSub")}
        actions={<button className="btn-primary btn-sm" onClick={() => { setEditing({ ...EMPTY_RULE }); setPreview(null); }}>{t("access.newRule")}</button>}
        bodyClass="panel-body flush"
      >
        {error && !editing && <div className="banner error" style={{ margin: "0 20px 12px" }}>{error}</div>}
        {!rules ? (
          <div className="empty">{t("common.loading")}</div>
        ) : rules.length === 0 ? (
          <div className="empty">{t("access.noRules")}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="num">{t("access.rule.priority")}</th>
                  <th>{t("access.rule.name")}</th>
                  <th>{t("access.rule.scope")}</th>
                  <th>{t("access.rule.matches")}</th>
                  <th>{t("access.rule.sets")}</th>
                  <th>{t("access.rule.enabled")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id}>
                    <td className="num strong">{r.priority}</td>
                    <td className="strong">
                      {r.name}
                      {r.note && <span className="cell-sub">{r.note}</span>}
                    </td>
                    <td>{t(`access.kind.${r.scope}`)}</td>
                    <td className="mono" style={{ fontSize: 11.5 }}>{ruleSummary(r, t)}</td>
                    <td>
                      <CellChip level={r.set_level} category={r.set_category} size="sm"
                        title={`${r.set_level != null ? lat.levelName(r.set_level) : t("access.keepAxis")} · ${r.set_category ? lat.categoryName(r.set_category) : t("access.keepAxis")}`} />
                    </td>
                    <td>
                      <label className="toggle">
                        <input type="checkbox" checked={r.enabled} onChange={() => toggleEnabled(r)} />
                        {r.enabled ? t("access.on") : t("access.off")}
                      </label>
                    </td>
                    <td style={{ textAlign: "end", whiteSpace: "nowrap" }}>
                      <button className="btn-ghost btn-sm" onClick={() => { setEditing({ ...r }); setPreview(null); }}>{t("common.edit")}</button>{" "}
                      <button className="btn-ghost btn-sm danger" onClick={() => setDeleting(r)}>{t("common.delete")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title={t("access.rulesHowTitle")}>
        <div className="muted small" style={{ lineHeight: 1.7 }}>{t("access.rulesHow")}</div>
      </Panel>

      {editing && (
        <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && setEditing(null)}>
          <div className="dialog" style={{ maxWidth: 640 }} role="dialog" aria-label={t("access.newRule")}>
            <div className="dialog-head">
              <h3>{editing.id ? t("access.editRule") : t("access.newRule")}</h3>
              <button className="icon-btn sm" onClick={() => setEditing(null)} aria-label={t("common.close")}><Icon name="x" size={14} /></button>
            </div>
            <div className="dialog-body">
              <div className="field-grid">
                <label style={{ gridColumn: "1 / -1" }}>
                  {t("access.rule.name")}
                  <input type="text" value={editing.name} onChange={(e) => set({ name: e.target.value })} placeholder={t("access.rule.namePlaceholder")} />
                </label>
                <label>
                  {t("access.rule.scope")}
                  <select value={editing.scope} onChange={(e) => set({ scope: e.target.value as AccessRule["scope"] })}>
                    {(["database", "schema", "table", "column"] as const).map((s) => (
                      <option key={s} value={s}>{t(`access.kind.${s}`)}</option>
                    ))}
                  </select>
                </label>
                <label>
                  {t("access.rule.priority")}
                  <input type="text" inputMode="numeric" value={String(editing.priority)} onChange={(e) => set({ priority: Number(e.target.value.replace(/\D/g, "")) || 0 })} />
                </label>
                {SCOPE_FIELDS[editing.scope].map((f) => (
                  <label key={f}>
                    {t(`access.rule.${f}`)}
                    <input type="text" value={(editing[f] as string) ?? ""} onChange={(e) => set({ [f]: e.target.value } as Partial<AccessRule>)}
                      placeholder={t(`access.rule.${f}Placeholder`)} />
                  </label>
                ))}
                <label>
                  {t("access.rule.setLevel")}
                  <select value={editing.set_level ?? ""} onChange={(e) => set({ set_level: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">{t("access.keepAxis")}</option>
                    {lat.levels.map((l) => <option key={l.value} value={l.value}>{l.value} · {lat.levelName(l.value)}</option>)}
                  </select>
                </label>
                <label>
                  {t("access.rule.setCategory")}
                  <select value={editing.set_category ?? ""} onChange={(e) => set({ set_category: e.target.value || null })}>
                    <option value="">{t("access.keepAxis")}</option>
                    {lat.categories.map((c) => <option key={c.key} value={c.key}>{c.key} · {lat.categoryName(c.key)}</option>)}
                  </select>
                </label>
                <label style={{ gridColumn: "1 / -1" }}>
                  {t("access.rule.note")}
                  <input type="text" value={editing.note ?? ""} onChange={(e) => set({ note: e.target.value })} />
                </label>
              </div>
              <div className="muted small" style={{ marginTop: 10 }}>{t("access.rule.patternHelp")}</div>
              {preview && (
                <div className="banner info" style={{ marginTop: 14, marginBottom: 0 }}>
                  <b>{t("access.previewResult", { tables: preview.tables_changed, columns: preview.columns_changed })}</b>
                  {preview.examples.length > 0 && (
                    <div className="mono" style={{ marginTop: 6, fontSize: 11 }}>
                      {preview.examples.map((ex) => (
                        <div key={ex.name}>{ex.name}: {ex.from} → {ex.to}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {error && <div className="banner error" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
            </div>
            <div className="dialog-foot">
              <button className="btn-ghost" onClick={runPreview} disabled={!editing.name || (editing.set_level == null && !editing.set_category)}>
                {t("access.preview")}
              </button>
              <button className="btn-primary" onClick={save} disabled={busy || !editing.name || (editing.set_level == null && !editing.set_category)}>
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}
      {deleting && (
        <ConfirmDialog
          title={t("access.deleteRuleTitle")}
          body={t("access.deleteRuleBody", { name: deleting.name })}
          confirm={t("common.delete")}
          danger
          onCancel={() => setDeleting(null)}
          onConfirm={() => remove(deleting)}
        />
      )}
    </>
  );
}

// ======================================================================== review
function Review({ onChanged }: { onChanged: () => void }) {
  const { t } = useI18n();
  const [data, setData] = useState<{ total: number; items: AccessObject[] } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [why, setWhy] = useState<AccessObject | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.accessReview(300).then((d) => {
      setData(d);
      setSelected(new Set());
    });
  }, []);
  useEffect(load, [load]);

  async function act(kind: "accept" | "dismiss", ids: string[]) {
    if (!data) return;
    setBusy(true);
    const targets = data.items.filter((i) => ids.includes(i.id)).map(targetOf);
    try {
      if (kind === "accept") await api.reviewAccept(targets);
      else await api.reviewDismiss(targets);
    } finally {
      setBusy(false);
      load();
      onChanged();
    }
  }

  return (
    <>
      <Panel
        title={t("access.reviewTitle", { n: data?.total ?? 0 })}
        subtitle={t("access.reviewSub")}
        actions={
          selected.size > 0 && (
            <>
              <button className="btn-primary btn-sm" disabled={busy} onClick={() => act("accept", Array.from(selected))}>{t("access.acceptN", { n: selected.size })}</button>
              <button className="btn-ghost btn-sm" disabled={busy} onClick={() => act("dismiss", Array.from(selected))}>{t("access.keepN", { n: selected.size })}</button>
            </>
          )
        }
        bodyClass="panel-body flush"
      >
        {!data ? (
          <div className="empty">{t("common.loading")}</div>
        ) : data.items.length === 0 ? (
          <div className="empty">{t("access.reviewEmpty")}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 32 }}>
                    <input type="checkbox" aria-label={t("access.selectAll")} checked={data.items.every((i) => selected.has(i.id))}
                      onChange={(e) => setSelected(e.target.checked ? new Set(data.items.map((i) => i.id)) : new Set())} />
                  </th>
                  <th>{t("access.object")}</th>
                  <th>{t("access.current")}</th>
                  <th>{t("access.aiSuggests")}</th>
                  <th>{t("access.why")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <input type="checkbox" aria-label={objectLabel(o)} checked={selected.has(o.id)} onChange={(e) => {
                        const next = new Set(selected);
                        e.target.checked ? next.add(o.id) : next.delete(o.id);
                        setSelected(next);
                      }} />
                    </td>
                    <td className="strong">
                      {o.kind === "column" ? `${o.table}.${o.name}` : o.name}
                      <span className="cell-sub mono">{o.database}.{o.schema} · {t(`access.kind.${o.kind}`)}</span>
                    </td>
                    <td>
                      <CellChip level={o.level} category={o.category} size="sm" /> <SourcePair obj={o} />
                    </td>
                    <td>
                      <CellChip level={o.ai_level ?? o.level} category={o.ai_category ?? o.category} size="sm" />
                    </td>
                    <td className="muted" style={{ fontSize: 11.5, maxWidth: 360 }}>
                      {o.detail?.review}
                      <span className="cell-sub">{o.detail?.ai?.via}</span>
                    </td>
                    <td style={{ textAlign: "end", whiteSpace: "nowrap" }}>
                      <button className="btn-ghost btn-sm" onClick={() => setWhy(o)}>{t("access.why")}</button>{" "}
                      <button className="btn-primary btn-sm" disabled={busy} onClick={() => act("accept", [o.id])}>{t("access.accept")}</button>{" "}
                      <button className="btn-ghost btn-sm" disabled={busy} onClick={() => act("dismiss", [o.id])}>{t("access.keep")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {why && <ExplainDrawer target={targetOf(why)} onClose={() => setWhy(null)} />}
    </>
  );
}

// ====================================================================== simulate
function Simulate() {
  const { t } = useI18n();
  const [cells, setCells] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<SimulateOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cells.size === 0) {
      setResult(null);
      return;
    }
    const h = setTimeout(() => {
      api.simulate(Array.from(cells)).then(setResult).catch((e) => setError(e.message));
    }, 250);
    return () => clearTimeout(h);
  }, [cells]);

  return (
    <div className="cols bucket-cols">
      <Panel title={t("access.simTitle")} subtitle={t("access.simSub")}>
        <MatrixEditor value={cells} onChange={setCells} />
        {cells.size > 0 && (
          <div className="toolbar" style={{ marginTop: 12 }}>
            <span className="cells">{sortCellTokens(cells).map((c) => <CellChip key={c} token={c} size="sm" />)}</span>
            <button className="btn-ghost btn-sm" onClick={() => setCells(new Set())}>{t("access.clearSelection")}</button>
          </div>
        )}
      </Panel>
      <Panel title={t("access.simResult")} subtitle={cells.size ? t("access.simResultSub") : t("access.simPick")}>
        {error && <div className="banner error">{error}</div>}
        {!result ? (
          <div className="empty">{t("access.simPick")}</div>
        ) : (
          <>
            <div className="kv-rows">
              <div className="kv-row"><span className="k">{t("access.simTables")}</span><span className="v info">{result.tables_visible} / {result.tables_total}</span></div>
              <div className="kv-row"><span className="k">{t("access.simDatabases")}</span><span className="v info">{result.databases_visible} / {result.databases_total}</span></div>
              <div className="kv-row"><span className="k">{t("access.simColumnsHidden")}</span><span className={`v ${result.columns_hidden ? "warn" : "good"}`}>{result.columns_hidden}</span></div>
            </div>
            <div className="bars compact" style={{ marginTop: 14 }}>
              {result.databases.filter((d) => d.visible > 0).map((d) => (
                <div key={d.database}>
                  <div className="bar-row-head"><span className="bar-row-label">{d.database}</span><span className="bar-row-value" style={{ color: "var(--text)" }}>{d.visible} / {d.total}</span></div>
                  <div className="bar-track tone-cyan"><div className="bar-fill" style={{ width: `${(d.visible / Math.max(1, d.total)) * 100}%` }} /></div>
                </div>
              ))}
            </div>
            {result.sample_tables.length > 0 && (
              <details className="disclosure">
                <summary>{t("access.simSample", { n: result.sample_tables.length })}</summary>
                <div className="list-rows" style={{ marginTop: 6 }}>
                  {result.sample_tables.map((s) => (
                    <div key={`${s.database}.${s.table}`} className="list-row">
                      <span className="list-row-main mono" style={{ fontWeight: 500 }}>{s.database}.{s.table}</span>
                      <CellChip token={s.cell} size="sm" />
                    </div>
                  ))}
                </div>
              </details>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}

// ======================================================================= history
function History() {
  const { t } = useI18n();
  const [rows, setRows] = useState<AccessAuditRow[] | null>(null);
  useEffect(() => {
    api.accessAudit(300).then(setRows).catch(() => setRows([]));
  }, []);
  return (
    <Panel title={t("access.historyTitle")} subtitle={t("access.historySub")} bodyClass="panel-body flush">
      {!rows ? (
        <div className="empty">{t("common.loading")}</div>
      ) : rows.length === 0 ? (
        <div className="empty">{t("access.historyEmpty")}</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("security.colWhen")}</th>
                <th>{t("access.who")}</th>
                <th>{t("access.action")}</th>
                <th>{t("access.object")}</th>
                <th>{t("access.change")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="nowrap" title={fmtDate(r.created_at, "")}>{relTime(r.created_at, t)}</td>
                  <td className="strong">{r.actor ?? "—"}</td>
                  <td>{t(`access.actions.${r.action}`)}</td>
                  <td className="mono" style={{ fontSize: 11.5 }}>{r.target}</td>
                  <td className="mono" style={{ fontSize: 11.5 }}>
                    {r.old_value ?? ""}{r.old_value && r.new_value ? " → " : ""}{r.new_value ?? ""}
                    {typeof r.detail?.reason === "string" && r.detail.reason ? <span className="cell-sub">{r.detail.reason as string}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
