import { describe, it, expect } from "vitest";
import { pbLapDurations, localLapInfo, buildSelfEntry } from "./localSelf.js";

describe("pbLapDurations", () => {
  it("differences cumulative PB splits into per-lap durations", () => {
    expect(pbLapDurations({ 1: 36000, 2: 72000, 3: 108000 })).toEqual([36000, 36000, 36000]);
  });
  it("handles uneven laps + null/empty", () => {
    expect(pbLapDurations({ 1: 40000, 2: 72000 })).toEqual([40000, 32000]);
    expect(pbLapDurations(null)).toBeNull();
    expect(pbLapDurations({})).toBeNull();
  });
});

describe("localLapInfo", () => {
  const pbCum = { 1: 36000, 2: 72000, 3: 108000 };   // 3 laps @ 36s each

  it("computes per-lap durations + LiveSplit deltas for completed laps", () => {
    // live: lap1 35.5s (ahead), lap2 36.5s (lost this segment, back to even)
    const r = localLapInfo({ 1: "0:35.500", 2: "0:36.500" }, pbCum);
    expect(r.pb_laps_ms).toEqual([36000, 36000, 36000]);
    expect(r.lap_deltas).toEqual([
      { lap: 1, delta_ms: -500, seg_delta_ms: -500, gained: true,  gold: false },
      { lap: 2, delta_ms: 0,    seg_delta_ms: 500,  gained: false, gold: false },
    ]);
  });
  it("only emits deltas for contiguous completed laps", () => {
    expect(localLapInfo({}, pbCum).lap_deltas).toEqual([]);
    expect(localLapInfo({ 2: "0:36.000" }, pbCum).lap_deltas).toEqual([]);   // lap 1 missing
  });
  it("no PB → null pb_laps_ms + null deltas", () => {
    expect(localLapInfo({ 1: "0:35.000" }, null)).toEqual({ pb_laps_ms: null, lap_deltas: null });
  });
});

describe("buildSelfEntry", () => {
  const state = {
    identity: { player_id: 1, name: "Paul", color: "#9b6bd0" },
    screen: "RACING",
    selection: { char: "Mario", costume: "Base", kart: "Std", course: "Rainbow Road" },
    race: { curLap: 2, totLap: 3, coins: 7, mushrooms: 1, finishTime: null, elapsedMs: 45000,
            splits: { 1: "0:35.500" } },
    minimap: { cx: 12, cy: 34 },
    resets: 4,
    pbTotalMs: 108000,
    pbCum: { 1: 36000, 2: 72000, 3: 108000 },
    now: 5000,
  };

  it("maps local stores into a renderable presence entry tagged _localSelf", () => {
    const e = buildSelfEntry(state);
    expect(e).toMatchObject({
      player_id: 1, name: "Paul", color: "#9b6bd0", online: true, screen: "RACING",
      character: "Mario", costume: "Base", kart: "Std", course: "Rainbow Road",
      cur_lap: 2, tot_lap: 3, resets: 4, elapsed_ms: 45000, final_time: null,
      pb_ms: 108000, pos: [12, 34], has_model: false, _localSelf: true, updated_at: 5000,
    });
    expect(e.pb_laps_ms).toEqual([36000, 36000, 36000]);
    expect(e.lap_deltas.length).toBe(1);
  });
  it("pos is null without a minimap fix", () => {
    expect(buildSelfEntry({ ...state, minimap: null }).pos).toBeNull();
    expect(buildSelfEntry({ ...state, minimap: { cx: null, cy: null } }).pos).toBeNull();
  });
  it("carries the dnf (timeout) flag so our own card shows DNF offline", () => {
    expect(buildSelfEntry(state).dnf).toBe(false);
    expect(buildSelfEntry({ ...state, race: { ...state.race, dnf: true } }).dnf).toBe(true);
  });
});
