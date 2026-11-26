import { useMemo } from "react";
import { AskGraphEdge, AskGraphNode } from "../api";
import { mulberry32, simulateForceLayout, statusColor } from "../lib/forceGraph";

/**
 * Per-answer relationship picture — SchemaGraph.tsx draws the whole
 * catalog, this draws just the handful of tables (and the relationships
 * between them) a single Ask question was grounded on. Nodes are the
 * redesign's labelled pills: border and dot colored by trust.
 */
const WIDTH = 640;
const HEIGHT = 300;
const NODE_H = 32;

interface GNode extends AskGraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  w: number;
  short: string;
}

function shortLabel(label: string): string {
  const name = label.includes(".") ? label.slice(label.lastIndexOf(".") + 1) : label;
  return name.length > 24 ? name.slice(0, 23) + "…" : name;
}

export default function AskGraph({
  nodes,
  edges,
  onSelect,
}: {
  nodes: AskGraphNode[];
  edges: AskGraphEdge[];
  onSelect?: (id: string) => void;
}) {
  const laidOut = useMemo(() => {
    if (nodes.length === 0) return [] as GNode[];
    const rnd = mulberry32(7); // deterministic scatter across re-renders of the same answer
    const gnodes: GNode[] = nodes.map((n) => {
      const short = shortLabel(n.label);
      return {
        ...n,
        short,
        w: Math.max(84, short.length * 6.6 + 36),
        x: WIDTH / 2 + (rnd() - 0.5) * WIDTH * 0.7,
        y: HEIGHT / 2 + (rnd() - 0.5) * HEIGHT * 0.7,
        vx: 0,
        vy: 0,
      };
    });
    simulateForceLayout(
      gnodes,
      edges.map((e) => ({ from: e.from_id, to: e.to_id })),
      { width: WIDTH, height: HEIGHT, iterations: 260, repulsion: 12000, springLength: 150, spring: 0.025 },
    );
    // Pills are wide: push apart any two whose boxes overlap.
    for (let pass = 0; pass < 60; pass++) {
      let moved = false;
      for (let i = 0; i < gnodes.length; i++) {
        for (let j = i + 1; j < gnodes.length; j++) {
          const a = gnodes[i];
          const b = gnodes[j];
          const ox = (a.w + b.w) / 2 + 14 - Math.abs(a.x - b.x);
          const oy = NODE_H + 14 - Math.abs(a.y - b.y);
          if (ox > 0 && oy > 0) {
            moved = true;
            if (oy < ox) {
              const d = (a.y <= b.y ? -1 : 1) * (oy / 2);
              a.y += d;
              b.y -= d;
            } else {
              const d = (a.x <= b.x ? -1 : 1) * (ox / 2);
              a.x += d;
              b.x -= d;
            }
          }
        }
      }
      for (const n of gnodes) {
        n.x = Math.max(n.w / 2 + 8, Math.min(WIDTH - n.w / 2 - 8, n.x));
        n.y = Math.max(NODE_H / 2 + 8, Math.min(HEIGHT - NODE_H / 2 - 8, n.y));
      }
      if (!moved) break;
    }
    for (const n of gnodes) {
      n.x = Math.max(n.w / 2 + 8, Math.min(WIDTH - n.w / 2 - 8, n.x));
      n.y = Math.max(NODE_H / 2 + 8, Math.min(HEIGHT - NODE_H / 2 - 8, n.y));
    }
    return gnodes;
  }, [nodes, edges]);

  if (laidOut.length === 0) return null;
  const byId = new Map(laidOut.map((n) => [n.id, n]));

  return (
    <svg direction="ltr" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" style={{ display: "block", maxHeight: 360 }}>
      {edges.map((e, i) => {
        const a = byId.get(e.from_id);
        const b = byId.get(e.to_id);
        if (!a || !b) return null;
        return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} style={{ stroke: statusColor(e.status) }} strokeWidth={1.4} opacity={0.55} />;
      })}
      {laidOut.map((n) => (
        <g
          key={n.id}
          transform={`translate(${n.x - n.w / 2}, ${n.y - NODE_H / 2})`}
          style={{ cursor: onSelect ? "pointer" : "default" }}
          onClick={onSelect ? () => onSelect(n.id) : undefined}
        >
          <title>{`${n.label} — ${n.status} (${Math.round(n.confidence * 100)}%)`}</title>
          <rect width={n.w} height={NODE_H} rx={8} style={{ fill: "var(--surface)", stroke: statusColor(n.status) }} strokeWidth={1.5} />
          <circle cx={14} cy={NODE_H / 2} r={3.5} style={{ fill: statusColor(n.status) }} />
          <text x={26} y={NODE_H / 2 + 4} fontSize={11} fontWeight={650} style={{ fill: "var(--text)" }}>
            {n.short}
          </text>
        </g>
      ))}
    </svg>
  );
}
