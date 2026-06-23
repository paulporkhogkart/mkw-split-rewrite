// Editor badge label for the NO_SIGNAL node's detection mode.
// mode = { auto: boolean, brand: "elgato" | "ugreen" | "obs" | null }
const BRAND_LABELS = { elgato: "Elgato", ugreen: "UGREEN", obs: "OBS Virtual Camera" };

export function nosignalBadgeLabel(mode) {
  const m = mode || {};
  if (!m.auto) return "Manual (custom)";
  if (m.brand && BRAND_LABELS[m.brand]) return `Auto · matched ${BRAND_LABELS[m.brand]}`;
  return "Auto · Elgato default (no card match)";
}
