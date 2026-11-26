import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { en } from "./locales/en";
import { ar } from "./locales/ar";

/**
 * Full English/Arabic bilingual support. Same shape as theme.tsx and
 * auth.tsx: a small context, persisted in localStorage, reflected onto
 * <html lang/dir> so the whole document (not just React-rendered text)
 * agrees on direction — logical CSS properties in styles.css then
 * flip the layout for free.
 *
 * Dictionaries are plain nested objects (see locales/en.ts, ar.ts),
 * kept in lockstep by TypeScript: `ar` is typed against `typeof en`,
 * so a key added to one and forgotten in the other is a build error,
 * not a silently-missing translation discovered in production.
 */
export type Lang = "en" | "ar";

const STORAGE_KEY = "ai_catalog_lang";

export const DICTIONARIES = { en, ar } as const;

// Dot-path lookup with {{var}} interpolation — e.g. t("entities.foundCount", { n: 12 })
// against a string value "Found {{n}} entities". Deliberately tiny (no
// pluralization engine, no ICU) — this project's strings don't need it,
// and a missing/mistyped key falls back to the key itself so a typo is
// visible in the UI instead of silently blank.
function resolve(dict: Record<string, unknown>, path: string): string | undefined {
  let node: unknown = dict;
  for (const part of path.split(".")) {
    if (typeof node !== "object" || node === null) return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{\{(\w+)\}\}/g, (_, key) => (key in vars ? String(vars[key]) : `{{${key}}}`));
}

interface I18nState {
  lang: Lang;
  dir: "ltr" | "rtl";
  setLang: (l: Lang) => void;
  toggleLang: () => void;
  t: (path: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nState | null>(null);

function getInitialLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "ar") return stored;
  } catch {
    /* storage unavailable — fall through */
  }
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ar")) return "ar";
  return "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(getInitialLang);
  const dir: "ltr" | "rtl" = lang === "ar" ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang);
    document.documentElement.setAttribute("dir", dir);
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* private mode / storage disabled — language still applies this tab */
    }
  }, [lang, dir]);

  const t = useMemo(() => {
    const dict = DICTIONARIES[lang];
    const fallback = DICTIONARIES.en;
    return (path: string, vars?: Record<string, string | number>) => {
      const value = resolve(dict, path) ?? resolve(fallback, path) ?? path;
      return interpolate(value, vars);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

  return (
    <I18nContext.Provider
      value={{
        lang,
        dir,
        setLang: setLangState,
        toggleLang: () => setLangState((l) => (l === "en" ? "ar" : "en")),
        t,
      }}
    >
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nState {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within an I18nProvider");
  return ctx;
}
