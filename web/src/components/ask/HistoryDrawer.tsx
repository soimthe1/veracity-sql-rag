/** Everything this person has asked — Ask > Recent questions > View all. */
import { useEffect, useState } from "react";
import { answerApi, RecentQuestion } from "../../api";
import { useI18n } from "../../i18n";
import { relTime } from "../../lib/format";
import Icon from "../Icon";
import { OutcomeTag, TrustScore } from "./shared";

const PAGE = 25;

export default function HistoryDrawer({ onClose, onAsk }: { onClose: () => void; onAsk: (q: string) => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<RecentQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load(offset: number, append: boolean) {
    setLoading(true);
    answerApi.history(PAGE, offset, q)
      .then((h) => {
        setItems((prev) => (append ? [...prev, ...h.items] : h.items));
        setTotal(h.total);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    const h = setTimeout(() => load(0, false), 250);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay side" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" style={{ width: 560 }} role="dialog" aria-label={t("ask.home.historyTitle")}>
        <div className="dialog-head">
          <h3><Icon name="clock" size={17} /> {t("ask.home.historyTitle")}</h3>
          <button className="icon-btn sm" onClick={onClose} aria-label={t("common.close")}><Icon name="x" size={14} /></button>
        </div>
        <div className="dialog-body">
          <div className="search-field" style={{ marginBottom: 12 }}>
            <Icon name="search" size={15} />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("ask.home.historySearch")} autoFocus />
          </div>
          <div className="muted small" style={{ marginBottom: 10 }}>{t("ask.home.historyCount", { n: total })}</div>
          {error && <div className="banner error">{error}</div>}
          <div className="askx-history">
            {items.map((r) => (
              <button key={r.ask_id ?? `${r.question}${r.asked_at}`} type="button" className="askx-history-row"
                onClick={() => { onAsk(r.question); onClose(); }} title={t("ask.home.askAgain")}>
                <span className="askx-history-q" dir="auto">{r.question}</span>
                <span className="askx-history-meta">
                  <OutcomeTag outcome={r.outcome} answered={r.answered} />
                  <TrustScore score={r.trust_score} />
                  {r.database_name && <span className="muted small"><Icon name="database" size={12} /> {r.database_name}</span>}
                  <span className="muted small" style={{ marginInlineStart: "auto" }}>{relTime(r.asked_at, t)}</span>
                </span>
              </button>
            ))}
            {!loading && items.length === 0 && <div className="askx-empty">{t("ask.home.historyEmpty")}</div>}
          </div>
          {items.length < total && (
            <button className="btn-ghost btn-sm" style={{ marginTop: 12, width: "100%" }} disabled={loading} onClick={() => load(items.length, true)}>
              {loading ? t("common.loading") : t("ask.home.loadMore")}
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}
