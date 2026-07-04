import { describe, it, expect } from "vitest";
import { withTheoretical } from "./courseSplits.js";

describe("withTheoretical", () => {
  it("sums each player's best laps; null when any lap is missing", () => {
    const splits = {
      laps: 3,
      perPlayer: [
        { player_id: 1, display_name: "Paul", color: "#f00", best: [39000, 34000, 35000] },
        { player_id: 2, display_name: "Luke", color: "#0f0", best: [38000, null, 35000] },
      ],
      fieldIdeal: [38000, 34000, 35000],
    };
    const r = withTheoretical(splits);
    expect(r.perPlayer[0].theoretical).toBe(108000);
    expect(r.perPlayer[1].theoretical).toBeNull();
    expect(r.fieldIdealTotal).toBe(107000);
  });

  it("field ideal total is null when a lap is unset", () => {
    expect(withTheoretical({ laps: 2, perPlayer: [], fieldIdeal: [40000, null] }).fieldIdealTotal).toBeNull();
  });
});
