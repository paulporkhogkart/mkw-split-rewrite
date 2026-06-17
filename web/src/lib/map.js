// Pure helpers for the World Map. Assets live in web/public/map/ (served at /map/*).
const MAP_DIR = "/map";
export const manifestUrl = () => `${MAP_DIR}/manifest.json`;
export const baseUrl = () => `${MAP_DIR}/base.jpg`;
export const spriteUrl = (slug) => `${MAP_DIR}/sprites/${slug}.png`;

const pct = (v) => (v * 100).toFixed(3) + "%";

// Absolute placement of a course hit box, as % of the map frame.
export function hitStyle(hit) {
  return `left:${pct(hit.x)};top:${pct(hit.y)};width:${pct(hit.w)};height:${pct(hit.h)}`;
}

// The sprite image is placed RELATIVE to its hit box, so a CSS :hover on the hit
// can transform the child. hit/spr are both normalized to the frame.
export function spriteStyle(hit, spr) {
  const l = (spr.x - hit.x) / hit.w, t = (spr.y - hit.y) / hit.h;
  return `left:${pct(l)};top:${pct(t)};width:${pct(spr.w / hit.w)};height:${pct(spr.h / hit.h)}`;
}
