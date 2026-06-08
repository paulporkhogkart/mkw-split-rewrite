// Turn a player's stored "#rrggbb" colour into a Discord embed edge colour that reads well on
// Discord's dark (~#313338) background: clamp it away from near-black and washed-out, and keep
// coloured hues vivid. True greys are left alone (only brightness is clamped).

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  const l = (max + min) / 2;
  let h = 0, s = 0;
  if (d !== 0) {
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h /= 6;
  }
  return [h, s, l];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) { const v = Math.round(l * 255); return [v, v, v]; }
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [
    Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, h) * 255),
    Math.round(hue2rgb(p, q, h - 1 / 3) * 255),
  ];
}

/** "#rrggbb" -> a dark-mode-legible 0xRRGGBB Discord colour, or null for missing/invalid input. */
export function discordColor(hex: string | null | undefined): number | null {
  if (!hex) return null;
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  const [h, s0, l0] = rgbToHsl((n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff);
  const l = Math.min(0.70, Math.max(0.48, l0));       // not near-black, not washed out
  const s = s0 > 0.08 ? Math.max(s0, 0.5) : s0;        // keep coloured hues vivid; leave greys
  const [r, g, b] = hslToRgb(h, s, l);
  return (r << 16) | (g << 8) | b;
}
