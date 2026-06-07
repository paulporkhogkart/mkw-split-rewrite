import { describe, it, expect } from "vitest";
import { playerColor, playerCfg, activeConfig, rankOpacity, buildTrailRuns, trailLegendRows, TRAIL_PRESETS } from "./trailSettings.js";

const roster = [
  { player_id: 1, display_name: "Paul", is_me: true },
  { player_id: 2, display_name: "Luke", is_me: false },
  { player_id: 3, display_name: "Alex", is_me: false },
];

describe("trailSettings helpers", () => {
  it("playerColor: server colour wins, then named assignment, then per-id preset", () => {
    expect(playerColor({ display_name: "Paul", player_id: 1, color: "#112233" })).toBe("#112233");  // server-curated wins
    expect(playerColor({ display_name: "Paul", player_id: 1 })).toBe("#9b6bd0");   // named (purple)
    expect(playerColor({ display_name: "Luke", player_id: 2 })).toBe("#cf5b4e");   // red
    expect(playerColor({ display_name: "Stranger", player_id: 8 })).toBe(TRAIL_PRESETS[0]);   // fallback
  });

  it("playerCfg defaults to last_pb (n=49 for me, 24 for others); stored overrides", () => {
    expect(playerCfg({ players: {} }, roster[0])).toEqual({ mode: "last_pb", n: 49 });
    expect(playerCfg({ players: {} }, roster[1])).toEqual({ mode: "last_pb", n: 24 });
    expect(playerCfg({ players: { 2: { mode: "best", n: 10 } } }, roster[1])).toEqual({ mode: "best", n: 10 });
  });

  it("activeConfig drops none and emits {player_id,mode,n} (others default)", () => {
    const s = { players: { 1: { mode: "last", n: 100 }, 2: { mode: "none" } } };
    expect(activeConfig(s, roster)).toEqual([
      { player_id: 1, mode: "last", n: 100 },
      { player_id: 3, mode: "last_pb", n: 24 },
    ]);
  });

  it("rankOpacity fades 1→floor; full when off or single", () => {
    expect(rankOpacity(0, 5, true)).toBe(1);
    expect(rankOpacity(4, 5, true)).toBe(0.2);
    expect(rankOpacity(3, 5, false)).toBe(1);
    expect(rankOpacity(0, 1, true)).toBe(1);
  });

  it("buildTrailRuns: locked colour by player; PB full-opacity + flagged; reset abandoned", () => {
    const s = { fadeByRank: true, players: {} };
    const reads = { trails: [
      { player_id: 2, status: "finished", is_pb: false, points: [[0, 1, 1, 1]] },   // rank 0
      { player_id: 2, status: "finished", is_pb: true,  points: [[0, 2, 2, 1]] },   // rank 1 (PB)
      { player_id: 2, status: "reset",    is_pb: false, points: [[0, 3, 3, 1]] },   // rank 2 (reset)
    ] };
    const out = buildTrailRuns(reads, s, roster);
    const luke = playerColor(roster[1]);   // Luke = red
    expect(out.map((r) => r.color)).toEqual([luke, luke, luke]);
    // Paint order (last = on top): faded rank-2 reset at the bottom, rank-0 above it, PB on top
    // - PBs always sit above non-PB dots, and fainter runs sit lower.
    expect(out[0]).toMatchObject({ is_pb: false, abandoned: true, opacity: 0.2 });   // rank 2 (reset)
    expect(out[1]).toMatchObject({ is_pb: false, abandoned: false, opacity: 1 });    // rank 0
    expect(out[2]).toMatchObject({ is_pb: true, abandoned: false, opacity: 1 });     // PB on top
  });

  it("buildTrailRuns z-order: global intermingle - PBs on top (fastest highest), fainter lower", () => {
    const s = { fadeByRank: true, players: {} };
    // points[0][1] tags each run so we can read the output (paint) order back.
    const reads = { trails: [
      { player_id: 2, status: "finished", is_pb: true,  total_ms: 100000, points: [[0, 1, 0, 1]] }, // p2 PB (fastest overall)
      { player_id: 2, status: "finished", is_pb: false, total_ms: 105000, points: [[0, 2, 0, 1]] }, // p2 rank 1 (op 0.6)
      { player_id: 2, status: "finished", is_pb: false, total_ms: 110000, points: [[0, 3, 0, 1]] }, // p2 rank 2 (op 0.2)
      { player_id: 3, status: "finished", is_pb: true,  total_ms: 102000, points: [[0, 4, 0, 1]] }, // p3 PB
      { player_id: 3, status: "finished", is_pb: false, total_ms: 108000, points: [[0, 5, 0, 1]] }, // p3 rank 1 (op 0.2)
    ] };
    const out = buildTrailRuns(reads, s, roster);
    // Bottom -> top: faint non-PBs first (3, then 5, then 2 - by opacity, ties broken faster-higher),
    // then both PBs above all non-PBs, sorted by time (4 below, fastest 1 on top). PBs intermingle
    // above every non-PB regardless of player; player colour never forms a layer.
    expect(out.map((r) => r.points[0][1])).toEqual([3, 5, 2, 4, 1]);
  });

  it("trailLegendRows lists active players with colour + mode", () => {
    const s = { players: { 2: { mode: "none" } } };
    expect(trailLegendRows(s, roster).map((r) => [r.name, r.mode, r.color])).toEqual([
      ["Paul", "last_pb", playerColor(roster[0])],
      ["Alex", "last_pb", playerColor(roster[2])],
    ]);
  });
});
