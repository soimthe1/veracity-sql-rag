import { useEffect, useRef, useState } from "react";
import { api, EntityOut } from "../api";
import { useI18n } from "../i18n";
import Icon from "./Icon";
import StatusBadge from "./StatusBadge";

/**
 * Top-bar catalog search. Uses the same fuzzy, typo-tolerant search the
 * API already exposes (POST /v1/search — api/search/), so "custmer" still
 * finds Customer tables. Picking a result opens that entity's detail view.
 */
export default function GlobalSearch({ onOpenEntity }: { onOpenEntity: (id: string) => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<EntityOut[] | null>(null);
  const [active, setActive] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const h = setTimeout(() => {
      api
        .search(term, 8)
        .then((r) => {
          if (!cancelled) {
            setResults(r.map((x) => x.entity));
            setActive(0);
          }
        })
        .catch(() => !cancelled && setResults([]));
    }, 220);
    return () => {
      cancelled = true;
      clearTimeout(h);
    };
  }, [q]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function pick(e: EntityOut) {
    onOpenEntity(e.id);
    setOpen(false);
    setQ("");
  }

  return (
    <div className="gsearch" ref={wrap}>
      <div className="gsearch-box">
        <Icon name="search" size={15} strokeWidth={1.9} />
        <input
          type="search"
          value={q}
          placeholder={t("topbar.searchPlaceholder")}
          aria-label={t("topbar.searchPlaceholder")}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (!results || results.length === 0) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(results.length - 1, a + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(0, a - 1));
            } else if (e.key === "Enter") {
              pick(results[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
      </div>
      {open && results && (
        <div className="gsearch-pop" role="listbox">
          {results.length === 0 ? (
            <div className="gsearch-empty">{t("topbar.noResults", { q: q.trim() })}</div>
          ) : (
            results.map((e, i) => (
              <button
                key={e.id}
                type="button"
                role="option"
                aria-selected={i === active}
                className={`gsearch-item${i === active ? " active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(e)}
              >
                <Icon name={e.object_type === "VIEW" ? "list" : "table"} size={16} />
                <div className="gsearch-item-main">
                  <div className="gsearch-item-title">{e.semantic_name ?? e.object_name}</div>
                  <div className="gsearch-item-sub mono">
                    {e.database_name}.{e.schema_name}.{e.object_name}
                  </div>
                </div>
                <StatusBadge status={e.status} />
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
