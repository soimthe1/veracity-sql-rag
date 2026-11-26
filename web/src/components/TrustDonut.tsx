/**
 * Pure-SVG donut chart for the Overview "Trust states" panel — the
 * visual counterpart to the clickable numeric row already there (kept
 * alongside it, not replacing it, since the numbers are the click
 * target for jumping into Entities). Same "no charting library" choice
 * as EntityDiagram.tsx: five arcs around a circle is simple geometry,
 * not worth a dependency.
 */
const SEGMENTS: { key: "TRUSTED" | "LEARNING" | "SUSPECT" | "UNKNOWN" | "STALE"; color: string }[] = [
  { key: "TRUSTED", color: "var(--green)" },
  { key: "LEARNING", color: "var(--yellow)" },
  { key: "SUSPECT", color: "var(--orange)" },
  { key: "UNKNOWN", color: "var(--gray)" },
  { key: "STALE", color: "var(--red)" },
];

function arcPath(cx: number, cy: number, rOuter: number, rInner: number, startAngle: number, endAngle: number): string {
  const a0 = startAngle - Math.PI / 2;
  const a1 = endAngle - Math.PI / 2;
  const large = endAngle - startAngle > Math.PI ? 1 : 0;
  const p = (r: number, a: number) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x0, y0] = p(rOuter, a0);
  const [x1, y1] = p(rOuter, a1);
  const [x2, y2] = p(rInner, a1);
  const [x3, y3] = p(rInner, a0);
  return `M ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${large} 1 ${x1} ${y1} L ${x2} ${y2} A ${rInner} ${rInner} 0 ${large} 0 ${x3} ${y3} Z`;
}

export default function TrustDonut({
  counts,
  size = 128,
  onSelect,
  unitLabel,
}: {
  counts: Record<string, number>;
  size?: number;
  onSelect?: (status: string) => void;
  unitLabel: string;
}) {
  const total = SEGMENTS.reduce((n, s) => n + (counts[s.key] ?? 0), 0);
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = size / 2 - 4;
  const rInner = rOuter * 0.6;

  if (total === 0) {
    return (
      <svg width={size} height={size}>
        <circle cx={cx} cy={cy} r={(rOuter + rInner) / 2} fill="none" stroke="var(--panel-border)" strokeWidth={rOuter - rInner} />
      </svg>
    );
  }

  let angle = 0;
  const arcs = SEGMENTS.map((s) => {
    const value = counts[s.key] ?? 0;
    const frac = value / total;
    const start = angle;
    const end = angle + frac * 2 * Math.PI;
    angle = end;
    return { ...s, value, start, end, frac };
  }).filter((a) => a.value > 0);

  return (
    <svg width={size} height={size} style={{ display: "block" }}>
      {arcs.map((a) => (
        <path
          key={a.key}
          d={arcPath(cx, cy, rOuter, rInner, a.start, a.end)}
          style={{ fill: a.color, cursor: onSelect ? "pointer" : "default" }}
          opacity={0.92}
          onClick={onSelect ? () => onSelect(a.key) : undefined}
        >
          <title>
            {a.key}: {a.value} ({Math.round(a.frac * 100)}%)
          </title>
        </path>
      ))}
      <text x={cx} y={cy - 3} textAnchor="middle" fontSize={18} fontWeight={700} style={{ fill: "var(--text)" }}>
        {total}
      </text>
      <text x={cx} y={cy + 13} textAnchor="middle" fontSize={9} style={{ fill: "var(--text-faint)" }}>
        {unitLabel}
      </text>
    </svg>
  );
}
