import { RelationshipOut } from "../api";
import { useI18n } from "../i18n";
import { statusColor } from "../lib/forceGraph";

/**
 * Entity-centered ER diagram, laid out in columns rather than a ring:
 *
 *   tables that point TO this one  →  [ this table ]  →  tables it points to
 *
 * Neighbors are stacked vertically on their side, so boxes can never
 * overlap however many there are (the old ring put up to 10 boxes, each
 * 168px wide, on a ~115px radius — they sat on top of each other). Several
 * relationships to the same neighbor merge into one box listing each join.
 * Capped at MAX_NODES neighbors, strongest (TRUSTED / declared FK / most
 * confident) first; the table view has the full list.
 */
const MAX_NODES = 12;
const NODE_W = 190;
const NODE_H = 46;
const GAP = 14;
const CENTER_W = 210;
const CENTER_H = 60;
const W = 900;

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

interface Neighbor {
  id: string;
  name: string;
  side: "in" | "out";
  status: string;
  declared: boolean;
  joins: string[];
  score: number;
}

const STATUS_RANK: Record<string, number> = { TRUSTED: 3, LEARNING: 2, SUSPECT: 1 };

export default function EntityDiagram({
  center,
  relationships,
  onSelectEntity,
}: {
  center: { id: string; name: string };
  relationships: RelationshipOut[];
  onSelectEntity?: (id: string) => void;
}) {
  const { t } = useI18n();

  if (relationships.length === 0) {
    return <div className="empty">{t("entityDiagram.noneYet")}</div>;
  }

  // One box per (neighbor, direction); keep the strongest relationship's
  // status for its color and list every join field pair.
  const byKey = new Map<string, Neighbor>();
  for (const r of relationships) {
    const out = r.from_entity_id === center.id;
    const id = out ? r.to_entity_id : r.from_entity_id;
    const name = out ? r.to_entity_name : r.from_entity_name;
    const key = `${out ? "out" : "in"}:${id}`;
    const score = (STATUS_RANK[r.status] ?? 0) * 2 + (r.is_declared_fk ? 1 : 0) + r.confidence;
    const join = `${r.from_field ?? "?"} → ${r.to_field ?? "?"}`;
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, { id, name, side: out ? "out" : "in", status: r.status, declared: r.is_declared_fk, joins: [join], score });
    } else {
      existing.joins.push(join);
      if (score > existing.score) {
        existing.score = score;
        existing.status = r.status;
        existing.declared = existing.declared || r.is_declared_fk;
      }
    }
  }
  const ranked = [...byKey.values()].sort((a, b) => b.score - a.score);
  const shown = ranked.slice(0, MAX_NODES);
  const rest = ranked.length - shown.length;
  const left = shown.filter((n) => n.side === "in");
  const right = shown.filter((n) => n.side === "out");

  const rows = Math.max(left.length, right.length, 1);
  const H = Math.max(CENTER_H + 60, rows * (NODE_H + GAP) - GAP + 60);
  const cx = W / 2;
  const cy = H / 2;
  const leftX = 20;
  const rightX = W - 20 - NODE_W;

  function columnY(count: number, i: number): number {
    const total = count * (NODE_H + GAP) - GAP;
    return (H - total) / 2 + i * (NODE_H + GAP);
  }

  const placed = [
    ...left.map((n, i) => ({ n, x: leftX, y: columnY(left.length, i) })),
    ...right.map((n, i) => ({ n, x: rightX, y: columnY(right.length, i) })),
  ];

  return (
    <div>
      <div style={{ overflowX: "auto" }}>
        <svg direction="ltr" viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", margin: "0 auto", maxWidth: W, minWidth: 560 }}>
          <defs>
            <marker id="ed-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" style={{ fill: "var(--text-faint)" }} />
            </marker>
          </defs>
          {left.length > 0 && (
            <text x={leftX + NODE_W / 2} y={16} textAnchor="middle" fontSize={10.5} style={{ fill: "var(--text-faint)" }}>
              {t("entityDiagram.pointsHere")}
            </text>
          )}
          {right.length > 0 && (
            <text x={rightX + NODE_W / 2} y={16} textAnchor="middle" fontSize={10.5} style={{ fill: "var(--text-faint)" }}>
              {t("entityDiagram.pointsTo")}
            </text>
          )}

          {placed.map(({ n, x, y }) => {
            const isLeft = n.side === "in";
            const nodeEdgeX = isLeft ? x + NODE_W : x;
            const nodeMidY = y + NODE_H / 2;
            const centerEdgeX = isLeft ? cx - CENTER_W / 2 : cx + CENTER_W / 2;
            const midX = (nodeEdgeX + centerEdgeX) / 2;
            // Arrow always points from the referencing table to the referenced one.
            const d = isLeft
              ? `M${nodeEdgeX},${nodeMidY} C${midX},${nodeMidY} ${midX},${cy} ${centerEdgeX},${cy}`
              : `M${centerEdgeX},${cy} C${midX},${cy} ${midX},${nodeMidY} ${nodeEdgeX},${nodeMidY}`;
            return (
              <path
                key={`e-${n.side}-${n.id}`}
                d={d}
                fill="none"
                style={{ stroke: statusColor(n.status) }}
                strokeWidth={n.status === "TRUSTED" ? 2 : 1.4}
                strokeDasharray={n.declared ? undefined : "5,4"}
                opacity={0.8}
                markerEnd="url(#ed-arrow)"
              />
            );
          })}

          {placed.map(({ n, x, y }) => (
            <g
              key={`n-${n.side}-${n.id}`}
              transform={`translate(${x}, ${y})`}
              style={{ cursor: onSelectEntity ? "pointer" : "default" }}
              onClick={onSelectEntity ? () => onSelectEntity(n.id) : undefined}
            >
              <title>{`${n.name}\n${n.joins.join("\n")}`}</title>
              <rect width={NODE_W} height={NODE_H} rx={9} style={{ fill: "var(--panel)", stroke: statusColor(n.status) }} strokeWidth={1.5} />
              <text x={10} y={18} fontSize={11.5} fontWeight={600} style={{ fill: "var(--text)" }}>
                {truncate(n.name, 26)}
              </text>
              <text x={10} y={34} fontSize={9.5} fontFamily="ui-monospace, Consolas, monospace" style={{ fill: "var(--text-dim)" }}>
                {truncate(n.joins[0], 30)}
                {n.joins.length > 1 ? ` +${n.joins.length - 1}` : ""}
              </text>
              <circle cx={NODE_W - 12} cy={12} r={4} style={{ fill: statusColor(n.status) }}>
                <title>
                  {t(`status.${n.status}`)}
                  {n.declared ? t("entityDiagram.declaredFk") : t("entityDiagram.inferred")}
                </title>
              </circle>
            </g>
          ))}

          <g transform={`translate(${cx - CENTER_W / 2}, ${cy - CENTER_H / 2})`}>
            <rect width={CENTER_W} height={CENTER_H} rx={10} style={{ fill: "var(--accent-soft)", stroke: "var(--accent)" }} strokeWidth={2} />
            <text x={CENTER_W / 2} y={26} textAnchor="middle" fontSize={12.5} fontWeight={700} style={{ fill: "var(--text)" }}>
              {truncate(center.name, 26)}
            </text>
            <text x={CENTER_W / 2} y={44} textAnchor="middle" fontSize={9.5} style={{ fill: "var(--text-dim)" }}>
              {t("entityDiagram.relationshipsShown", { n: shown.length, plural: shown.length === 1 ? "" : "s" })}
            </text>
          </g>
        </svg>
      </div>
      <div className="muted" style={{ textAlign: "center", fontSize: 11.5, marginTop: 8 }}>
        {t("entityDiagram.legend")}
        {rest > 0 && <> · {t("entityDiagram.moreNotShown", { n: rest, plural: rest === 1 ? "" : "s" })}</>}
      </div>
    </div>
  );
}
