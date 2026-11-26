import { useEffect, useMemo, useState } from "react";
import { api, EntityOut, RelationshipOut } from "../api";
import { useI18n } from "../i18n";
import { mulberry32, simulateForceLayout, statusColor } from "../lib/forceGraph";

/**
 * Whole-catalog relationship map (GET /v1/schema-graph).
 *
 * Relationships never cross databases, so instead of one tangle of every
 * table, each database gets its own labelled box, laid out independently
 * and packed in a grid — related tables stay together and labels have
 * room. Defaults: every trust level (TRUSTED-only is a toggle — as the
 * default it showed 6 of 256 tables), and only tables that have at least
 * one relationship (isolated tables are a toggle). Hovering a table
 * highlights its direct neighbours.
 */
const MAX_NODES = 600;
const CANVAS_W = 1100;
const CELL_GAP = 16;
const HEADER_H = 26;

interface GNode {
  id: string;
  label: string;
  full: string;
  status: string;
  database: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

interface GEdge {
  from: string;
  to: string;
  status: string;
}

interface Cell {
  database: string;
  x: number;
  y: number;
  w: number;
  h: number;
  nodes: GNode[];
}

function layout(nodes: GNode[], edges: GEdge[]): { cells: Cell[]; height: number } {
  const byDb = new Map<string, GNode[]>();
  for (const n of nodes) {
    if (!byDb.has(n.database)) byDb.set(n.database, []);
    byDb.get(n.database)!.push(n);
  }
  const groups = [...byDb.entries()].sort((a, b) => b[1].length - a[1].length);
  const cells: Cell[] = [];
  let x = 0;
  let y = 0;
  let rowH = 0;
  for (const [database, members] of groups) {
    const n = members.length;
    const ids0 = new Set(members.map((m) => m.id));
    // Denser clusters (many edges per table) get more room.
    const density = edges.filter((e) => ids0.has(e.from) && ids0.has(e.to)).length / Math.max(1, n);
    const scale = 1 + Math.min(0.25, Math.max(0, density - 1) * 0.15);
    // Width budget: at most ~half the canvas so two databases share a row.
    const w = Math.min(Math.floor((CANVAS_W - CELL_GAP) / 2), Math.max(250, Math.round(Math.sqrt(n) * 150 * scale)));
    const h = Math.max(160, Math.round(Math.sqrt(n) * 108 * scale)) + HEADER_H;
    if (x > 0 && x + w > CANVAS_W) {
      x = 0;
      y += rowH + CELL_GAP;
      rowH = 0;
    }
    const ids = new Set(members.map((m) => m.id));
    const rnd = mulberry32(database.length * 7919 + n);
    for (const m of members) {
      m.x = w / 2 + (rnd() - 0.5) * w * 0.7;
      m.y = HEADER_H + (h - HEADER_H) / 2 + (rnd() - 0.5) * (h - HEADER_H) * 0.7;
      m.vx = 0;
      m.vy = 0;
    }
    simulateForceLayout(
      members,
      edges.filter((e) => ids.has(e.from) && ids.has(e.to)),
      { width: w, height: h, iterations: 320, repulsion: 7000 * scale, springLength: 105 * scale, spring: 0.03 },
    );
    for (const m of members) {
      // keep clear of the header and the right edge (labels extend right)
      m.x = Math.max(12, Math.min(w - 110, m.x));
      m.y = Math.max(HEADER_H + 14, Math.min(h - 12, m.y));
    }
    cells.push({ database, x, y, w, h, nodes: members });
    x += w + CELL_GAP;
    rowH = Math.max(rowH, h);
  }
  return { cells, height: y + rowH };
}

export default function SchemaGraph({ onSelectEntity }: { onSelectEntity?: (id: string) => void }) {
  const { t } = useI18n();
  const [trustedOnly, setTrustedOnly] = useState(false);
  const [showIsolated, setShowIsolated] = useState(false);
  const [data, setData] = useState<{ nodes: EntityOut[]; edges: RelationshipOut[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api
      .schemaGraph({ trusted_only: trustedOnly, limit: MAX_NODES })
      .then((res) => setData({ nodes: res.nodes, edges: res.edges, total: res.total_entities }))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [trustedOnly]);

  const graph = useMemo(() => {
    if (!data) return null;
    const known = new Set(data.nodes.map((n) => n.id));
    const edges: GEdge[] = data.edges
      .filter((e) => known.has(e.from_entity_id) && known.has(e.to_entity_id) && e.from_entity_id !== e.to_entity_id)
      .map((e) => ({ from: e.from_entity_id, to: e.to_entity_id, status: e.status }));
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.from, (degree.get(e.from) ?? 0) + 1);
      degree.set(e.to, (degree.get(e.to) ?? 0) + 1);
    }
    const nodes: GNode[] = data.nodes
      .filter((n) => n.object_type === "TABLE" || n.object_type === "VIEW")
      .filter((n) => showIsolated || (degree.get(n.id) ?? 0) > 0)
      .map((n) => ({
        id: n.id,
        label: n.object_name,
        full: `${n.database_name}.${n.schema_name}.${n.object_name}${n.semantic_name ? ` — ${n.semantic_name}` : ""}`,
        status: n.status,
        database: n.database_name,
        x: 0,
        y: 0,
        vx: 0,
        vy: 0,
      }));
    const shownIds = new Set(nodes.map((n) => n.id));
    const shownEdges = edges.filter((e) => shownIds.has(e.from) && shownIds.has(e.to));
    const neighbors = new Map<string, Set<string>>();
    for (const e of shownEdges) {
      if (!neighbors.has(e.from)) neighbors.set(e.from, new Set());
      if (!neighbors.has(e.to)) neighbors.set(e.to, new Set());
      neighbors.get(e.from)!.add(e.to);
      neighbors.get(e.to)!.add(e.from);
    }
    const { cells, height } = layout(nodes, shownEdges);
    const isolated = data.nodes.filter(
      (n) => (n.object_type === "TABLE" || n.object_type === "VIEW") && (degree.get(n.id) ?? 0) === 0,
    ).length;
    return { cells, height, edges: shownEdges, neighbors, nodeCount: nodes.length, isolated };
  }, [data, showIsolated]);

