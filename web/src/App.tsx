import { useEffect, useState } from "react";
import { api, EntitiesFilter, StatusOut } from "./api";
import { usePolling } from "./hooks";
import { useAuth } from "./auth";
import { useTheme } from "./theme";
import { useI18n } from "./i18n";
import { relTime } from "./lib/format";
import Icon from "./components/Icon";
import GlobalSearch from "./components/GlobalSearch";
import Overview from "./components/Overview";
import Sources from "./components/Sources";
import Entities from "./components/Entities";
import Relationships from "./components/Relationships";
import Glossary from "./components/Glossary";
import Integrity from "./components/Integrity";
import Ask from "./components/Ask";
import Suggestions from "./components/Suggestions";
import Security from "./components/Security";
import DataAccess from "./components/DataAccess";
import MyAccess from "./components/MyAccess";
import Health from "./components/Health";
import Login from "./components/Login";
import ErrorBoundary from "./components/ErrorBoundary";
import { BrandMark } from "./lib/brand";

/** Every screen, grouped as the rail shows it. `screen` is the key the API
 * lists in principal.screens (catalog.access.non_admin_screens). */
const NAV = [
  { id: "Ask", icon: "sparkle", screen: "ask", group: "workspace" },
  { id: "Overview", icon: "overview", screen: "overview", group: "catalog" },
  { id: "Sources", icon: "database", screen: "sources", group: "catalog" },
  { id: "Entities", icon: "table", screen: "entities", group: "catalog" },
  { id: "Relationships", icon: "network", screen: "relationships", group: "catalog" },
  { id: "Glossary", icon: "book", screen: "glossary", group: "catalog" },
  { id: "Suggestions", icon: "bulb", screen: "suggestions", group: "catalog" },
  { id: "Integrity", icon: "shield", screen: "integrity", group: "catalog" },
  { id: "Health", icon: "activity", screen: "health", group: "catalog" },
  { id: "Access", icon: "key", screen: "access", group: "admin" },
  { id: "Security", icon: "lock", screen: "security", group: "admin" },
] as const;

const GROUPS = ["workspace", "catalog", "admin"] as const;

/** Screens that are drawn from the live catalog status. */
const STATUS_SCREENS = new Set(["overview", "integrity", "health", "sources"]);

type Tab = (typeof NAV)[number]["id"];

const STATUS_POLL_MS = 10_000;

/** A one-shot "open this entity" request; `n` makes a repeat click on the
 * same entity still count as a new request. */
interface OpenRequest {
  id: string;
  n: number;
}

function SystemCard({ status }: { status: StatusOut | null }) {
  const { t } = useI18n();
  if (!status) return null;
  const all = status.entities_total;
  const deepShare = all ? (status.deep_analysis_entities / all) * 100 : 0;
  const ok = status.enabled && status.catalog_db_connected;
  return (
    <div className="rail-system">
      <div className="rail-system-title">
        <span className={`dot ${ok ? "on" : status.enabled ? "off" : "warn"}`} />
        {!status.enabled ? t("rail.catalogDisabled") : status.catalog_db_connected ? t("rail.catalogConnected") : t("rail.catalogUnreachable")}
      </div>
      <div className="rail-system-row">
        <span>{t("rail.lastSweep")}</span>
        <b>{relTime(status.last_discovery_at, t)}</b>
      </div>
      <div className="rail-system-row">
        <span>{t("rail.lastValidation")}</span>
        <b>{relTime(status.last_validation_at, t)}</b>
      </div>
      <div className="rail-system-row">
        <span>{t("rail.deepCoverage")}</span>
        <b className={deepShare >= status.coverage_deep_analysis_target_pct ? "good" : "warn"}>{deepShare.toFixed(1)}%</b>
      </div>
    </div>
  );
}

