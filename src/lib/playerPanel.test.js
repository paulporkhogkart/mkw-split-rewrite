import { describe, it, expect } from "vitest";
import { emptyState } from "./playerPanel.js";

describe("emptyState", () => {
  it("guides to Settings when no server is configured", () => {
    expect(emptyState(false)).toEqual({ title: "No player data yet.", hint: "Connect a season server in Settings › Sync." });
  });
  it("waits for the server when configured", () => {
    expect(emptyState(true)).toEqual({ title: "No player data yet.", hint: "Waiting for the season server…" });
  });
});
