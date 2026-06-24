// Pure: a normalized store row (see activityMerge.js) -> renderable row spans. No Svelte/DOM/fetch.
// Colour is player-identity only; deltas/gaps are neutral; times bright-but-uncoloured.

import { chipsFor } from "./chips.js";

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

/** A session duration as a ticking clock: m:ss, or h:mm:ss past an hour (long grinds / menu sits). */
export function fmtClock(ms) {
  const t = Math.max(0, Math.floor((ms ?? 0) / 1000));
  const s = t % 60, m = Math.floor(t / 60) % 60, h = Math.floor(t / 3600);
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
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

/** An absolute log timestamp in the viewer's local time, single line: "25 Jun 14:32:05". */
export function fmtStamp(ts) {
  const d = new Date(ts);
  const date = d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  return `${date} ${time}`;
}

const paren = (s) => (s == null ? "" : `(${s})`);
const nameSpan = (n) => ({ text: n.name, cls: "name", color: n.color });

const OFFTRACK_LABEL = {
  menus: "in the menus", character_select: "choosing a character",
  kart_select: "choosing a kart", track_select: "choosing a track", ghost: "watching a ghost",
};
const runsLabel = (n) => `${n} run${n === 1 ? "" : "s"}`;

/** A normalized store row -> a structured render row the component draws span-by-span. */
export function toRow(row, now) {
  const out = row.kind === "session" ? sessionRow(row, now) : milestoneRow(row, now);
  return out ? { ...out, chips: chipsFor(row) } : out;
}

function sessionRow(row, now) {
  // soft: sessions wear a gentler player-name colour than the PB row (the feed's one highlight).
  const base = { id: row.key, when: fmtStamp(row.started_ts), sys: false, strip: null, soft: true };
  const p = row.player;
  const who = p ? { text: p.name, color: p.color } : { text: "", color: null };
  const dur = row.live ? now - row.started_ts : row.duration_ms ?? 0;

  if (row.cls === "racing") {
    const where = { text: row.course?.name ?? "" };
    if (row.live) {
      const what = [{ text: "racing ", cls: "" }, { text: fmtClock(dur), cls: "t" }];
      if (row.attempts >= 1) what.push({ text: ` · ${runsLabel(row.attempts)}`, cls: "dim" });
      return { ...base, who, where, what };
    }
    // Finalised grind: just the run count + duration. The course is already in `where`, and the PB
    // (if any) has its own row, so neither the character nor the PB outcome is repeated here.
    return { ...base, who, where, what: [
      { text: runsLabel(row.attempts ?? 0), cls: "" },
      { text: ` · ${fmtClock(dur)}`, cls: "dim" },
    ] };
  }

  // Off-track: the activity phrase sits in `where` (dim), the duration in `what`.
  return { ...base, who, where: { text: OFFTRACK_LABEL[row.cls] ?? "in the menus", dim: true },
    what: [{ text: fmtClock(dur), cls: "dim" }] };
}

function milestoneRow(row, now) {
  const e = row.event;
  const when = fmtStamp(e.ts);
  const course = e.course?.name ?? "";
  const p = e.player;
  const pay = e.payload || {};
  const base = { id: row.key, when };

  switch (e.type) {
    case "pb":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: course }, strip: p.color,
        what: [{ text: "pb ", cls: "" }, { text: fmtTime(pay.time_ms), cls: "t" }, { text: " " + paren(signedDelta(pay.delta_ms)), cls: "delta" }] };
    case "rank":
      return { ...base, sys: true, who: { text: "rank", color: null }, where: { text: course }, strip: null,
        what: [nameSpan(p), { text: ` took ${ordinal(pay.place)} from `, cls: "" }, nameSpan(pay.rival),
               { text: " · ", cls: "dim" }, { text: fmtTime(pay.rival_time_ms), cls: "t" },
               { text: " " + paren(signedDelta(pay.gap_ms)), cls: "delta" }] };
    case "turf_claim":
      return { ...base, sys: true, who: { text: "turf", color: null }, where: { text: course }, strip: null,
        what: [nameSpan(p), { text: " claimed ", cls: "" }, { text: pay.rival.name + "'s", cls: "name", color: pay.rival.color }, { text: " turf", cls: "" }] };
    case "turf_fire":
      return { ...base, sys: true, who: { text: "turf", color: null }, where: { text: course }, strip: null,
        what: [{ text: "the people are rallying behind ", cls: "" }, nameSpan(p)] };
    case "turf_waver":
      return { ...base, sys: true, who: { text: "turf", color: null }, where: { text: course }, strip: null,
        what: [{ text: "the people are losing faith in ", cls: "" }, nameSpan(p)] };
    case "wr":
      return { ...base, sys: true, who: { text: "wr", color: null }, where: { text: course }, strip: null,
        what: [{ text: fmtTime(pay.time_ms), cls: "t" }, { text: " " + paren(signedDelta(pay.delta_ms)), cls: "delta" }, { text: " by " + pay.holder, cls: "dim" }] };
    case "presence":
      // App open/close: a soft player-name row, the action in the `where` slot like an off-track session.
      return { ...base, sys: false, soft: true, who: { text: p.name, color: p.color }, strip: null,
        where: { text: pay.online ? "logged in" : "logged out", dim: true }, what: [] };
    default:
      return null;
  }
}
