import { describe, it, expect } from "vitest";
import { playerCfg, activeConfig, rankOpacity, buildTrailRuns, trailLegendRows, TRAIL_PRESETS } from "./trailSettings.js";

const roster = [
  { player_id: 1, display_name: "Paul" },
  { player_id: 2, display_name: "Luke" },
  { player_id: 3, display_name: "Alex" },
];

describe("trailSettings helpers", () => {
  it("playerCfg defaults to pbs / n=100 / preset colour by index; honours stored", () => {
    expect(playerCfg({ players: {} }, 1, 0)).toEqual({ mode: "pbs", n: 100, color: TRAIL_PRESETS[0] });
    expect(playerCfg({ players: { 2: { mode: "last", n: 50, color: "#fff" } } }, 2, 1)).toEqual({ mode: "last", n: 50, color: "#fff" });
    expect(playerCfg({ players: {} }, 3, 9).color).toBe(TRAIL_PRESETS[9 % TRAIL_PRESETS.length]);
  });

  it("activeConfig drops none and emits {player_id,mode,n}", () => {
    const s = { fadeByRank: true, players: { 1: { mode: "last", n: 100 }, 2: { mode: "none" }, 3: { mode: "best", n: 10 } } };
    expect(activeConfig(s, roster)).toEqual([
      { player_id: 1, mode: "last", n: 100 },
      { player_id: 3, mode: "best", n: 10 },
    ]);
  });

  it("rankOpacity fades 1→floor; full when off or single", () => {
    expect(rankOpacity(0, 5, true)).toBe(1);
    expect(rankOpacity(4, 5, true)).toBe(0.2);
    expect(rankOpacity(2, 5, true)).toBe(0.6);
    expect(rankOpacity(3, 5, false)).toBe(1);
    expect(rankOpacity(0, 1, true)).toBe(1);
  });

  it("buildTrailRuns groups by player, applies colour + rank fade", () => {
    const s = { fadeByRank: true, players: { 1: { mode: "last", n: 3, color: "#blue" } } };
    const reads = { trails: [{ player_id: 1, points: [[0, 1, 1, 1]] }, { player_id: 1, points: [[0, 2, 2, 1]] }] };
    expect(buildTrailRuns(reads, s, roster)).toEqual([
      { points: [[0, 1, 1, 1]], color: "#blue", opacity: 1 },
      { points: [[0, 2, 2, 1]], color: "#blue", opacity: 0.2 },
    ]);
  });

  it("trailLegendRows lists active players with name/colour/mode", () => {
    const s = { fadeByRank: true, players: { 1: { mode: "last", n: 100, color: "#a" }, 2: { mode: "none" } } };
    expect(trailLegendRows(s, roster).map((r) => [r.name, r.mode])).toEqual([["Paul", "last"], ["Alex", "pbs"]]);
  });
});
