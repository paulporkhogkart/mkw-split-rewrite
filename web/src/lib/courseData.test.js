import { describe, it, expect } from "vitest";
import { buildCourseView } from "./courseData.js";

const lb = [
  { player_id: 1, display_name: "Gub",   total_time_ms: 110579, total_time_str: "1:50.579", rank: 1 },
  { player_id: 2, display_name: "Paul",  total_time_ms: 114914, total_time_str: "1:54.914", rank: 2 },
];
const colorById = { 1: "#38bdf8", 2: "#a78bfa" };

describe("buildCourseView", () => {
  it("assembles leader, rows, gap-to-#1 and gif urls", () => {
    const v = buildCourseView({ rows: lb, wr: { record_ms: 107414 }, colorById, courseName: "Mario Bros. Circuit" });
    expect(v.name).toBe("Mario Bros. Circuit");
    expect(v.wr_ms).toBe(107414);
    expect(v.leader).toEqual({ name: "Gub", color: "#38bdf8" });
    expect(v.onFire).toBe(true);
    expect(v.gifUrl).toBe("/players/gub.gif");
    expect(v.fireGifUrl).toBe("/players/gub__fire.gif");
    expect(v.rows[0]).toMatchObject({ rank: 1, name: "Gub", color: "#38bdf8", time_str: "1:50.579", gap_ms: null });
    expect(v.rows[1]).toMatchObject({ rank: 2, name: "Paul", color: "#a78bfa", gap_ms: 4335 });
  });

  it("is calm and gif-only when there is no WR or no #2", () => {
    const v = buildCourseView({ rows: [lb[0]], wr: null, colorById, courseName: "X" });
    expect(v.onFire).toBe(false);
    expect(v.wr_ms).toBe(null);
    expect(v.rows).toHaveLength(1);
  });

  it("falls back to a neutral colour when the roster has none", () => {
    const v = buildCourseView({ rows: lb, wr: null, colorById: {}, courseName: "X" });
    expect(v.leader.color).toBe("#888");
    expect(v.rows[0].color).toBe("#888");
  });
});
