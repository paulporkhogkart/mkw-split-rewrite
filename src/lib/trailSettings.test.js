import { describe, it, expect } from "vitest";
import { playerColor, playerCfg, activeConfig, rankOpacity, buildTrailRuns, trailLegendRows, TRAIL_PRESETS, wrCfg, bandOf, WR_COLOR } from "./trailSettings.js";

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

  it("trailLegendRows lists active players with colour + mode, plus the default-on WR row", () => {
    const s = { players: { 2: { mode: "none" } } };
    expect(trailLegendRows(s, roster).map((r) => [r.name, r.mode, r.color])).toEqual([
      ["Paul", "last_pb", playerColor(roster[0])],
      ["Alex", "last_pb", playerColor(roster[2])],
      ["WR", "current", WR_COLOR],
    ]);
  });
});

describe("WR trails (the WR is one more grey player; spec 2026-07-18)", () => {
  // Wire rows as the Pi serves them: fastest-first, is_current 0/1 integers.
  const wrRows = [
    { wr_id: 9, holder_name: "JaK", record_ms: 62934, is_current: 1, points: [[0, 90, 0, 1]] },
    { wr_id: 7, holder_name: "Old", record_ms: 64000, is_current: 0, points: [[0, 91, 0, 1]] },
  ];

  it("wrCfg defaults to current; unknown stored values fall back to current", () => {
    expect(wrCfg({})).toEqual({ mode: "current" });
    expect(wrCfg(undefined)).toEqual({ mode: "current" });
    expect(wrCfg({ wr: { mode: "off" } })).toEqual({ mode: "off" });
    expect(wrCfg({ wr: { mode: "all" } })).toEqual({ mode: "all" });
    expect(wrCfg({ wr: { mode: "garbage" } })).toEqual({ mode: "current" });
  });

  it("mode current shows only the current WR; all shows history too; off shows none", () => {
    const reads = { trails: [], wr_trails: wrRows };
    expect(buildTrailRuns(reads, { players: {}, wr: { mode: "current" } }, roster)
      .map((r) => r.points[0][1])).toEqual([90]);
    // all: historic band sits below the current WR's band.
    expect(buildTrailRuns(reads, { players: {}, wr: { mode: "all" } }, roster)
      .map((r) => r.points[0][1])).toEqual([91, 90]);
    expect(buildTrailRuns(reads, { players: {}, wr: { mode: "off" } }, roster)).toEqual([]);
  });

  it("the current WR is grey, pulses like a PB, and paints directly UNDER player PBs", () => {
    const reads = {
      trails: [
        { player_id: 2, status: "finished", is_pb: true,  total_ms: 100000, points: [[0, 1, 0, 1]] },
        { player_id: 2, status: "finished", is_pb: false, total_ms: 110000, points: [[0, 2, 0, 1]] },
      ],
      wr_trails: [wrRows[0]],
    };
    const out = buildTrailRuns(reads, { players: {}, wr: { mode: "current" } }, roster);
    // Bottom -> top: player ghost, current WR, player PB. The WR yields to players
    // within its rank (decided 2026-07-18, supersedes the earlier above-PBs call).
    expect(out.map((r) => r.points[0][1])).toEqual([2, 90, 1]);
    expect(out[1]).toMatchObject({ color: WR_COLOR, is_pb: true, abandoned: false, wr: "current" });
  });

  it("historic WRs sort under all alive player past runs and obey the fade toggle", () => {
    const reads = {
      trails: [{ player_id: 2, status: "finished", is_pb: false, total_ms: 110000, points: [[0, 2, 0, 1]] }],
      wr_trails: wrRows,
    };
    const out = buildTrailRuns(reads, { fadeByRank: true, players: {}, wr: { mode: "all" } }, roster);
    // historic WR < alive player past run < current WR
    expect(out.map((r) => r.points[0][1])).toEqual([91, 2, 90]);
    // Fade parity: the historic row is index 1 of the fastest-first WR set, exactly
    // like a player's non-PB run at rank 1 of 2 (no special dimming anywhere).
    expect(out[0].opacity).toBe(rankOpacity(1, 2, true));
    expect(out[0]).toMatchObject({ is_pb: false, wr: "historic", color: WR_COLOR });
  });

  it("two-tier band formula: every alive run outranks every abandoned one", () => {
    // Paul's canonical impossible case: a dead current WR sits under an alive player past run.
    expect(bandOf({ wr: "current", is_pb: true, abandoned: true }))
      .toBeLessThan(bandOf({ wr: null, is_pb: false, abandoned: false }));
    expect(bandOf({ wr: null, is_pb: false, abandoned: true }))
      .toBeLessThan(bandOf({ wr: "historic", is_pb: false, abandoned: false }));
    // Within a tier: historic WR < player past run < current WR < player PB.
    expect([
      bandOf({ wr: "historic", is_pb: false, abandoned: false }),
      bandOf({ wr: null, is_pb: false, abandoned: false }),
      bandOf({ wr: "current", is_pb: true, abandoned: false }),
      bandOf({ wr: null, is_pb: true, abandoned: false }),
    ]).toEqual([4, 5, 6, 7]);
    // The abandoned tier mirrors it 4 lower.
    expect(bandOf({ wr: null, is_pb: false, abandoned: true })).toBe(1);
    expect(bandOf({ wr: null, is_pb: true, abandoned: true })).toBe(3);
  });

  it("a stale cached payload without wr_trails yields no WR runs and no crash", () => {
    const reads = { trails: [{ player_id: 2, status: "finished", is_pb: false, points: [[0, 2, 0, 1]] }] };
    const out = buildTrailRuns(reads, { players: {}, wr: { mode: "current" } }, roster);
    expect(out).toHaveLength(1);
    expect(out[0].wr).toBe(null);
  });

  it("legend gains a grey WR row iff mode != off", () => {
    const rows = trailLegendRows({ players: {}, wr: { mode: "all" } }, roster);
    expect(rows[rows.length - 1]).toMatchObject({ name: "WR", color: WR_COLOR, mode: "all" });
    expect(trailLegendRows({ players: {}, wr: { mode: "off" } }, roster)
      .map((r) => r.name)).not.toContain("WR");
  });

  it("an abandoned run sorts below an alive ghost even when less faded (tier beats opacity)", () => {
    const reads = { trails: [
      { player_id: 2, status: "reset",    is_pb: false, total_ms: null,   points: [[0, 7, 0, 1]] },  // rank 0 -> opacity 1, abandoned
      { player_id: 2, status: "finished", is_pb: false, total_ms: 110000, points: [[0, 8, 0, 1]] },  // rank 1 -> opacity 0.2, alive
    ] };
    const out = buildTrailRuns(reads, { fadeByRank: true, players: {}, wr: { mode: "off" } }, roster);
    // The old opacity-first sort painted [8, 7]; the two-tier hierarchy puts the
    // abandoned run under the alive one regardless of fade (spec 2026-07-18).
    expect(out.map((r) => r.points[0][1])).toEqual([7, 8]);
    expect(out[0]).toMatchObject({ abandoned: true, opacity: 1 });
    expect(out[1]).toMatchObject({ abandoned: false, opacity: 0.2 });
  });
});
