import { describe, it, expect } from "vitest";
import { toRow, fmtTime, signedDelta, fmtDuration, fmtClock, relTime, ordinal } from "./activityFormat.js";
import { rowFromEvent, rowFromSession } from "./activityMerge.js";

const ev = (type, over = {}) => ({
  id: 1, ts: 1000, type,
  course: { slug: "crown_city", name: "Crown City" },
  player: { id: 1, name: "Gub", color: "#38bdf8" },
  payload: {}, ...over,
});
const mrow = (type, over) => toRow(rowFromEvent(ev(type, over)), 1000);

const sw = (over = {}) => ({
  session_id: 3, state: "open", started_ts: 1000,
  player: { id: 1, name: "Gub", color: "#38bdf8" }, course: { slug: "cc", name: "Choco Mountain" },
  cls: "racing", character: "Peach", costume: "Base", ended_ts: null, duration_ms: null,
  attempts: 0, pbs: 0, ...over,
});
const srow = (over, now = 1000) => toRow(rowFromSession(sw(over)), now);

describe("helpers", () => {
  it("fmtTime → m:ss.SSS", () => expect(fmtTime(107980)).toBe("1:47.980"));
  it("signedDelta → signed 3dp", () => { expect(signedDelta(-430)).toBe("-0.430"); expect(signedDelta(1118)).toBe("+1.118"); });
  it("fmtDuration → compact", () => { expect(fmtDuration(40000)).toBe("40s"); expect(fmtDuration(7 * 60000)).toBe("7m"); });
  it("fmtClock → m:ss / h:mm:ss", () => {
    expect(fmtClock(42000)).toBe("0:42"); expect(fmtClock(24 * 60000 + 13000)).toBe("24:13"); expect(fmtClock(3661000)).toBe("1:01:01");
  });
  it("relTime → terse", () => { expect(relTime(1000, 1000)).toBe("now"); expect(relTime(1000, 1000 + 120000)).toBe("2m"); });
  it("ordinal", () => { expect(ordinal(1)).toBe("1st"); expect(ordinal(11)).toBe("11th"); });
});

describe("toRow milestones", () => {
  it("pb → player row with colour strip + neutral delta, evt key", () => {
    const r = mrow("pb", { payload: { time_ms: 107980, time_str: "1:47.980", delta_ms: -430 } });
    expect(r.sys).toBe(false);
    expect(r.who).toEqual({ text: "Gub", color: "#38bdf8" });
    expect(r.strip).toBe("#38bdf8");
    expect(r.id).toBe("evt:1");
    expect(r.what.map((s) => s.text).join("")).toBe("pb 1:47.980 (-0.430)");
  });
  it("rank → system tag, coloured mover+rival, ordinal + gap", () => {
    const r = mrow("rank", { player: { id: 1, name: "Paul", color: "#a78bfa" }, payload: {
      place: 2, rival_id: 3, rival_name: "Aliias", rival_time_ms: 116420, gap_ms: 1118,
      rival: { id: 3, name: "Aliias", color: "#4ade80" } } });
    expect(r.sys).toBe(true);
    expect(r.what.map((s) => s.text).join("")).toBe("Paul took 2nd from Aliias · 1:56.420 (+1.118)");
  });
  it("turf_claim wording", () => {
    expect(mrow("turf_claim", { payload: { rival_id: 2, rival: { id: 2, name: "Paul", color: "#a78bfa" } } })
      .what.map((s) => s.text).join("")).toBe("Gub claimed Paul's turf");
  });
  it("turf_fire / turf_waver wording", () => {
    expect(mrow("turf_fire").what.map((s) => s.text).join("")).toBe("the people are rallying behind Gub");
    expect(mrow("turf_waver").what.map((s) => s.text).join("")).toBe("the people are losing faith in Gub");
  });
  it("wr → grey tag, neutral delta, 'by holder'", () => {
    const r = mrow("wr", { player: null, payload: { time_ms: 89180, time_str: "1:29.180", holder: "Ralph", delta_ms: -220 } });
    expect(r.who.text).toBe("wr");
    expect(r.what.map((s) => s.text).join("")).toBe("1:29.180 (-0.220) by Ralph");
  });
});

describe("toRow sessions", () => {
  it("racing live → player, course, ticking clock + run count, no strip", () => {
    const r = srow({ attempts: 12 }, 1000 + 42000);   // 42s in
    expect(r.sys).toBe(false);
    expect(r.strip).toBe(null);
    expect(r.who).toEqual({ text: "Gub", color: "#38bdf8" });
    expect(r.where.text).toBe("Choco Mountain");
    expect(r.what.map((s) => s.text).join("")).toBe("racing 0:42 · 12 runs");
    expect(r.what.find((s) => s.cls === "t").text).toBe("0:42");
  });
  it("racing live with no attempts yet → just the clock", () => {
    expect(srow({ attempts: 0 }, 1000 + 8000).what.map((s) => s.text).join("")).toBe("racing 0:08");
  });
  it("racing final → 'as {char} · N runs · dur · outcome'", () => {
    const r = srow({ state: "final", ended_ts: 1000 + 24 * 60000 + 13000, duration_ms: 24 * 60000 + 13000, attempts: 67, pbs: 0 });
    expect(r.what.map((s) => s.text).join("")).toBe("as Peach · 67 runs · 24:13 · no PB");
  });
  it("racing final with a PB → 'new PB'", () => {
    expect(srow({ state: "final", duration_ms: 60000, attempts: 5, pbs: 1 }).what.map((s) => s.text).join("")).toContain("new PB");
  });
  it("a single run is singular", () => {
    expect(srow({ state: "final", duration_ms: 30000, attempts: 1, pbs: 0 }).what.map((s) => s.text).join("")).toBe("as Peach · 1 run · 0:30 · no PB");
  });
  it("menus → activity phrase in where (dim), duration in what", () => {
    const r = srow({ cls: "menus", character: null, costume: null, course: null, state: "final", duration_ms: 23 * 60000 }, 1000);
    expect(r.where).toEqual({ text: "in the menus", dim: true });
    expect(r.what.map((s) => s.text).join("")).toBe("23:00");
  });
  it("character_select / ghost labels", () => {
    expect(srow({ cls: "character_select", course: null, state: "final", duration_ms: 40000 }, 1000).where.text).toBe("choosing a character");
    expect(srow({ cls: "ghost", course: null, state: "final", duration_ms: 40000 }, 1000).where.text).toBe("watching a ghost");
  });
});
