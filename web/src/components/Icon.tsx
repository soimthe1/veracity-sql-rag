/**
 * The redesign's icon set (ai_sql_catalog_redesign/assets/icons/svg), inlined
 * so every icon inherits the surrounding text color (stroke=currentColor)
 * and works in both themes. Generated from the supplied SVGs — same 24x24
 * viewBox and 1.6 stroke; only the hardcoded stroke color was removed.
 */
import type { CSSProperties } from "react";

const PATHS: Record<string, string> = {
  "chart": "<path d=\"M4 20V10M10 20V4M16 20v-7M22 20H2\"/>",
  "clock": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M12 7v5l3 2\"/>",
  "doc": "<path d=\"M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z\"/><path d=\"M14 3v5h5M9 13h6M9 17h6\"/>",
  "mail": "<rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2\"/><path d=\"M3 7l9 6 9-6\"/>",
  "slides": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"13\" rx=\"2\"/><path d=\"M12 17v4M8 21h8M7 9h6M7 12h10\"/>",
  "printer": "<path d=\"M7 9V3h10v6\"/><rect x=\"3\" y=\"9\" width=\"18\" height=\"8\" rx=\"2\"/><path d=\"M7 14h10v7H7z\"/>",
  "more": "<path d=\"M5 12h.01M12 12h.01M19 12h.01\" stroke-width=\"3\"/>",
  "shuffle": "<path d=\"M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5\"/>",
  "wand": "<path d=\"M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8L19 13M15 9h.01M17.8 6.2L19 5M3 21l9-9M12.2 6.2L11 5\"/>",
  "code": "<path d=\"M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14\"/>",
  "activity": "<path d=\"M2 12h4l2-6 4 12 3-9 2 3h5\"/>",
  "alert": "<path d=\"M12 3L2 21h20z\"/><path d=\"M12 9v5M12 18h.01\"/>",
  "book": "<path d=\"M4 5.5c2.5-1.7 5.2-1.7 8 0v14c-2.8-1.7-5.5-1.7-8 0z\"/><path d=\"M20 5.5c-2.5-1.7-5.2-1.7-8 0v14c2.8-1.7 5.5-1.7 8 0z\"/>",
  "brain": "<path d=\"M9 4a4 4 0 00-4 4v1a4 4 0 00-2 3.5A4.5 4.5 0 007.5 17H9V4zm6 0a4 4 0 014 4v1a4 4 0 012 3.5 4.5 4.5 0 01-4.5 4.5H15V4z\"/><path d=\"M9 8H7M15 8h2M9 13H7M15 13h2\"/>",
  "bulb": "<path d=\"M9 18h6M10 22h4\"/><path d=\"M8 14c-1.8-1.3-3-3.4-3-5.8A7 7 0 0112 1a7 7 0 017 7.2c0 2.4-1.2 4.5-3 5.8-1 .8-1.5 1.5-1.5 2.5h-5C9.5 15.5 9 14.8 8 14z\"/>",
  "check": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M8 12l2.5 2.5L16 9\"/>",
  "chevron": "<path d=\"M9 5l7 7-7 7\"/>",
  "copy": "<rect x=\"8\" y=\"8\" width=\"12\" height=\"12\" rx=\"2\"/><path d=\"M5 16H4a2 2 0 01-2-2V4a2 2 0 012-2h10a2 2 0 012 2v1\"/>",
  "database": "<ellipse cx=\"12\" cy=\"5\" rx=\"8\" ry=\"3\"/><path d=\"M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5\"/><path d=\"M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6\"/>",
  "download": "<path d=\"M12 3v12M7 10l5 5 5-5M4 20h16\"/>",
  "filter": "<path d=\"M3 5h18l-7 8v6l-4 2v-8z\"/>",
  "gear": "<circle cx=\"12\" cy=\"12\" r=\"3\"/><path d=\"M19 13.5v-3l-2-.7a6 6 0 00-.8-1.8l.9-1.9-2.2-2.2-1.9.9a6 6 0 00-1.8-.8L10.5 2h-3l-.7 2a6 6 0 00-1.8.8l-1.9-.9L1 6.1 1.9 8a6 6 0 00-.8 1.8l-2 .7v3l2 .7a6 6 0 00.8 1.8L1 17.9l2.1 2.2 1.9-.9a6 6 0 001.8.8l.7 2h3l.7-2a6 6 0 001.8-.8l1.9.9 2.2-2.2-.9-1.9a6 6 0 00.8-1.8z\" transform=\"translate(2) scale(.85)\"/>",
  "globe": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18\"/>",
  "key": "<circle cx=\"8\" cy=\"12\" r=\"4\"/><path d=\"M12 12h10M18 12v3M15 12v2\"/>",
  "list": "<path d=\"M8 6h13M8 12h13M8 18h13\"/><circle cx=\"3\" cy=\"6\" r=\"1\"/><circle cx=\"3\" cy=\"12\" r=\"1\"/><circle cx=\"3\" cy=\"18\" r=\"1\"/>",
  "lock": "<rect x=\"4\" y=\"10\" width=\"16\" height=\"11\" rx=\"2\"/><path d=\"M8 10V7a4 4 0 018 0v3\"/>",
  "moon": "<path d=\"M20 15a8 8 0 01-11-11 9 9 0 1011 11z\"/>",
  "network": "<circle cx=\"6\" cy=\"6\" r=\"3\"/><circle cx=\"18\" cy=\"6\" r=\"3\"/><circle cx=\"12\" cy=\"18\" r=\"3\"/><path d=\"M8.6 7.5l6.8 0M7.5 8.6l3 6M16.5 8.6l-3 6\"/>",
  "overview": "<rect x=\"3\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"3\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/><rect x=\"14\" y=\"14\" width=\"7\" height=\"7\" rx=\"1\"/>",
  "refresh": "<path d=\"M20 7V3l-3 3a8 8 0 10 1 11\"/>",
  "search": "<circle cx=\"10\" cy=\"10\" r=\"6\"/><path d=\"M14.5 14.5L21 21\"/>",
  "shield": "<path d=\"M12 2l8 3v6c0 5-3.2 8.7-8 11-4.8-2.3-8-6-8-11V5z\"/><path d=\"M8 12l2.5 2.5L16 9\"/>",
  "sparkle": "<path d=\"M12 2l1.7 4.3L18 8l-4.3 1.7L12 14l-1.7-4.3L6 8l4.3-1.7z\"/><path d=\"M19 14l.9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9z\"/>",
  "table": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"2\"/><path d=\"M3 9h18M8 4v16M15 4v16\"/>",
  "users": "<circle cx=\"9\" cy=\"8\" r=\"4\"/><path d=\"M2 21c.5-4 3-6 7-6s6.5 2 7 6\"/><circle cx=\"18\" cy=\"9\" r=\"3\"/><path d=\"M16 15c3.5.2 5.5 2 6 5\"/>",
  // Not in the supplied set, drawn in the same 24px / 1.6-stroke style for
  // controls the app already had (sign out, info toggle, dismiss).
  "logout": "<path d=\"M9 4H6a2 2 0 00-2 2v12a2 2 0 002 2h3\"/><path d=\"M14 17l5-5-5-5M19 12H9\"/>",
  "info": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M12 11v5M12 8h.01\"/>",
  "x": "<path d=\"M6 6l12 12M18 6L6 18\"/>",
};

export type IconName = keyof typeof PATHS;

export default function Icon({ name, size = 18, strokeWidth = 1.6, className, style }: {
  name: IconName | string;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: PATHS[name] ?? "" }}
    />
  );
}
