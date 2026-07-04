import { describe, it, expect } from "vitest";
import { overallBoard } from "./overallBoard.js";

describe("overallBoard", () => {
  it("sums each player's per-track bests, fastest total first, with a track count", () => {
    const boards = [
      { standings: [{ player: "Paul", ms: 110000 }, { player: "Luke", ms: 108000 }] },
      { standings: [{ player: "Paul", ms: 90000 }] },
    ];
    const r = overallBoard(boards);
    expect(r).toEqual([
      { player: "Luke", total_ms: 108000, tracks: 1 },
      { player: "Paul", total_ms: 200000, tracks: 2 },
    ]);
  });
});
