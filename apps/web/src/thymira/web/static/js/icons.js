// Inline SVG icons. The console ships no icon font and fetches no sprite sheet: every glyph below
// is a handful of line segments on a 24x24 grid, drawn with createElementNS so nothing is ever
// parsed as HTML. `currentColor` keeps them on the tone of whatever element holds them.

const SVG_NS = "http://www.w3.org/2000/svg";

// A full circle as two arcs, so every shape below is a plain path.
const circle = (cx, cy, r) => `M${cx} ${cy - r}a${r} ${r} 0 1 0 0 ${r * 2}a${r} ${r} 0 1 0 0 ${-r * 2}`;

const PATHS = {
  plus: ["M12 5v14", "M5 12h14"],
  search: [circle(11, 11, 7), "M16.2 16.2 20.5 20.5"],
  settings: [
    circle(12, 12, 3),
    "M12 2.5v2.2M12 19.3v2.2M4.5 4.5l1.6 1.6M17.9 17.9l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.5 19.5l1.6-1.6M17.9 6.1l1.6-1.6",
  ],
  sun: [circle(12, 12, 4), "M12 2.5v2M12 19.5v2M4.5 4.5 6 6M18 18l1.5 1.5M2.5 12h2M19.5 12h2M4.5 19.5 6 18M18 6l1.5-1.5"],
  moon: ["M20.5 14.8A8.6 8.6 0 0 1 9.2 3.5a8.6 8.6 0 1 0 11.3 11.3z"],
  monitor: ["M3.5 5h17v11h-17z", "M9 20h6", "M12 16v4"],
  sidebar: ["M3.5 4.5h17v15h-17z", "M9.5 4.5v15"],
  panel: ["M3.5 4.5h17v15h-17z", "M14.5 4.5v15"],
  check: ["M4.5 12.5 9.5 17.5 19.5 6.5"],
  x: ["M6 6l12 12", "M18 6 6 18"],
  play: ["M8 5.2 19 12 8 18.8z"],
  pause: ["M9.5 5v14", "M14.5 5v14"],
  square: ["M6.5 6.5h11v11h-11z"],
  alert: ["M12 3.2 1.8 20.8h20.4z", "M12 9.5v4.5", "M12 17.6v.01"],
  info: [circle(12, 12, 9), "M12 11v5.5", "M12 7.6v.01"],
  file: ["M13.5 3.5H7a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8.5z", "M13.5 3.5v5h5"],
  image: ["M3.5 5h17v14h-17z", "M3.5 16.5 8.5 11.5 12.5 15.5 15.5 12.5 20.5 17.5", circle(8.5, 9, 1.3)],
  table: ["M3.5 5h17v14h-17z", "M3.5 10h17", "M9.5 10v9"],
  tool: ["M15 3.2a5 5 0 0 0-4.4 7.2l-7.2 7.2 2.9 2.9 7.2-7.2A5 5 0 1 0 15 3.2z", "M17.6 4.6l2.4 2.4"],
  agent: [circle(12, 8, 3.6), "M4.8 20.2a7.4 7.4 0 0 1 14.4 0"],
  shield: ["M12 3 20 6v5.6c0 4.8-3.3 7.9-8 9.4-4.7-1.5-8-4.6-8-9.4V6z"],
  thread: ["M7 4v9.5a4 4 0 0 0 4 4h5.5", "M14 14.5l3 3-3 3", circle(7, 4, 1.4)],
  copy: ["M9.5 8.5h10v11h-10z", "M5.5 15.5h-1a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"],
  "chevron-down": ["M6 9.5 12 15.5 18 9.5"],
  "chevron-right": ["M9.5 6 15.5 12 9.5 18"],
  refresh: ["M20.4 12a8.4 8.4 0 1 1-2.5-6", "M20.4 3.6v4.8h-4.8"],
  external: ["M14 3.6h6.4V10", "M20.4 3.6 11.2 12.8", "M18 14v5.4a1 1 0 0 1-1 1H4.6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1H10"],
  terminal: ["M3.5 5h17v14h-17z", "M7.6 10 11 12.5 7.6 15", "M13 15.4h4"],
  database: [
    "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z",
    "M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6",
    "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  ],
  clock: [circle(12, 12, 9), "M12 6.8V12l3.6 2.1"],
  send: ["M20.6 3.4 3.4 10.6l7.4 2.6 2.6 7.4z", "M10.8 13.2 20.6 3.4"],
  "arrow-down": ["M12 4.5v14", "M6 12.5 12 18.5 18 12.5"],
  command: ["M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"],
  circle: [circle(12, 12, 9)],
};

export const ICON_NAMES = Object.keys(PATHS);

export function icon(name, { size = 16, className = null, title = null } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("class", ["icon", className].filter(Boolean).join(" "));
  if (title) {
    const label = document.createElementNS(SVG_NS, "title");
    label.textContent = title;
    svg.append(label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  svg.setAttribute("focusable", "false");
  for (const d of PATHS[name] ?? PATHS.circle) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}
