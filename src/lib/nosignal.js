// Editor badge label for the NO_SIGNAL node's detection mode.
// mode = { auto: boolean, brand: "elgato" | "ugreen" | null }
const BRAND_LABELS = { elgato: "Elgato", ugreen: "UGREEN" };

export function nosignalBadgeLabel(mode) {
  const m = mode || {};
  if (!m.auto) return "Manual (custom)";
  if (m.brand && BRAND_LABELS[m.brand]) return `Auto · matched ${BRAND_LABELS[m.brand]}`;
  return "Auto · Elgato default (no card match)";
}
