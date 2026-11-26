import { createContext, ReactNode, useContext, useEffect, useState } from "react";

/**
 * Light/dark theme state. Every visual token lives in styles.css as a
 * CSS custom property (see :root and :root[data-theme="light"]) — this
 * context's only job is choosing which one is active, persisting that
 * choice, and reflecting it onto <html data-theme="..."> so the whole
 * CSS tree (plus SVG that reads the same vars via inline style, e.g.
 * EntityDiagram.tsx) re-skins itself with no per-component plumbing.
 *
 * Same "session state, backed by storage, never a hard dependency"
 * posture as auth.tsx: a private-mode/storage-disabled browser just
 * means the choice doesn't survive a refresh, not a crash.
 */
export type Theme = "dark" | "light";

const STORAGE_KEY = "ai_catalog_theme";

interface ThemeState {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage unavailable — fall through */
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* private mode / storage disabled — theme still applies this tab */
    }
  }, [theme]);

  return (
    <ThemeContext.Provider
      value={{
        theme,
        setTheme: setThemeState,
        toggleTheme: () => setThemeState((t) => (t === "dark" ? "light" : "dark")),
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}
