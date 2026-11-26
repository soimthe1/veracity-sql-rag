/**
 * The classification lattice (levels x categories) for the whole UI —
 * fetched once from GET /v1/auth/matrix (public display metadata, read by
 * the API from config/levels.yaml). Provides level colours, names in both
 * languages, and optional cell labels ("10A" => "J1 Normal"), plus the
 * <CellChip> every screen uses to show a cell.
 *
 * A cell is written LEVEL THEN CATEGORY, "20B", "40A" (the knowledge
 * platform's spelling), strictly: "B20" is not a cell and never parses.
 */
import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { api, CategoryDef, LevelDef, MatrixShape } from "../api";
import { useI18n } from "../i18n";

/** (20, "B") => "20B". An axis that isn't set shows as "*" ("20*", "*B"). */
export function cellToken(level: number | null | undefined, category: string | null | undefined): string {
  return `${level ?? "*"}${(category ?? "*").toUpperCase()}`;
}

const CELL_RE = /^([0-9]{1,4})([A-Z])$/;

/** "20B" => {level: 20, category: "B"}; anything else ("B20", "20b") => null. */
export function parseCell(token: string): { level: number; category: string } | null {
  const m = CELL_RE.exec(token);
  return m ? { level: Number(m[1]), category: m[2] } : null;
}

/** Sorted by level, then category: 10A, 20B, 100A. */
export function sortCellTokens(tokens: Iterable<string>): string[] {
  return Array.from(tokens).sort((a, b) => {
    const pa = parseCell(a), pb = parseCell(b);
    if (!pa || !pb) return a.localeCompare(b);
    return pa.level - pb.level || pa.category.localeCompare(pb.category);
  });
}

interface LatticeState {
  ready: boolean;
  levels: LevelDef[];
  /** enabled categories, quarantine included */
  categories: CategoryDef[];
  grantable: Set<string>;
  defaultLevel: number | null;
  defaultCategory: string | null;
  level: (v: number | null | undefined) => LevelDef | undefined;
  category: (k: string | null | undefined) => CategoryDef | undefined;
  levelName: (v: number | null | undefined) => string;
  categoryName: (k: string | null | undefined) => string;
  color: (v: number | null | undefined) => string;
  cellLabel: (level: number | null | undefined, category: string | null | undefined) => string | null;
  isQuarantine: (k: string | null | undefined) => boolean;
}

const LatticeContext = createContext<LatticeState | null>(null);

export function LatticeProvider({ children }: { children: ReactNode }) {
  const { lang } = useI18n();
  const [shape, setShape] = useState<MatrixShape | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .matrix()
        .then((m) => alive && setShape(m))
        .catch(() => alive && setTimeout(load, 5000));
    load();
    return () => {
      alive = false;
    };
  }, []);

  const value = useMemo<LatticeState>(() => {
    const levels = [...(shape?.levels ?? [])].sort((a, b) => a.value - b.value);
    const categories = (shape?.all_categories ?? shape?.categories ?? []).filter((c) => c.enabled);
    const byLevel = new Map(levels.map((l) => [l.value, l]));
    const byCat = new Map(categories.map((c) => [c.key, c]));
    const labels = shape?.cell_labels ?? {};
    const ar = lang === "ar";
    return {
      ready: !!shape,
      levels,
      categories,
      grantable: new Set(shape?.grantable ?? categories.filter((c) => !c.reserved).map((c) => c.key)),
      defaultLevel: shape?.default_level ?? null,
      defaultCategory: shape?.default_category ?? null,
      level: (v) => (v == null ? undefined : byLevel.get(v)),
      category: (k) => (k ? byCat.get(k.toUpperCase()) : undefined),
      levelName: (v) => {
        const l = v == null ? undefined : byLevel.get(v);
        return l ? (ar ? l.name_ar || l.name : l.name) : v == null ? "" : String(v);
      },
      categoryName: (k) => {
        const c = k ? byCat.get(k.toUpperCase()) : undefined;
        return c ? (ar ? c.name_ar || c.name : c.name) : k ?? "";
      },
      color: (v) => (v == null ? "var(--gray)" : byLevel.get(v)?.color || "var(--gray)"),
      cellLabel: (level, category) => {
        const l = labels[cellToken(level, category)];
        return l ? (ar ? l.name_ar || l.name : l.name) : null;
      },
      isQuarantine: (k) => !!(k && byCat.get(k.toUpperCase())?.reserved),
    };
  }, [shape, lang]);

  return <LatticeContext.Provider value={value}>{children}</LatticeContext.Provider>;
}

export function useLattice(): LatticeState {
  const ctx = useContext(LatticeContext);
  if (!ctx) throw new Error("useLattice must be used within LatticeProvider");
  return ctx;
}

/** A cell, colour-coded by its level. Hover shows level, category and the
 * cell's own label (config/levels.yaml cell_labels) when one is set. */
export function CellChip({
  level,
  category,
  token,
  size = "md",
  showLabel = false,
  title,
}: {
  level?: number | null;
  category?: string | null;
  token?: string;
  size?: "sm" | "md";
  showLabel?: boolean;
  title?: string;
}) {
  const lat = useLattice();
  const { t } = useI18n();
  let lv = level ?? null;
  let cat = category ?? null;
  if (token) {
    const p = parseCell(token);
    if (!p) {
      // Shown as-is and flagged, never re-read: "B20" is not "20B".
      return <span className="cell-chip sm invalid" title={t("cell.invalid")}><b>{token}</b></span>;
    }
    lv = p.level;
    cat = p.category;
  }
  if (lv == null && !cat) return <span className="muted">—</span>;
  const label = lat.cellLabel(lv, cat);
  const quarantine = lat.isQuarantine(cat);
  const tip = title ?? [label, `${lat.levelName(lv)} · ${lat.categoryName(cat)}`].filter(Boolean).join(" — ");
  return (
    <span
      className={`cell-chip ${size}${quarantine ? " quarantine" : ""}`}
      style={{ ["--cell" as string]: lat.color(lv) }}
      title={tip}
    >
      <b>{cellToken(lv, cat)}</b>
      {showLabel && label && <span className="cell-chip-label">{label}</span>}
    </span>
  );
}

/** Level name as a colour-banded "classification" banner (Ask answers). */
export function ClassificationBanner({ level, category }: { level: number; category: string }) {
  const lat = useLattice();
  const label = lat.cellLabel(level, category);
  return (
    <div className="classification-banner" style={{ ["--cell" as string]: lat.color(level) }}>
      <span className="classification-banner-level">{lat.levelName(level)}</span>
      <span className="classification-banner-cell">
        {cellToken(level, category)} · {lat.categoryName(category)}
        {label ? ` · ${label}` : ""}
      </span>
    </div>
  );
}
