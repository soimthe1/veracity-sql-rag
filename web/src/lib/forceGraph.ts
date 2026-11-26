/**
 * Tiny force-directed layout — repulsion + spring edges + light
 * centering, run for a fixed number of iterations synchronously once
 * per dataset. No charting/graph-drawing library: this is a handful of
 * lines of geometry, and every consumer (SchemaGraph.tsx for the
 * whole-catalog map, AskGraph.tsx for one answer's tables) just needs
 * dots and lines, not a general-purpose graph engine.
 *
 * Generic over any node shape that carries at least {id, x, y, vx, vy}
 * so callers can attach their own fields (label, status, confidence, …)
 * without this module knowing about them.
 */

export interface ForceNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

export interface ForceEdge {
  from: string;
  to: string;
}

export interface ForceLayoutOptions {
  width: number;
  height: number;
  iterations?: number;
  repulsion?: number;
  spring?: number;
  springLength?: number;
  center?: number;
  damping?: number;
}

export function simulateForceLayout<N extends ForceNode>(
  nodes: N[],
  edges: ForceEdge[],
  opts: ForceLayoutOptions,
): void {
  const {
    width,
    height,
    iterations = 260,
    repulsion = 2600,
    spring = 0.02,
    springLength = 80,
    center = 0.006,
    damping = 0.82,
  } = opts;
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const cx = width / 2;
  const cy = height / 2;

  for (let iter = 0; iter < iterations; iter++) {
    // Repulsion between every pair — O(n^2), fine for the small node
    // counts every current caller renders (a few dozen at most).
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distSq = dx * dx + dy * dy;
        if (distSq < 1) distSq = 1;
        const force = repulsion / distSq;
        const dist = Math.sqrt(distSq);
        dx = (dx / dist) * force;
        dy = (dy / dist) * force;
        a.vx += dx;
        a.vy += dy;
        b.vx -= dx;
        b.vy -= dy;
      }
    }
    // Spring attraction along edges.
    for (const e of edges) {
      const a = nodes[idx.get(e.from)!];
      const b = nodes[idx.get(e.to)!];
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - springLength) * spring;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }
    // Gentle pull to center so the whole graph doesn't drift off-canvas.
    for (const n of nodes) {
      n.vx += (cx - n.x) * center;
      n.vy += (cy - n.y) * center;
      n.vx *= damping;
      n.vy *= damping;
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  // Clamp into the visible canvas with a margin.
  const margin = 40;
  for (const n of nodes) {
    n.x = Math.max(margin, Math.min(width - margin, n.x));
    n.y = Math.max(margin, Math.min(height - margin, n.y));
  }
}

// Deterministic PRNG so an initial random scatter (and thus the final
// layout) is stable across reloads instead of jumping around on every
// refresh.
export function mulberry32(seed: number) {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Shared trust-status -> color mapping, same vocabulary as StatusBadge/
// styles.css's .badge.* colors, so a graph node's color always means
// the same thing a status badge's color already means elsewhere in
// this app.
export function statusColor(status: string): string {
  switch (status) {
    case "TRUSTED":
      return "var(--green)";
    case "LEARNING":
      return "var(--yellow)";
    case "SUSPECT":
    case "STALE":
      return "var(--orange)";
    default:
      return "var(--gray)";
  }
}
