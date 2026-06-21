import { describe, it, expect } from "vitest";
import { buildCourseView } from "./courseData.js";

describe("buildCourseView", () => {
  const colorByName = { Gub: "#38bdf8", Paul: "#a78bfa" };

  it("builds rows/leader/gap/gifs/on-fire from name-keyed standings", () => {
    const standings = [
      { player: "Paul", ms: 114914 },
      { player: "Gub", ms: 110579 }, // out of order on input -> must be sorted to #1
    ];
    const v = buildCourseView({ standings, colorByName, courseName: "Mario Bros. Circuit", wr: 107414 });
    expect(v.name).toBe("Mario Bros. Circuit");
    expect(v.wr_ms).toBe(107414);
    expect(v.leader).toEqual({ name: "Gub", color: "#38bdf8" });
    expect(v.onFire).toBe(true);
    expect(v.gifUrl).toBe("/players/gub.gif");
    expect(v.fireGifUrl).toBe("/players/gub__fire.gif");
    expect(v.rows[0]).toMatchObject({ rank: 1, name: "Gub", color: "#38bdf8", time_ms: 110579, gap_ms: null });
    expect(v.rows[1]).toMatchObject({ rank: 2, name: "Paul", color: "#a78bfa", gap_ms: 4335 });
  });

  it("falls back to a neutral colour and is calm with no #2 / no WR", () => {
    const v = buildCourseView({ standings: [{ player: "Nobody", ms: 100000 }], colorByName: {}, courseName: "X", wr: null });
    expect(v.leader.color).toBe("#888");
    expect(v.rows[0].color).toBe("#888");
    expect(v.onFire).toBe(false);
    expect(v.wr_ms).toBe(null);
  });
});
