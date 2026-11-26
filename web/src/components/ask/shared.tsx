/**
 * Pieces shared by the Ask landing page and the answer view.
 */
import type { AskOutcome, QuestionKind } from "../../api";
import { useI18n } from "../../i18n";
import Icon from "../Icon";

/** The shape of a suggested question (answer/suggestions.py's kind). */
export const KINDS: { kind: QuestionKind; icon: string; tone: string }[] = [
  { kind: "breakdown", icon: "chart", tone: "violet" },
  { kind: "count", icon: "overview", tone: "blue" },
  { kind: "latest", icon: "clock", tone: "cyan" },
  { kind: "list", icon: "list", tone: "green" },
];

export function kindMeta(kind?: string | null) {
  return KINDS.find((k) => k.kind === kind) ?? KINDS[1];
}

export const OUTCOME_TONE: Record<AskOutcome, string> = {
  ANSWERED: "good",
  NO_ROWS: "neutral",
  NOT_FOUND: "neutral",
  DECLINED: "neutral",
  BLOCKED: "bad",
  ERROR: "bad",
  DISABLED: "neutral",
};

export function OutcomeTag({ outcome, answered }: { outcome?: AskOutcome | null; answered?: boolean }) {
  const { t } = useI18n();
  const o: AskOutcome = outcome ?? (answered ? "ANSWERED" : "NO_ROWS");
  const tone = OUTCOME_TONE[o];
  return (
    <span className={`outcome-tag ${tone}`}>
      <Icon name={tone === "good" ? "check" : tone === "bad" ? "alert" : "info"} size={14} />
      {t(`ask.outcome.${o}`)}
    </span>
  );
}

export function TrustScore({ score }: { score?: number | null }) {
  if (score == null) return <span className="muted">—</span>;
  const pct = Math.round(score * 100);
  const tone = pct >= 80 ? "good" : pct >= 50 ? "warn" : "bad";
  return <span className={`trust-pill ${tone}`}>{pct}%</span>;
}

/** The question box: sparkle, text, database picker, send. */
export function AskInput({
  value, onChange, onSubmit, database, onDatabase, databases, disabled, compact, autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  database: string;
  onDatabase: (v: string) => void;
  databases: string[];
  disabled?: boolean;
  compact?: boolean;
  autoFocus?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className={`askx-input${compact ? " compact" : ""}`}>
      <span className="askx-input-icon"><Icon name="sparkle" size={compact ? 16 : 19} /></span>
      <input
        type="text"
        dir="auto"
        placeholder={t("ask.placeholderLong")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && !disabled && onSubmit()}
        aria-label={t("ask.placeholderLong")}
        autoFocus={autoFocus}
      />
      {databases.length > 1 && (
        <select className="askx-db" value={database} onChange={(e) => onDatabase(e.target.value)}
          aria-label={t("ask.dbPicker")} title={t("ask.dbPickerHint")}>
          <option value="">{t("ask.allDatabases")}</option>
          {databases.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      )}
      <button className="askx-send" onClick={onSubmit} disabled={disabled || !value.trim()} aria-label={t("ask.askButton")}
        title={t("ask.askButton")}>
        <Icon name="chevron" size={compact ? 17 : 19} strokeWidth={2.2} className="flip-rtl" />
      </button>
    </div>
  );
}
