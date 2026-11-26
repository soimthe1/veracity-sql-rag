import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useI18n } from "../i18n";
import { BrandMark } from "../lib/brand";

/**
 * Sign-in screen. With local sign-in and no administrator yet it shows the
 * one-time "create the first administrator" form instead, unlocked by the
 * setup code the API prints to its log.
 */
export default function Login() {
  const { login, setup, error } = useAuth();
  const { t } = useI18n();
  const [mode, setMode] = useState<string>("local");
  const [needsSetup, setNeedsSetup] = useState(false);
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.setupStatus().then((s) => { setMode(s.mode); setNeedsSetup(s.needed); }).catch(() => void 0);
  }, []);

  useEffect(() => {
    document.title = `${needsSetup ? t("setup.subtitle") : t("login.signIn")} – ${t("app.brand")}`;
  }, [needsSetup, t]);

  const mismatch = needsSetup && confirm.length > 0 && confirm !== password;
  const ready = needsSetup ? code && username && password && password === confirm : username && password;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    try {
      if (needsSetup) await setup({ code, username, password, display_name: displayName || undefined });
      else await login(username, password);
    } catch {
      /* error already captured in auth state */
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <BrandMark className="login-mark" size={42} />
          <div>
            <div className="login-title">{t("app.brand")}</div>
            <div className="login-subtitle">{needsSetup ? t("setup.subtitle") : t("login.subtitle")}</div>
          </div>
        </div>

        <form onSubmit={onSubmit}>
          {needsSetup && (
            <>
              <div className="banner info" style={{ marginBottom: 12 }}>{t("setup.intro")}</div>
              <label className="login-label" htmlFor="setup-code">{t("setup.code")}</label>
              <input id="setup-code" type="text" autoComplete="off" placeholder="XXXX-XXXX-XXXX"
                value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
            </>
          )}

          <label className="login-label" htmlFor="username">{t("login.username")}</label>
          <input
            id="username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus={!needsSetup}
          />

          {needsSetup && (
            <>
              <label className="login-label" htmlFor="display-name">{t("security.displayName")}</label>
              <input id="display-name" type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </>
          )}

          <label className="login-label" htmlFor="password">{t("login.password")}</label>
          <input
            id="password"
            type="password"
            autoComplete={needsSetup ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          {needsSetup && (
            <>
              <label className="login-label" htmlFor="confirm">{t("setup.confirm")}</label>
              <input id="confirm" type="password" autoComplete="new-password" value={confirm}
                onChange={(e) => setConfirm(e.target.value)} />
              {mismatch && <div className="banner error login-error">{t("setup.mismatch")}</div>}
            </>
          )}

          {error && <div className="banner error login-error">{error}</div>}

          <button type="submit" className="btn-primary login-submit" disabled={submitting || !ready}>
            {submitting ? t("login.signingIn") : needsSetup ? t("setup.create") : t("login.signIn")}
          </button>
        </form>

        <div className="login-footnote">
          {needsSetup ? t("setup.footnote") : mode === "ldap" ? t("login.footnote") : t("login.footnoteLocal")}
        </div>
      </div>
    </div>
  );
}