  if (error) return <div className="banner error">{error}</div>;

  const controls = (
    <div className="toolbar" style={{ marginBottom: 12 }}>
      <label className="toggle">
        <input type="checkbox" checked={trustedOnly} onChange={(e) => setTrustedOnly(e.target.checked)} /> {t("schemaGraph.trustedOnly")}
      </label>
      <label className="toggle">
        <input type="checkbox" checked={showIsolated} onChange={(e) => setShowIsolated(e.target.checked)} /> {t("schemaGraph.showIsolated")}
      </label>
      {graph && (
        <span className="muted" style={{ fontSize: 12 }}>
          {t("schemaGraph.summary", {
            shown: graph.nodeCount,
            edges: graph.edges.length,
            dbs: graph.cells.length,
            isolated: !showIsolated && graph.isolated ? t("schemaGraph.isolatedHidden", { n: graph.isolated }) : "",
          })}{" "}
          · {t("schemaGraph.clickHint")}
        </span>
      )}
    </div>
  );

  if (!graph) return <div>{controls}<div className="empty">{t("schemaGraph.loading")}</div></div>;
  if (graph.nodeCount === 0) return <div>{controls}<div className="empty">{t("schemaGraph.empty")}</div></div>;

  const active = hover ? graph.neighbors.get(hover) ?? new Set<string>() : null;
  const isLit = (id: string) => !active || id === hover || active.has(id);

  return (
    <div>
      {controls}
      <div className="graph-canvas" style={{ padding: 12, background: "var(--surface)" }}>
        <svg direction="ltr" viewBox={`0 0 ${CANVAS_W} ${graph.height}`} width="100%" style={{ display: "block", minWidth: 900 }} onMouseLeave={() => setHover(null)}>
          {graph.cells.map((cell) => {
            const pos = new Map(cell.nodes.map((n) => [n.id, n]));
            return (
              <g key={cell.database} transform={`translate(${cell.x}, ${cell.y})`}>
                <rect width={cell.w} height={cell.h} rx={10} style={{ fill: "var(--surface-2)", stroke: "var(--border)" }} />
                <text x={12} y={18} fontSize={11.5} fontWeight={700} style={{ fill: "var(--text)" }}>
                  {cell.database}
                  <tspan dx={6} fontWeight={400} style={{ fill: "var(--text-faint)" }}>
                    {cell.nodes.length}
                  </tspan>
                </text>
                {graph.edges.map((e, i) => {
                  const a = pos.get(e.from);
                  const b = pos.get(e.to);
                  if (!a || !b) return null;
                  const lit = !active || e.from === hover || e.to === hover;
                  return (
                    <line
                      key={i}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      style={{ stroke: statusColor(e.status) }}
                      strokeWidth={lit && active ? 2 : 1.2}
                      opacity={lit ? 0.7 : 0.08}
                    />
                  );
                })}
                {cell.nodes.map((n) => (
                  <g
                    key={n.id}
                    transform={`translate(${n.x}, ${n.y})`}
                    style={{ cursor: onSelectEntity ? "pointer" : "default" }}
                    opacity={isLit(n.id) ? 1 : 0.2}
                    onMouseEnter={() => setHover(n.id)}
                    onClick={onSelectEntity ? () => onSelectEntity(n.id) : undefined}
                  >
                    <title>{n.full}</title>
                    <circle r={n.id === hover ? 7 : 5.5} style={{ fill: statusColor(n.status) }} stroke="var(--panel)" strokeWidth={1.5} />
                    <text x={9} y={3.5} fontSize={10} className="graph-label">
                      {n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label}
                    </text>
                  </g>
                ))}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
