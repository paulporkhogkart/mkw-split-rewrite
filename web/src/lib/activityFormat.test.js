import { describe, it, expect } from "vitest";
import { toRow, fmtTime, signedDelta, fmtDuration, relTime, ordinal } from "./activityFormat.js";

const ev = (type, over = {}) => ({
  id: 1, ts: 1000, type,
  course: { slug: "crown_city", name: "Crown City" },
  player: { id: 1, name: "Gub", color: "#38bdf8" },
  payload: {}, ...over,
});

describe("helpers", () => {
  it("fmtTime → m:ss.SSS", () => expect(fmtTime(107980)).toBe("1:47.980"));
  it("signedDelta → signed 3dp", () => { expect(signedDelta(-430)).toBe("-0.430"); expect(signedDelta(1118)).toBe("+1.118"); });
  it("fmtDuration → compact", () => { expect(fmtDuration(40000)).toBe("40s"); expect(fmtDuration(7*60000)).toBe("7m"); expect(fmtDuration(64*60000)).toBe("1h 4m"); });
  it("relTime → terse", () => { const now = 1000 + 0; expect(relTime(1000, now)).toBe("now"); expect(relTime(1000, 1000 + 120000)).toBe("2m"); expect(relTime(1000, 1000 + 2*3600000)).toBe("2h"); });
  it("ordinal", () => { expect(ordinal(1)).toBe("1st"); expect(ordinal(2)).toBe("2nd"); expect(ordinal(11)).toBe("11th"); });
});

describe("toRow", () => {
  it("pb → player row with colour strip + neutral delta", () => {
    const r = toRow(ev("pb", { payload: { time_ms: 107980, time_str: "1:47.980", delta_ms: -430 } }), 1000);
    expect(r.sys).toBe(false);
    expect(r.who).toEqual({ text: "Gub", color: "#38bdf8" });
    expect(r.strip).toBe("#38bdf8");
    expect(r.what.map(s => s.text).join("")).toBe("PB 1:47.980 (-0.430)");
    expect(r.what.find(s => s.cls === "t").text).toBe("1:47.980");
  });
  it("rank → system tag, coloured mover+rival, ordinal + gap", () => {
    const r = toRow(ev("rank", { player: { id: 1, name: "Paul", color: "#a78bfa" }, payload: {
      place: 2, rival_id: 3, rival_name: "Aliias", rival_time_ms: 116420, gap_ms: 1118,
      rival: { id: 3, name: "Aliias", color: "#4ade80" } } }), 1000);
    expect(r.sys).toBe(true);
    expect(r.who.text).toBe("Rank");
    const names = r.what.filter(s => s.cls === "name");
    expect(names.map(s => [s.text, s.color])).toEqual([["Paul", "#a78bfa"], ["Aliias", "#4ade80"]]);
    expect(r.what.map(s => s.text).join("")).toBe("Paul took 2nd from Aliias · 1:56.420 (+1.118)");
  });
  it("turf_claim → 'X claimed Y\\'s turf'", () => {
    const r = toRow(ev("turf_claim", { player: { id: 1, name: "Gub", color: "#38bdf8" }, payload: {
      rival_id: 2, rival: { id: 2, name: "Paul", color: "#a78bfa" } } }), 1000);
    expect(r.sys).toBe(true);
    expect(r.what.map(s => s.text).join("")).toBe("Gub claimed Paul's turf");
  });
  it("turf_fire / turf_waver wording", () => {
    expect(toRow(ev("turf_fire"), 1000).what.map(s => s.text).join("")).toBe("the people are rallying behind Gub");
    expect(toRow(ev("turf_waver"), 1000).what.map(s => s.text).join("")).toBe("the people are losing faith in Gub");
  });
  it("wr → grey tag, neutral delta, dimmed 'by holder', null player", () => {
    const r = toRow(ev("wr", { player: null, payload: { time_ms: 89180, time_str: "1:29.180", holder: "Ralph", delta_ms: -220 } }), 1000);
    expect(r.sys).toBe(true);
    expect(r.who.text).toBe("WR");
    expect(r.what.map(s => s.text).join("")).toBe("1:29.180 (-0.220) by Ralph");
  });
  it("attempts → 'N attempts · dur'", () => {
    const r = toRow(ev("attempts", { payload: { count: 19, duration_ms: 7*60000 } }), 1000);
    expect(r.sys).toBe(false);
    expect(r.what.map(s => s.text).join("")).toBe("19 attempts · 7m");
  });
  it("screen → screen label in `where`, dwell in `what`, null course", () => {
    const r = toRow(ev("screen", { course: null, payload: { screen: "character select", dwell_ms: 40000 } }), 1000);
    expect(r.where).toEqual({ text: "character select", dim: true });
    expect(r.what.map(s => s.text).join("")).toBe("40s");
  });
});