function AppShell() {
  const { principal, logout, authEnabled } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { t, toggleLang } = useI18n();

  // What this person may open. Older API builds don't send `screens`, so
  // fall back to "admins see everything, others everything but admin".
  const allowed = new Set<string>(
    principal?.screens ??
      NAV.filter((n) => principal?.is_admin || !authEnabled || n.group !== "admin").map((n) => n.screen),
  );
  const nav = NAV.filter((n) => allowed.has(n.screen));
  // Someone who only has workspace screens (Ask, later Search / Create)
  // gets a focused layout: no rail, just the workspace under a top bar.
  const focused = nav.every((n) => n.group === "workspace");
  const canStatus = nav.some((n) => STATUS_SCREENS.has(n.screen));
  const canBrowse = allowed.has("entities");

  // Ask is the home screen for everyone.
  const [tab, setTab] = useState<Tab>(() => (nav.some((n) => n.id === "Ask") ? "Ask" : nav[0]?.id ?? "Ask"));
  const [entitiesFilter, setEntitiesFilter] = useState<EntitiesFilter | null>(null);
  const [entityRequest, setEntityRequest] = useState<OpenRequest | null>(null);
  const [relationshipsEntityId, setRelationshipsEntityId] = useState<string | null>(null);
  const { data: status, error } = usePolling(
    () => (canStatus ? api.status() : Promise.resolve(null)),
    STATUS_POLL_MS,
    [canStatus],
  );

  // A one-shot navigation filter/selection from elsewhere only applies
  // to the mount it was intended for — the moment the user leaves that
  // tab (by any route, including the rail), it's cleared so a later,
  // unrelated visit never silently re-applies it.
  useEffect(() => {
    if (tab !== "Entities" && entitiesFilter) setEntitiesFilter(null);
    if (tab !== "Entities" && entityRequest) setEntityRequest(null);
    if (tab !== "Relationships" && relationshipsEntityId) setRelationshipsEntityId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // Browser tab title follows the open page: "Ask – Veracity SQL".
  useEffect(() => {
    document.title = `${t(`nav.${tab}`)} – ${t("app.brand")}`;
  }, [tab, t]);

  // If what's allowed changes under us (e.g. an admin loses the role),
  // never leave a screen open that is no longer allowed.
  useEffect(() => {
    if (!nav.some((n) => n.id === tab) && nav[0]) setTab(nav[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.map((n) => n.id).join(",")]);

  function goToEntities(filter: EntitiesFilter) {
    setEntityRequest(null);
    setEntitiesFilter(filter);
    setTab("Entities");
  }

  function openEntity(id: string) {
    setEntityRequest({ id, n: Date.now() });
    setTab("Entities");
  }

  function goToRelationships(entityId?: string) {
    setRelationshipsEntityId(entityId ?? null);
    setTab("Relationships");
  }

  const showUser = authEnabled && principal?.username;
  const needsStatus = tab === "Overview" || tab === "Integrity" || tab === "Health";
  const workspaceTabs = nav.filter((n) => n.group === "workspace");

  const brandBlock = (
    <div className="rail-brand">
      <BrandMark className="rail-logo" size={32} />
      <div className="rail-brand-text">
        <div className="rail-brand-name">{t("app.brand")}</div>
        <div className="rail-brand-sub">{t("app.tagline")}</div>
      </div>
    </div>
  );

  return (
    <div className={`app-shell${focused ? " focused" : ""}`}>
      {!focused && (
        <aside className="rail">
          {brandBlock}
          <nav className="rail-nav">
            {GROUPS.map((g) => {
              const items = nav.filter((n) => n.group === g);
              if (!items.length) return null;
              return (
                <div key={g} className="rail-group">
                  <div className="rail-group-label">{t(`rail.group_${g}`)}</div>
                  {items.map(({ id, icon }) => (
                    <button key={id} className={id === tab ? "active" : ""} onClick={() => setTab(id)} title={t(`nav.${id}`)}>
                      <Icon name={icon} size={18} strokeWidth={1.7} />
                      <span>{t(`nav.${id}`)}</span>
                    </button>
                  ))}
                </div>
              );
            })}
          </nav>

          {canStatus && status && (
            <>
              <div className="rail-section">{t("rail.system")}</div>
              <SystemCard status={status} />
            </>
          )}
          <div className="rail-spacer" />
        </aside>
      )}

      <div className="main-col">
        <header className="topbar">
          {focused ? (
            <div className="topbar-brand">{brandBlock}</div>
          ) : (
            <div className="topbar-title">
              <h1>{t(`pages.${tab}.title`)}</h1>
              <div className="sub">{t(`pages.${tab}.subtitle`)}</div>
            </div>
          )}
          {focused && workspaceTabs.length > 1 && (
            <div className="segmented topbar-switch" role="tablist">
              {workspaceTabs.map(({ id, icon }) => (
                <button key={id} role="tab" aria-selected={id === tab} className={id === tab ? "active" : ""} onClick={() => setTab(id)}>
                  <Icon name={icon} size={15} /> {t(`nav.${id}`)}
                </button>
              ))}
            </div>
          )}
          {canBrowse && <GlobalSearch onOpenEntity={openEntity} />}
          <button className="icon-btn" onClick={toggleLang} title={t("topbar.languageToggle")} aria-label={t("topbar.languageToggle")}>
            <Icon name="globe" size={17} />
          </button>
          <button
            className="icon-btn"
            onClick={toggleTheme}
            title={t("topbar.themeToggle", { mode: theme === "dark" ? t("topbar.light") : t("topbar.dark") })}
            aria-label={t("topbar.themeToggle", { mode: theme === "dark" ? t("topbar.light") : t("topbar.dark") })}
          >
            <Icon name="moon" size={17} />
          </button>
          <MyAccess showUser={!!showUser} onSignOut={logout} />
        </header>

        <main className="content">
          {needsStatus && error && !status && <div className="banner error">{error}</div>}
          {needsStatus && !status && !error && <div className="empty">{t("app.loading")}</div>}
          {tab === "Overview" && status && (
            <ErrorBoundary tabName={t("nav.Overview")}>
              <Overview
                status={status}
                onGoToEntities={goToEntities}
                onGoToSources={() => setTab("Sources")}
                onGoToRelationships={() => goToRelationships()}
              />
            </ErrorBoundary>
          )}
          {tab === "Sources" && (
            <ErrorBoundary tabName={t("nav.Sources")}>
              <Sources onGoToEntities={goToEntities} />
            </ErrorBoundary>
          )}
          {tab === "Entities" && (
            <ErrorBoundary tabName={t("nav.Entities")}>
              <Entities initialFilter={entitiesFilter} openRequest={entityRequest} onViewRelationships={goToRelationships} />
            </ErrorBoundary>
          )}
          {tab === "Relationships" && (
            <ErrorBoundary tabName={t("nav.Relationships")}>
              <Relationships initialEntityId={relationshipsEntityId} onOpenEntity={openEntity} />
            </ErrorBoundary>
          )}
          {tab === "Glossary" && (
            <ErrorBoundary tabName={t("nav.Glossary")}>
              <Glossary onGoToEntities={goToEntities} onOpenEntity={openEntity} />
            </ErrorBoundary>
          )}
          {tab === "Ask" && (
            <ErrorBoundary tabName={t("nav.Ask")}>
              <Ask onOpenEntity={canBrowse ? openEntity : undefined} status={canStatus ? status : null}
                onOpenHealth={allowed.has("health") ? () => setTab("Health") : undefined} />
            </ErrorBoundary>
          )}
          {tab === "Suggestions" && (
            <ErrorBoundary tabName={t("nav.Suggestions")}>
              <Suggestions onOpenEntity={openEntity} />
            </ErrorBoundary>
          )}
          {tab === "Integrity" && status && (
            <ErrorBoundary tabName={t("nav.Integrity")}>
              <Integrity status={status} onGoToEntities={goToEntities} />
            </ErrorBoundary>
          )}
          {tab === "Access" && (
            <ErrorBoundary tabName={t("nav.Access")}>
              <DataAccess onOpenEntity={openEntity} />
            </ErrorBoundary>
          )}
          {tab === "Security" && (
            <ErrorBoundary tabName={t("nav.Security")}>
              <Security />
            </ErrorBoundary>
          )}
          {tab === "Health" && status && (
            <ErrorBoundary tabName={t("nav.Health")}>
              <Health status={status} />
            </ErrorBoundary>
          )}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const { authEnabled, principal, loading } = useAuth();
  const { t } = useI18n();

  if (loading) {
    return (
      <div className="login-screen">
        <div className="empty">{t("app.loading")}</div>
      </div>
    );
  }

  // authEnabled === true and no principal resolved yet -> show the login
  // page. authEnabled === false, or a principal IS resolved, -> straight
  // into the app, per the project's explicit "no login page, take
  // directly to the system" requirement when auth is disabled.
  if (authEnabled && !principal) {
    return <Login />;
  }

  return <AppShell />;
}
