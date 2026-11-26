/**
 * Shared building blocks for the redesigned UI (wireframes in
 * ai_sql_catalog_redesign/): section cards, KPI tiles, bar rows,
 * segmented toggles, pagination. Presentation only — no data fetching —
 * so every page composes the same visual language from its own real data.
 */
import { ReactNode } from "react";
import Icon from "./Icon";
import { useI18n } from "../i18n";

export type Tone = "blue" | "violet" | "cyan" | "green" | "amber" | "orange" | "red" | "gray";

/** Trust/state vocabulary -> semantic tone (green verified, amber review, …). */
export function statusTone(status: string | null | undefined): Tone {
  switch ((status ?? "").toUpperCase()) {
    case "TRUSTED":
    case "HEALTHY":
    case "OK":
      return "green";
    case "LEARNING":
    case "DEGRADED":
    case "DISCOVERED":
      return "amber";
    case "SUSPECT":
    case "STALE":
      return "orange";
    case "UNAVAILABLE":
    case "ERROR":
    case "DISABLED":
      return "red";
    default:
      return "gray";
  }
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
  bodyClass = "panel-body",
  headerOnly,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClass?: string;
  headerOnly?: boolean;
}) {
  return (
    <section className={`panel ${headerOnly ? "header-card" : ""} ${className}`}>
      {(title || actions) && (
        <div className="panel-head">
          <div className="panel-head-text">
            {title && <h3 className="panel-title">{title}</h3>}
            {subtitle && <div className="panel-sub">{subtitle}</div>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </div>
      )}
      {!headerOnly && children !== undefined && <div className={bodyClass}>{children}</div>}
    </section>
  );
}

export function Kpi({
  icon,
  tone = "blue",
  tag,
  value,
  label,
  hint,
  hintWarn,
  onClick,
  title,
  textValue,
}: {
  icon: string;
  tone?: Tone;
  tag?: ReactNode;
  value: ReactNode;
  label: ReactNode;
  hint?: ReactNode;
  hintWarn?: boolean;
  onClick?: () => void;
  title?: string;
  textValue?: boolean;
}) {
  const body = (
    <>
      <div className="kpi-icon">
        <Icon name={icon} size={19} strokeWidth={1.8} />
      </div>
      {tag != null && tag !== "" && <div className="kpi-tag">{tag}</div>}
      <div className={`kpi-value${textValue ? " text" : ""}`}>{value}</div>
      <div className="kpi-label">{label}</div>
      {hint && <div className={`kpi-hint${hintWarn ? " warn" : ""}`}>{hint}</div>}
    </>
  );
  return onClick ? (
    <button type="button" className={`kpi tone-${tone}`} onClick={onClick} title={title}>
      {body}
    </button>
  ) : (
    <div className={`kpi tone-${tone}`} title={title}>
      {body}
    </div>
  );
}

export function BarRow({
  label,
  pct,
  value,
  tone = "blue",
  description,
  marker,
  markerTitle,
  onClick,
  title,
}: {
  label: ReactNode;
  pct: number;
  value?: ReactNode;
  tone?: Tone;
  description?: ReactNode;
  marker?: number;
  markerTitle?: string;
  onClick?: () => void;
  title?: string;
}) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div className={`bar-row tone-${tone}${onClick ? " clickable" : ""}`} onClick={onClick} title={title}>
      <div className="bar-row-head">
        <span className="bar-row-label">{label}</span>
        <span className="bar-row-value">{value ?? `${Math.round(pct)}%`}</span>
      </div>
      {description && <div className="bar-row-desc">{description}</div>}
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${w}%` }} />
        {marker != null && <div className="bar-marker" style={{ insetInlineStart: `${Math.min(100, marker)}%` }} title={markerTitle} />}
      </div>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: ReactNode; icon?: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={o.value === value} className={o.value === value ? "active" : ""} onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} size={14} strokeWidth={1.9} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
}) {
  const { t } = useI18n();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);
  // A window of at most 5 page buttons around the current page.
  const start = Math.max(0, Math.min(page - 2, pages - 5));
  const nums = Array.from({ length: Math.min(5, pages) }, (_, i) => start + i);
  return (
    <div className="pager">
      <span>{t("ui.showing", { from, to, total })}</span>
      {pages > 1 && (
        <div className="pager-pages">
          <button type="button" disabled={page === 0} onClick={() => onPage(page - 1)} aria-label={t("ui.previous")}>
            <Icon name="chevron" size={13} strokeWidth={2} className="chev-back" />
          </button>
          {nums.map((n) => (
            <button key={n} type="button" className={n === page ? "active" : ""} onClick={() => onPage(n)}>
              {n + 1}
            </button>
          ))}
          <button type="button" disabled={page >= pages - 1} onClick={() => onPage(page + 1)} aria-label={t("ui.next")}>
            <Icon name="chevron" size={13} strokeWidth={2} className="flip-rtl" />
          </button>
        </div>
      )}
    </div>
  );
}

export function Pill({ tone = "blue", children, solid, title }: { tone?: Tone; children: ReactNode; solid?: boolean; title?: string }) {
  return (
    <span className={`pill tone-${tone}${solid ? " solid" : ""}`} title={title}>
      {children}
    </span>
  );
}

export function pct(n: number, total: number): number {
  return total > 0 ? (n / total) * 100 : 0;
}
