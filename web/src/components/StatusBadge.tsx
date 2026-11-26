import { useI18n } from "../i18n";

export default function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  // The CSS class always keys off the raw English status word (so
  // styles.css's .badge.trusted etc. never need translating), while the
  // visible text goes through the dictionary — falls back to the raw
  // value for any status string not in status.* (defensive, not
  // expected in practice).
  return <span className={`badge ${status.toLowerCase()}`}>{t(`status.${status}`) || status}</span>;
}
