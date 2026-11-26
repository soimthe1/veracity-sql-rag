/**
 * Session state for the web UI's own login + (level, category)
 * permission matrix (see shared/auth/, api/routers/auth.py,
 * docs/integration.md). Deliberately
 * simple: a React context holding the current principal + bearer
 * token, backed by sessionStorage so a page refresh doesn't force a
 * re-login mid-session but a closed tab does (session, not persistent,
 * storage — matches the short-session posture of an internal admin
 * tool more than a "remember me for 30 days" consumer app).
 *
 * The token travels as an `Authorization: Bearer` header (see api.ts),
 * not solely the httponly cookie the API also sets — this sidesteps
 * cross-origin cookie/CORS wrinkles entirely in dev (web on :5180,
 * api on :8100) and works identically in prod behind a same-origin
 * reverse proxy.
 */
import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, Principal } from "./api";

const STORAGE_KEY = "ai_catalog_token";

interface AuthState {
  authEnabled: boolean | null; // null while still loading catalog.auth.enabled
  principal: Principal | null;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  setup: (body: { code: string; username: string; password: string; display_name?: string }) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = sessionStorage.getItem(STORAGE_KEY);
    if (token) api.setToken(token);

    api
      .me()
      .then((p) => {
        setAuthEnabled(p.auth_enabled);
        if (p.auth_enabled && p.username) {
          setPrincipal(p as Principal);
        } else if (!p.auth_enabled) {
          // Auth disabled — /v1/auth/me returns the unrestricted
          // principal; treat the UI as "signed in" with nothing to log
          // in as, per the project's "no login page, go straight in".
          setPrincipal(p as Principal);
        }
      })
      .catch((e) => {
        // A 401 here specifically means auth IS enabled server-side but
        // this session has no valid token — that's the one case that
        // must still show the login page, so authEnabled is set
        // explicitly rather than left unknown. Any OTHER failure (API
        // unreachable, 5xx) leaves authEnabled null/unknown: falling
        // through to the app rather than a login wall we can't actually
        // verify, matching the "never a hard dependency" posture —
        // status polling elsewhere already surfaces "can't reach API".
        if (e instanceof ApiError && e.status === 401) {
          setAuthEnabled(true);
        }
        api.setToken(null);
        sessionStorage.removeItem(STORAGE_KEY);
      })
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      authEnabled,
      principal,
      loading,
      error,
      login: async (username: string, password: string) => {
        setError(null);
        try {
          const res = await api.login(username, password);
          api.setToken(res.token);
          sessionStorage.setItem(STORAGE_KEY, res.token);
          setPrincipal(res.principal);
          setAuthEnabled(res.principal.auth_enabled);
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          throw e;
        }
      },
      setup: async (body) => {
        setError(null);
        try {
          const res = await api.setup(body);
          api.setToken(res.token);
          sessionStorage.setItem(STORAGE_KEY, res.token);
          setPrincipal(res.principal);
          setAuthEnabled(res.principal.auth_enabled);
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          throw e;
        }
      },
      logout: () => {
        api.setToken(null);
        sessionStorage.removeItem(STORAGE_KEY);
        setPrincipal(null);
        api.logout().catch(() => void 0);
      },
    }),
    [authEnabled, principal, loading, error],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
