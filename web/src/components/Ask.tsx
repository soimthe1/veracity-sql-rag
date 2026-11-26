import { useEffect, useMemo, useState } from "react";
import { answerApi, AskResult, Grant, MatrixShape, RecentQuestion, ScopeOut, StatusOut, SuggestedQuestion } from "../api";
import { api } from "../api";
import { useAuth } from "../auth";
import { useI18n } from "../i18n";
import Icon from "./Icon";
import AskAnswer, { DetailTab } from "./ask/AskAnswer";
import AskHome from "./ask/AskHome";
import HistoryDrawer from "./ask/HistoryDrawer";

/**
 * Ask — the app's home and its one pipeline: question -> catalog -> SQL ->
 * results -> narration (POST /v1/ask on the answer/ process), plus Create
 * (email / Word / PowerPoint / PDF) on every answer.
 *
 * Signed in: the answer service resolves the person's cells from their
 * session. Sign-in off (testing): the grant picker below simulates a caller.
 *
 * `result.notes` and the raw rows come straight from the answer/ backend
 * and are not localized — only this component's chrome is.
 */
export default function Ask({ onOpenEntity, status, onOpenHealth }: {
  onOpenEntity?: (id: string) => void;
  /** live catalog status — passed for administrators only */
  status?: StatusOut | null;
  onOpenHealth?: () => void;
}) {
  const { principal, authEnabled } = useAuth();
  const { t } = useI18n();
  const [matrix, setMatrix] = useState<MatrixShape | null>(null);
  const [manualGrants, setManualGrants] = useState<Set<string>>(new Set());
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [askedBefore, setAskedBefore] = useState<Set<string>>(() => new Set());
  const [followUp, setFollowUp] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answerUp, setAnswerUp] = useState<boolean | null>(null);
  const [requireGrants, setRequireGrants] = useState<boolean>(true);
  const [showInfo, setShowInfo] = useState(false);
  const [suggested, setSuggested] = useState<SuggestedQuestion[]>([]);
  const [recent, setRecent] = useState<RecentQuestion[]>([]);
  const [scope, setScope] = useState<ScopeOut | null>(null);
  const [database, setDatabase] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  // The answer's detail tabs start collapsed; whichever one the person
  // opens stays open for their next answers.
  const [detailTab, setDetailTab] = useState<DetailTab | null>(null);

  function refreshRecent() {
    answerApi.recent(6).then(setRecent).catch(() => void 0);
  }

  useEffect(() => {
    api.matrix().then(setMatrix).catch(() => void 0);
    answerApi
      .health()
      .then((h) => {
        setAnswerUp(h.answer_enabled);
        setRequireGrants(h.require_grants);
      })
      .catch(() => setAnswerUp(false));
    answerApi.suggestions(40).then(setSuggested).catch(() => void 0);
    api.scope().then(setScope).catch(() => void 0);
    refreshRecent();
  }, []);

  // Deep link: /?q=<question>[&db=<database>] asks it straight away (so
  // another application can link straight to an answer). Cleared from the URL
  // afterwards so a reload doesn't ask again.
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const q = params.get("q");
      if (!q) return;
      const db = params.get("db");
      if (db) setDatabase(db);
      params.delete("q");
      params.delete("db");
      const rest = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
      setTimeout(() => void askWith(q, db ?? ""), 0);
    } catch {
      /* no deep link */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const databases = useMemo(
    () => (scope?.databases ?? []).filter((d) => d.visible > 0).map((d) => d.database).sort((a, b) => a.localeCompare(b)),
    [scope],
  );

  const effectiveGrants: Grant[] =
    authEnabled && principal?.grants
      ? principal.grants
      : Array.from(manualGrants).map((token) => {
          const [level, category] = token.split(":");
          return { level: Number(level), category };
        });

  function toggleManualGrant(level: number, category: string) {
    const token = `${level}:${category}`;
    setManualGrants((prev) => {
      const next = new Set(prev);
      if (next.has(token)) next.delete(token);
      else next.add(token);
      return next;
    });
  }

  function ask(text: string) {
    return askWith(text, database);
  }

  async function askWith(text: string, db: string) {
    const q = text.trim();
    if (!q || loading) return;
    setAsked(q);
    setAskedBefore((prev) => new Set(prev).add(q.toLowerCase()));
    setQuestion(q);
    setFollowUp("");
    try {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch {
      /* older browsers */
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await answerApi.ask(q, effectiveGrants, 5, db || null));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      refreshRecent();
    }
  }

  function reset() {
    setAsked(null);
    setResult(null);
    setError(null);
    setQuestion("");
  }

  if (answerUp === false) {
    return (
      <div className="banner warn">
        {t("ask.disabledBanner", { cfg: "catalog.answer.enabled: false", proc: "answer", doc: "docs/integration.md" })}
      </div>
    );
  }

  const corner = (
    <>
      {!requireGrants && !authEnabled && (
        <button className="chip warn-chip chip-btn" onClick={() => setShowInfo(true)} title={t("ask.grantsOpenChipHint")}>
          {t("ask.grantsOpenChip")}
        </button>
      )}
      <button
        className={`icon-btn sm${showInfo ? " active" : ""}`}
        onClick={() => setShowInfo((v) => !v)}
        title={t("ask.infoToggle")}
        aria-label={t("ask.infoToggle")}
        aria-expanded={showInfo}
      >
        <Icon name="info" size={15} />
      </button>
    </>
  );

  const infoBox = showInfo && (
    <div className="info-box" style={{ maxWidth: 820, margin: "0 auto 16px" }}>
      <p style={{ marginTop: 0 }}>
        {t("ask.intro1")} <strong>{t("ask.trustWord")}</strong> {t("ask.intro2")} <span className="mono">docs/integration.md</span>.
      </p>
      {!requireGrants && !authEnabled && <p style={{ marginBottom: 0 }}>{t("ask.grantsOpenBanner", { cfg: "catalog.answer.require_grants: false", val: "true" })}</p>}
    </div>
  );

  // Sign-in off (testing only): pick the cells to ask as.
  const grantsControl =
    requireGrants && !authEnabled && matrix ? (
      <div className="ask-grants">
        <div className="picker-label">{t("ask.grantsPickerLabel")}</div>
        <div className="grant-picker">
          {matrix.levels.map((lvl) => (
            <div key={lvl.value} className="grant-picker-row">
              <span className="grant-picker-level">{lvl.name}</span>
              {matrix.categories.map((cat) => {
                const token = `${lvl.value}:${cat.key}`;
                return (
                  <label key={cat.key} className="grant-picker-cell">
                    <input type="checkbox" checked={manualGrants.has(token)} onChange={() => toggleManualGrant(lvl.value, cat.key)} />
                    {cat.key}
                  </label>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    ) : null;

  const history = historyOpen && <HistoryDrawer onClose={() => setHistoryOpen(false)} onAsk={ask} />;

  if (asked === null) {
    return (
      <>
        {infoBox}
        <AskHome
          question={question} setQuestion={setQuestion} database={database} setDatabase={setDatabase} databases={databases}
          onAsk={ask} suggested={suggested} recent={recent} scope={scope} status={status ?? null}
          onOpenHistory={() => setHistoryOpen(true)} onOpenHealth={onOpenHealth} extra={grantsControl} corner={corner}
        />
        {history}
      </>
    );
  }

  return (
    <>
      <AskAnswer
        asked={asked} result={result} loading={loading} error={error} followUp={followUp} setFollowUp={setFollowUp}
        database={database} setDatabase={setDatabase} databases={databases} onAsk={ask} onNew={reset}
        suggested={suggested} recent={recent} askedBefore={askedBefore} onOpenEntity={onOpenEntity}
        corner={corner} info={infoBox} extra={grantsControl} detailTab={detailTab} setDetailTab={setDetailTab}
      />
      {history}
    </>
  );
}
