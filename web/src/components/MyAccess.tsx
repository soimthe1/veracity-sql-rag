import { useEffect, useRef, useState } from "react";
import { api, ScopeOut } from "../api";
import { useAuth } from "../auth";
import { useI18n } from "../i18n";
import { initials } from "../lib/format";
import { CellChip, cellToken, useLattice } from "../lib/lattice";
import Icon from "./Icon";

/**
 * Top-bar user chip: who you are, your roles and your cells (e.g. 10A 20E,
 * colour-coded by level) — click for "My access": every cell with its
 * label, and how much of the catalog that lets you query.
 */
export default function MyAccess({ showUser, onSignOut }: { showUser: boolean; onSignOut: () => void }) {
  const { t } = useI18n();
  const lat = useLattice();
  const { principal, authEnabled } = useAuth();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<ScopeOut | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    api.scope().then(setScope).catch(() => setScope(null));
    const onDoc = (e: MouseEvent) => wrap.current && !wrap.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const grants = [...(principal?.grants ?? [])].sort((a, b) => a.level - b.level || a.category.localeCompare(b.category));
  const full = !!principal?.full_access || !authEnabled;
  const name = showUser ? principal?.display_name ?? principal?.username ?? "" : t("myAccess.openAccess");
  const role = !authEnabled
    ? t("myAccess.authOff")
    : principal?.is_admin
      ? t("topbar.administrator")
      : (principal?.roles ?? []).join(", ") || t("topbar.standardAccess");

  return (
    <div className="user-chip" ref={wrap}>
      <button type="button" className="user-chip-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-haspopup="dialog"
        title={t("myAccess.title")}>
        <span className="avatar">{showUser ? initials(name) : <Icon name="key" size={15} />}</span>
        <span className="user-chip-text">
          <span className="user-chip-name" style={{ display: "block" }}>{name}</span>
          <span className="user-chip-role" style={{ display: "block" }}>{role}</span>
          {!full && grants.length > 0 && (
            <span className="topbar-cells">
              {grants.slice(0, 3).map((g) => <CellChip key={cellToken(g.level, g.category)} level={g.level} category={g.category} size="sm" />)}
              {grants.length > 3 && <span className="chip neutral" style={{ height: 18, padding: "0 5px", fontSize: 10 }}>+{grants.length - 3}</span>}
            </span>
          )}
        </span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label={t("myAccess.title")}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
            <span className="avatar" style={{ width: 40, height: 40 }}>{showUser ? initials(name) : <Icon name="key" size={17} />}</span>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 700 }}>{name}</div>
              <div className="muted small mono">{showUser ? principal?.username : ""}</div>
            </div>
          </div>

          {!authEnabled ? (
            <div className="banner warn" style={{ marginBottom: 12 }}>{t("myAccess.authOffNote")}</div>
          ) : full ? (
            <div className="banner info" style={{ marginBottom: 12 }}>{t("myAccess.adminNote")}</div>
          ) : null}

          {authEnabled && (
            <div className="kv-rows">
              <div className="kv-row">
                <span className="k">{t("myAccess.roles")}</span>
                <span className="v chips" style={{ justifyContent: "flex-end" }}>
                  {(principal?.roles ?? []).length ? principal!.roles!.map((r) => <span key={r} className="chip">{r}</span>) : <span className="muted">{t("common.none")}</span>}
                </span>
              </div>
            </div>
          )}

          {authEnabled && grants.length > 0 && (
            <>
              <div className="suggest-label" style={{ margin: "12px 0 8px" }}>{t("myAccess.cells")}</div>
              <div className="list-rows">
                {grants.map((g) => (
                  <div key={cellToken(g.level, g.category)} className="list-row" style={{ padding: "7px 0" }}>
                    <CellChip level={g.level} category={g.category} size="sm" />
                    <span className="list-row-main" style={{ fontWeight: 500, fontSize: 12 }}>
                      {lat.cellLabel(g.level, g.category) ?? `${lat.levelName(g.level)} · ${lat.categoryName(g.category)}`}
                      {lat.cellLabel(g.level, g.category) && <span className="list-row-sub">{lat.levelName(g.level)} · {lat.categoryName(g.category)}</span>}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
          {authEnabled && !full && grants.length === 0 && <div className="muted small" style={{ marginTop: 10 }}>{t("myAccess.noCells")}</div>}

          {scope && (
            <div className="kv-rows" style={{ marginTop: 10 }}>
              <div className="kv-row">
                <span className="k">{t("myAccess.canQuery")}</span>
                <span className="v info">
                  {scope.tables_total == null
                    ? t("myAccess.tablesN", { n: scope.tables_visible })
                    : t("myAccess.tablesOf", { n: scope.tables_visible, total: scope.tables_total })}
                </span>
              </div>
              <div className="kv-row">
                <span className="k">{t("myAccess.databases")}</span>
                <span className="v">{scope.databases_total == null ? scope.databases_visible : `${scope.databases_visible} / ${scope.databases_total}`}</span>
              </div>
              {scope.columns_hidden > 0 && (
                <div className="kv-row">
                  <span className="k">{t("myAccess.columnsHidden")}</span>
                  <span className="v warn">{scope.columns_hidden}</span>
                </div>
              )}
            </div>
          )}

          {showUser && (
            <button className="btn-ghost btn-sm" style={{ marginTop: 14, width: "100%" }} onClick={onSignOut}>
              <Icon name="logout" size={14} className="flip-rtl" /> {t("topbar.signOut")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
