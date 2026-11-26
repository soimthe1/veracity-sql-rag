type T = (path: string, vars?: Record<string, string | number>) => string;

/** "2 min ago" / "3 h ago" / "4 d ago" — `nowMs` lets a caller use the
 * server's clock (summary.generated_at) instead of a skewed local one. */
export function relTime(iso: string | null | undefined, t: T, nowMs: number = Date.now()): string {
  if (!iso) return t("common.never");
  const diff = Math.max(0, nowMs - new Date(iso).getTime());
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return t("time.justNow");
  if (mins < 60) return t("time.minutesAgo", { n: mins });
  const hours = Math.floor(mins / 60);
  if (hours < 48) return t("time.hoursAgo", { n: hours });
  return t("time.daysAgo", { n: Math.floor(hours / 24) });
}

export function fmtDate(iso: string | null | undefined, never: string): string {
  return iso ? new Date(iso).toLocaleString() : never;
}

export function fmtPct(v: number, digits = 0): string {
  return `${v.toFixed(digits)}%`;
}

export function initials(name: string | null | undefined): string {
  const parts = (name ?? "").replace(/^.*\\/, "").split(/[\s._@-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[1][0] : "")).toUpperCase();
}
