import { useEffect, useMemo, useState } from "react";
import { api, CoverageTargetOut, DirectoryPerson, Grant, LoginAuditOut, MatrixShape, RoleOut, UserOut } from "../api";
import { useAuth } from "../auth";
import { useI18n } from "../i18n";
import { fmtDate, initials, relTime } from "../lib/format";
import { CellChip, cellToken, parseCell, useLattice } from "../lib/lattice";
import Icon from "./Icon";
import { ConfirmDialog, MatrixEditor } from "./AccessBits";
import { Panel, Segmented } from "./ui";
import QuestionAudit from "./QuestionAudit";

type SecTab = "people" | "roles" | "knobs" | "questions" | "audit";

function grantsToSet(grants: Grant[]): Set<string> {
  return new Set(grants.map((g) => cellToken(g.level, g.category)));
}

function setToGrants(set: Set<string>): Grant[] {
  return Array.from(set)
    .map(parseCell)
    .filter((c): c is { level: number; category: string } => !!c)
    .sort((a, b) => a.level - b.level || a.category.localeCompare(b.category));
}

export function CellList({ grants, max = 8 }: { grants: Grant[]; max?: number }) {
  const shown = grants.slice(0, max);
  return (
    <span className="cells">
      {shown.map((g) => <CellChip key={cellToken(g.level, g.category)} level={g.level} category={g.category} size="sm" />)}
      {grants.length > max && <span className="chip neutral">+{grants.length - max}</span>}
    </span>
  );
}

