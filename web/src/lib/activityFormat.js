// Pure: an ActivityEvent -> renderable row spans. No Svelte, no DOM, no fetch. Unit-tested.
// Colour is player-identity only; deltas/gaps are neutral; times bright-but-uncoloured.

const ORD = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"];
export const ordinal = (n) => ORD[n] || `${n}th`;

export function fmtTime(ms) {
  if (ms == null) return null;
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), x = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(x).padStart(3, "0")}`;
}

export function signedDelta(ms) {
  if (ms == null) return null;
  return `${ms < 0 ? "-" : "+"}${(Math.abs(ms) / 1000).toFixed(3)}`;
}

export function fmtDuration(ms) {
  const s = Math.round((ms ?? 0) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function relTime(ts, now) {
  const s = Math.max(0, Math.floor((now - ts) / 1000));
  if (s < 45) return "now";
  const m = Math.floor(s / 60); if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

const paren = (s) => (s == null ? "" : `(${s})`);
const nameSpan = (n) => ({ text: n.name, cls: "name", color: n.color });

/** ActivityEvent -> a structured row the component renders span-by-span. */
export function toRow(e, now) {
  const when = relTime(e.ts, now);
  const course = e.course?.name ?? "";
  const p = e.player;
  const pay = e.payload || {};
  const base = { id: e.id, when };

  switch (e.type) {
    case "pb":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: course }, strip: p.color,
        what: [{ text: "PB ", cls: "" }, { text: fmtTime(pay.time_ms), cls: "t" }, { text: " " + paren(signedDelta(pay.delta_ms)), cls: "delta" }] };
    case "rank":
      return { ...base, sys: true, who: { text: "Rank", color: null }, where: { text: course }, strip: null,
        what: [nameSpan(p), { text: ` took ${ordinal(pay.place)} from `, cls: "" }, nameSpan(pay.rival),
               { text: " · ", cls: "dim" }, { text: fmtTime(pay.rival_time_ms), cls: "t" },
               { text: " " + paren(signedDelta(pay.gap_ms)), cls: "delta" }] };
    case "turf_claim":
      return { ...base, sys: true, who: { text: "Turf", color: null }, where: { text: course }, strip: null,
        what: [nameSpan(p), { text: " claimed ", cls: "" }, { text: pay.rival.name + "'s", cls: "name", color: pay.rival.color }, { text: " turf", cls: "" }] };
    case "turf_fire":
      return { ...base, sys: true, who: { text: "Turf", color: null }, where: { text: course }, strip: null,
        what: [{ text: "the people are rallying behind ", cls: "" }, nameSpan(p)] };
    case "turf_waver":
      return { ...base, sys: true, who: { text: "Turf", color: null }, where: { text: course }, strip: null,
        what: [{ text: "the people are losing faith in ", cls: "" }, nameSpan(p)] };
    case "wr":
      return { ...base, sys: true, who: { text: "WR", color: null }, where: { text: course }, strip: null,
        what: [{ text: fmtTime(pay.time_ms), cls: "t" }, { text: " " + paren(signedDelta(pay.delta_ms)), cls: "delta" }, { text: " by " + pay.holder, cls: "dim" }] };
    case "attempts":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: course }, strip: null,
        what: [{ text: `${pay.count} attempts`, cls: "" }, { text: " · " + fmtDuration(pay.duration_ms), cls: "dim" }] };
    case "screen":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: pay.screen, dim: true }, strip: null,
        what: [{ text: fmtDuration(pay.dwell_ms), cls: "dim" }] };
    default:
      return null;
  }
}