// ======================================================================= people
function PersonEditor({
  user, roles, onClose, onSaved,
}: { user: UserOut | "new"; roles: RoleOut[]; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const { principal } = useAuth();
  const isNew = user === "new";
  const [username, setUsername] = useState(isNew ? "" : user.username);
  const [displayName, setDisplayName] = useState(isNew ? "" : user.display_name ?? "");
  const [active, setActive] = useState(isNew ? true : user.is_active);
  const [roleIds, setRoleIds] = useState<Set<string>>(new Set(isNew ? [] : user.role_ids ?? []));
  const [q, setQ] = useState("");
  const [dir, setDir] = useState<{ configured: boolean; results: DirectoryPerson[] } | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const isSelf = !isNew && user.id === principal?.user_id;

  useEffect(() => {
    if (!isNew) return;
    if (q.trim().length < 2) {
      if (dir === null) api.directory("").then(setDir).catch(() => setDir({ configured: false, results: [] }));
      return;
    }
    setSearching(true);
    const h = setTimeout(() => {
      api.directory(q.trim()).then(setDir).catch((e) => setError(e.message)).finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, isNew]);

  const cells = useMemo(() => {
    const out = new Map<string, Grant>();
    roles.filter((r) => roleIds.has(r.id)).forEach((r) => r.grants.forEach((g) => out.set(cellToken(g.level, g.category), g)));
    return Array.from(out.values()).sort((a, b) => a.level - b.level || a.category.localeCompare(b.category));
  }, [roles, roleIds]);
  const willBeAdmin = roles.some((r) => roleIds.has(r.id) && r.is_admin);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = { username, display_name: displayName || undefined, is_active: active, role_ids: Array.from(roleIds) };
      if (isNew) await api.createUser(body);
      else await api.updateUser(user.id, body);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  async function remove() {
    if (isNew) return;
    try {
      await api.deleteUser(user.id);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setConfirmRemove(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" style={{ maxWidth: 640 }} role="dialog" aria-label={isNew ? t("people.addTitle") : t("people.manageTitle")}>
        <div className="dialog-head">
          <h3>{isNew ? t("people.addTitle") : t("people.manageTitle", { name: user.display_name ?? user.username })}</h3>
          <button className="icon-btn sm" onClick={onClose} aria-label={t("common.close")}><Icon name="x" size={14} /></button>
        </div>
        <div className="dialog-body">
          {isNew && (
            <div style={{ marginBottom: 16 }}>
              <label className="stack-label">
                {t("people.searchDirectory")}
                <span className="search-field">
                  <Icon name="search" size={15} strokeWidth={1.9} />
                  <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("people.searchPlaceholder")} autoFocus />
                </span>
              </label>
              {dir && !dir.configured && <div className="muted small" style={{ marginTop: 8 }}>{t("people.directoryOff")}</div>}
              {dir?.configured && q.trim().length >= 2 && (
                <div className="results">
                  {searching && <div className="muted small">{t("common.loading")}</div>}
                  {!searching && dir.results.length === 0 && <div className="muted small">{t("people.noMatches")}</div>}
                  {dir.results.map((p) => (
                    <button key={p.username} type="button" disabled={p.registered}
                      className={`person-result${username === p.username ? " selected" : ""}`}
                      onClick={() => { setUsername(p.username); setDisplayName(p.display_name); }}>
                      <span className="avatar" style={{ width: 30, height: 30, fontSize: 10.5 }}>{initials(p.display_name)}</span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <b style={{ fontSize: 12.5 }}>{p.display_name}</b>
                        <span className="cell-sub">{[p.username, p.title, p.department, p.email].filter(Boolean).join(" · ")}</span>
                      </span>
                      {p.registered && <span className="chip neutral">{t("people.alreadyAdded")}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="field-grid">
            <label>
              {t("people.account")}
              <input type="text" value={username} disabled={!isNew} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label>
              {t("security.displayName")}
              <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </label>
          </div>
          <label className="toggle" style={{ marginTop: 12 }}>
            <input type="checkbox" checked={active} disabled={isSelf} onChange={(e) => setActive(e.target.checked)} /> {t("security.active")}
          </label>
          <div className="stack-label" style={{ marginTop: 16 }}>
            {t("people.roles")}
            {isSelf && <span className="banner warn" style={{ margin: 0 }}>{t("security.selfEscalationBanner")}</span>}
            <div className="role-list" style={{ gap: 8 }}>
              {roles.map((r) => (
                <label key={r.id} className="person-result" style={{ cursor: isSelf ? "not-allowed" : "pointer", opacity: isSelf ? 0.6 : 1 }}>
                  <input type="checkbox" disabled={isSelf} checked={roleIds.has(r.id)} onChange={() => {
                    const next = new Set(roleIds);
                    next.has(r.id) ? next.delete(r.id) : next.add(r.id);
                    setRoleIds(next);
                  }} />
                  <span style={{ flex: 1 }}>
                    <b style={{ fontSize: 12.5 }}>{r.name}</b>
                    {r.is_admin && <span className="chip warn-chip" style={{ marginInlineStart: 8 }}>{t("people.adminBadge")}</span>}
                    <span className="cell-sub">{r.description ?? ""}</span>
                  </span>
                  <CellList grants={r.grants} max={5} />
                </label>
              ))}
            </div>
          </div>
          <div className="kv-rows" style={{ marginTop: 12 }}>
            <div className="kv-row">
              <span className="k">{t("people.effectiveAccess")}</span>
              <span className="v">{willBeAdmin ? <span className="chip warn-chip">{t("people.adminAll")}</span> : cells.length ? <CellList grants={cells} max={12} /> : <span className="muted">{t("people.noCells")}</span>}</span>
            </div>
          </div>
          {error && <div className="banner error" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
        </div>
        <div className="dialog-foot">
          {!isNew && !isSelf && <button className="btn-ghost danger" style={{ marginInlineEnd: "auto" }} onClick={() => setConfirmRemove(true)}>{t("common.remove")}</button>}
          <button className="btn-ghost" onClick={onClose}>{t("common.cancel")}</button>
          <button className="btn-primary" onClick={save} disabled={busy || !username.trim() || (isNew && roleIds.size === 0)}>
            {busy ? t("common.saving") : isNew ? t("people.addButton") : t("common.save")}
          </button>
        </div>
      </div>
      {confirmRemove && !isNew && (
        <ConfirmDialog danger title={t("people.removeTitle")} body={t("security.removeUserConfirm", { name: user.username })}
          confirm={t("common.remove")} onCancel={() => setConfirmRemove(false)} onConfirm={remove} />
      )}
    </div>
  );
}

function PeoplePanel() {
  const { t } = useI18n();
  const [users, setUsers] = useState<UserOut[] | null>(null);
  const [roles, setRoles] = useState<RoleOut[]>([]);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<UserOut | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    api.users().then(setUsers).catch((e) => setError(e.message));
    api.roles().then(setRoles).catch(() => void 0);
  }
  useEffect(refresh, []);

  const shown = (users ?? []).filter((u) => {
    const s = q.trim().toLowerCase();
    return !s || `${u.username} ${u.display_name ?? ""} ${u.roles.join(" ")}`.toLowerCase().includes(s);
  });

  return (
    <>
      <Panel
        title={t("people.title")}
        subtitle={t("people.subtitle")}
        actions={
          <>
            <label className="search-field" style={{ width: 260 }}>
              <Icon name="search" size={15} strokeWidth={1.9} />
              <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("people.filterPlaceholder")} />
            </label>
            <button className="btn-primary btn-sm" onClick={() => setEditing("new")}>{t("people.addButton")}</button>
          </>
        }
        bodyClass="panel-body flush"
      >
        {error && <div className="banner error" style={{ margin: "0 20px 12px" }}>{error}</div>}
        {!users ? (
          <div className="empty">{t("common.loading")}</div>
        ) : users.length === 0 ? (
          <div className="empty">{t("security.noUsersYet", { script: "scripts/bootstrap_admin.py" })}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("security.colUser")}</th>
                  <th>{t("people.roles")}</th>
                  <th>{t("people.effectiveAccess")}</th>
                  <th>{t("security.colState")}</th>
                  <th>{t("security.colLastLogin")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {shown.map((u) => (
                  <tr key={u.id} className="clickable" onClick={() => setEditing(u)}>
                    <td className="strong">
                      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span className="avatar" style={{ width: 30, height: 30, fontSize: 10.5 }}>{initials(u.display_name ?? u.username)}</span>
                        <span>
                          {u.display_name ?? u.username}
                          <span className="cell-sub mono">{u.username}</span>
                        </span>
                      </span>
                    </td>
                    <td><span className="chips">{u.roles.length ? u.roles.map((r) => <span key={r} className="chip">{r}</span>) : <span className="muted">{t("common.none")}</span>}</span></td>
                    <td>{u.is_admin ? <span className="chip warn-chip">{t("people.adminAll")}</span> : u.cells?.length ? <CellList grants={u.cells} max={6} /> : <span className="muted">{t("people.noCells")}</span>}</td>
                    <td>{u.is_active ? <span className="badge ok">{t("security.active")}</span> : <span className="badge failed">{t("security.inactive")}</span>}</td>
                    <td title={fmtDate(u.last_login_at, t("common.never"))}>{u.last_login_at ? relTime(u.last_login_at, t) : t("common.never")}</td>
                    <td style={{ textAlign: "end" }}><button className="btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); setEditing(u); }}>{t("people.manage")}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {editing && <PersonEditor user={editing} roles={roles} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />}
    </>
  );
}

// ======================================================================== roles
function RolesMatrix() {
  const { t } = useI18n();
  const lat = useLattice();
  const [roles, setRoles] = useState<RoleOut[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | "new" | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [canViewSql, setCanViewSql] = useState(false);
  const [cells, setCells] = useState<Set<string>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  function load(selectAfter?: string) {
    api.roles().then((rs) => {
      setRoles(rs);
      const pick = selectAfter ?? (selectedId && selectedId !== "new" ? selectedId : rs[0]?.id);
      const r = rs.find((x) => x.id === pick);
      if (r) choose(r);
    }).catch((e) => setError(e.message));
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(), []);

  function choose(r: RoleOut | "new") {
    setError(null);
    setDirty(false);
    if (r === "new") {
      setSelectedId("new");
      setName("");
      setDescription("");
      setIsAdmin(false);
      setCanViewSql(false);
      setCells(new Set());
    } else {
      setSelectedId(r.id);
      setName(r.name);
      setDescription(r.description ?? "");
      setIsAdmin(r.is_admin);
      setCanViewSql(!!r.can_view_sql);
      setCells(grantsToSet(r.grants));
    }
  }

  const current = roles?.find((r) => r.id === selectedId);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = { name, description, is_admin: isAdmin, grants: setToGrants(cells), can_view_sql: canViewSql };
      const saved = selectedId === "new" ? await api.createRole(body) : await api.updateRole(selectedId as string, body);
      setFlash(t("roles.saved"));
      setTimeout(() => setFlash(null), 3000);
      load(saved.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!current) return;
    try {
      await api.deleteRole(current.id);
      setConfirmDelete(false);
      setSelectedId(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setConfirmDelete(false);
    }
  }

  return (
    <div className="cols wide-right">
      <Panel title={t("roles.title")} subtitle={t("roles.subtitle")}
        actions={<button className="btn-primary btn-sm" onClick={() => choose("new")}>{t("security.newRoleButton")}</button>}>
        {!roles ? <div className="empty">{t("common.loading")}</div> : (
          <div className="role-list">
            {roles.map((r) => (
              <button key={r.id} type="button" className={`role-card${selectedId === r.id ? " selected" : ""}`} onClick={() => choose(r)}
                style={selectedId === r.id ? { borderColor: "var(--primary)", background: "var(--primary-soft)" } : undefined}>
                <Icon name={r.is_admin ? "key" : "shield"} size={20} />
                <span className="role-card-main">
                  <span className="role-card-name">
                    {r.name}
                    {r.is_system && <span className="chip neutral">{t("roles.builtIn")}</span>}
                    {r.is_admin && <span className="chip warn-chip">{t("people.adminBadge")}</span>}
                  </span>
                  <span className="role-card-desc" style={{ display: "block" }}>{r.description ?? ""}</span>
                  <span style={{ display: "block", marginTop: 8 }}>
                    {r.grants.length ? <CellList grants={r.grants} max={6} /> : <span className="muted small">{r.is_admin ? t("people.adminAll") : t("security.noGrantCells")}</span>}
                  </span>
                </span>
                <span className="role-card-count">{t("security.usersCount", { n: r.user_count })}</span>
              </button>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title={selectedId === "new" ? t("security.newRole") : current ? t("roles.editTitle", { name: current.name }) : t("roles.pick")}
        subtitle={t("roles.matrixSub")}
        actions={selectedId && (
          <>
            {current && !current.is_system && <button className="btn-ghost btn-sm danger" onClick={() => setConfirmDelete(true)}>{t("common.delete")}</button>}
            <button className="btn-primary btn-sm" onClick={save} disabled={busy || !name.trim() || (!dirty && selectedId !== "new")}>
              {busy ? t("common.saving") : t("roles.saveChanges")}
            </button>
          </>
        )}
      >
        {!selectedId ? <div className="empty">{t("roles.pick")}</div> : (
          <>
            {flash && <div className="banner info">{flash}</div>}
            {error && <div className="banner error">{error}</div>}
            <div className="field-grid">
              <label>
                {t("security.name")}
                <input type="text" value={name} disabled={current?.is_system} onChange={(e) => { setName(e.target.value); setDirty(true); }} />
              </label>
              <label>
                {t("security.description")}
                <input type="text" value={description} onChange={(e) => { setDescription(e.target.value); setDirty(true); }} />
              </label>
            </div>
            <label className="toggle" style={{ marginTop: 12 }}>
              <input type="checkbox" checked={isAdmin} disabled={current?.is_system} onChange={(e) => { setIsAdmin(e.target.checked); setDirty(true); }} />
              {t("roles.adminToggle")}
            </label>
            {isAdmin && <div className="banner info" style={{ marginTop: 12 }}>{t("roles.adminNote")}</div>}
            <label className="toggle" style={{ marginTop: 10 }} title={t("roles.sqlHint")}>
              <input type="checkbox" checked={isAdmin || canViewSql} disabled={isAdmin}
                onChange={(e) => { setCanViewSql(e.target.checked); setDirty(true); }} />
              <span className="toggle-text">
                <span>{t("roles.sqlToggle")}</span>
                <span className="muted small">{t("roles.sqlHint")}</span>
              </span>
            </label>
            <div style={{ marginTop: 16 }}>
              <div className="toolbar" style={{ marginBottom: 8 }}>
                <b style={{ fontSize: 12.5 }}>{t("roles.cellsHeading")}</b>
                <span className="muted small">{t("roles.cellsHint")}</span>
                <span className="grow" />
                <span className="muted small">{t("roles.selectedCells", { n: cells.size })}</span>
              </div>
              {lat.ready ? <MatrixEditor value={cells} onChange={(next) => { setCells(next); setDirty(true); }} /> : <div className="empty">{t("common.loading")}</div>}
              <div className="legend">
                <span className="legend-item"><span className="matrix-cell on" style={{ width: 18, height: 14, ["--cell" as string]: "var(--primary)" }} /> {t("roles.legendAllow")}</span>
                <span className="legend-item"><span className="matrix-cell" style={{ width: 18, height: 14 }} /> {t("roles.legendDeny")}</span>
                <span className="legend-item"><span className="matrix-cell" style={{ width: 18, height: 14, background: "repeating-linear-gradient(135deg, var(--surface-2) 0 4px, var(--border-soft) 4px 8px)" }} /> {t("roles.legendReserved")}</span>
              </div>
            </div>
          </>
        )}
      </Panel>
      {confirmDelete && current && (
        <ConfirmDialog danger title={t("roles.deleteTitle")} body={t("security.deleteRoleConfirm", { name: current.name })}
          confirm={t("common.delete")} onCancel={() => setConfirmDelete(false)} onConfirm={remove} />
      )}
    </div>
  );
}

function AuditPanel() {
  const { t } = useI18n();
  const [rows, setRows] = useState<LoginAuditOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.loginAudit(200).then(setRows).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="banner error">{error}</div>;

  // Signals over the last 24h of the rows actually returned.
  const dayAgo = Date.now() - 24 * 3600 * 1000;
  const recent = (rows ?? []).filter((r) => r.created_at && new Date(r.created_at).getTime() >= dayAgo);
  const ok24 = recent.filter((r) => r.success).length;
  const fail24 = recent.length - ok24;

  return (
    <>
      <Panel headerOnly title={t("security.loginAuditHeading")} subtitle={t("security.auditSub")} />
      <div className="cols wide-left">
        <div className="panel">
          {!rows ? (
            <div className="empty">{t("common.loading")}</div>
          ) : rows.length === 0 ? (
            <div className="empty">{t("security.noLoginAttempts")}</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("security.colWhen")}</th>
                    <th>{t("security.colUsernameAttempted")}</th>
                    <th>{t("security.colResult")}</th>
                    <th>{t("security.colReason")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td className="nowrap">{r.created_at ? new Date(r.created_at).toLocaleString() : t("common.dash")}</td>
                      <td className="strong mono">{r.username_attempted}</td>
                      <td>
                        <span className={`badge ${r.success ? "success" : "failed"}`}>{r.success ? t("security.success") : t("security.failed")}</span>
                      </td>
                      <td className="muted">{r.reason ?? t("common.dash")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <Panel title={t("security.signalsTitle")} subtitle={t("security.signalsSub")}>
          <div className="kv-rows">
            <div className="kv-row">
              <span className="k">{t("security.successfulLogins")}</span>
              <span className="v good" style={{ fontSize: 15 }}>{ok24}</span>
            </div>
            <div className="kv-row">
              <span className="k">{t("security.failedLogins")}</span>
              <span className={`v ${fail24 ? "warn" : "good"}`} style={{ fontSize: 15 }}>{fail24}</span>
            </div>
            <div className="kv-row">
              <span className="k">{t("security.recordsShown")}</span>
              <span className="v info" style={{ fontSize: 15 }}>{rows?.length ?? 0}</span>
            </div>
          </div>
        </Panel>
      </div>
    </>
  );
}

function TrustLevelKnob() {
  const { t } = useI18n();
  const [level, setLevel] = useState<number | null>(null);
  const [minConfidence, setMinConfidence] = useState<number | null>(null);
  const [trustedThreshold, setTrustedThreshold] = useState<number | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function apply(r: { level: number; min_confidence: number; trusted_threshold: number; source: string }) {
    setLevel(r.level);
    setMinConfidence(r.min_confidence);
    setTrustedThreshold(r.trusted_threshold);
    setSource(r.source);
  }

  useEffect(() => {
    api.trustLevel().then(apply).catch((e) => setError(e.message));
  }, []);

  async function commit(next: number) {
    setSaving(true);
    setError(null);
    try {
      apply(await api.setTrustLevel(next));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="knob">
      <div className="knob-head">
        <span className="knob-title">{t("security.trustHeading")}</span>
        {level !== null && <span className="knob-value">{level}/10</span>}
      </div>
      <div className="knob-desc">{t("security.trustIntro")}</div>
      {error && <div className="banner error">{error}</div>}
      {level === null ? (
        !error && <div className="muted small">{t("common.loading")}</div>
      ) : (
        <>
          <div className="slider-row">
            <input type="range" min={0} max={10} step={1} value={level} disabled={saving} onChange={(e) => commit(Number(e.target.value))} aria-label={t("security.trustHeading")} />
          </div>
          <div className="knob-note">
            {t("security.trustEffect", { level, confidence: minConfidence?.toFixed(2) ?? "?", threshold: trustedThreshold?.toFixed(2) ?? "?" })}
          </div>
          <div className="knob-note faint">{source === "override" ? t("security.trustSourceOverride") : t("security.trustSourceDefault")}</div>
        </>
      )}
    </div>
  );
}

function CoverageDepthKnob() {
  const { t } = useI18n();
  const [data, setData] = useState<CoverageTargetOut | null>(null);
  const [draft, setDraft] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .coverageTarget()
      .then((r) => {
        setData(r);
        setDraft(r.target_pct);
      })
      .catch((e) => setError(e.message));
  }, []);

  // Commit on release rather than on every drag tick — a 0-100 slider
  // fires dozens of change events per drag, each would be a PUT.
  async function commit(next: number) {
    setSaving(true);
    setError(null);
    try {
      const r = await api.setCoverageTarget(next);
      setData(r);
      setDraft(r.target_pct);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="knob">
      <div className="knob-head">
        <span className="knob-title">{t("security.coverageHeading")}</span>
        {draft !== null && <span className="knob-value">{Math.round(draft)}%</span>}
      </div>
      <div className="knob-desc">{t("security.coverageIntro")}</div>
      {error && <div className="banner error">{error}</div>}
      {data === null || draft === null ? (
        !error && <div className="muted small">{t("common.loading")}</div>
      ) : (
        <>
          <div className="slider-row">
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={draft}
              disabled={saving}
              aria-label={t("security.coverageHeading")}
              onChange={(e) => setDraft(Number(e.target.value))}
              onMouseUp={() => draft !== data.target_pct && commit(draft)}
              onTouchEnd={() => draft !== data.target_pct && commit(draft)}
              onKeyUp={() => draft !== data.target_pct && commit(draft)}
            />
          </div>
          <div className="knob-note">
            {t("security.coverageEffect", { pct: Math.round(draft), min: data.min_deep_objects_per_source, max: data.max_deep_objects_per_source })}
          </div>
          <div className="knob-note faint">{data.source === "override" ? t("security.coverageSourceOverride") : t("security.coverageSourceDefault")}</div>
        </>
      )}
    </div>
  );
}


export default function Security() {
  const { t } = useI18n();
  const { principal, authEnabled } = useAuth();
  const [tab, setTab] = useState<SecTab>("people");
  const [matrix, setMatrix] = useState<MatrixShape | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.matrix().then(setMatrix).catch((e) => setError(e.message));
  }, []);

  if (!principal?.is_admin) {
    return <div className="banner warn">{t("security.adminOnly")}</div>;
  }
  if (error) return <div className="banner error">{error}</div>;
  if (!matrix) return <div className="empty">{t("common.loading")}</div>;

  return (
    <div>
      <Panel
        headerOnly
        title={t("security.headerTitle")}
        subtitle={t("security.headerSub")}
        actions={
          <Segmented
            value={tab}
            onChange={setTab}
            options={[
              { value: "people", label: t("security.tabUsers"), icon: "users" },
              { value: "roles", label: t("security.tabRoles"), icon: "shield" },
              { value: "knobs", label: t("security.tabKnobs"), icon: "gear" },
              { value: "questions", label: t("security.tabQuestions"), icon: "sparkle" },
              { value: "audit", label: t("security.tabAudit"), icon: "key" },
            ]}
          />
        }
      />
      {!authEnabled && (
        <div className="banner warn">
          {t("security.authDisabledBanner", { cfg: "catalog.auth.enabled: false", cls: "catalog.classification", script: "scripts/bootstrap_admin.py" })}
        </div>
      )}
      {tab === "people" && <PeoplePanel />}
      {tab === "roles" && <RolesMatrix />}
      {tab === "knobs" && (
        <Panel title={t("security.knobsTitle")} subtitle={t("security.knobsSub")}>
          <TrustLevelKnob />
          <CoverageDepthKnob />
        </Panel>
      )}
      {tab === "questions" && <QuestionAudit />}
      {tab === "audit" && <AuditPanel />}
    </div>
  );
}
