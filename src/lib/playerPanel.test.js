import { describe, it, expect } from "vitest";
import { connectionChip, emptyState } from "./playerPanel.js";

describe("connectionChip", () => {
  it("is Live when connected", () => {
    expect(connectionChip({ connected: true, syncedAt: 1000 }, 5000)).toEqual({ tier: "live", label: "Live" });
  });
  it("is Offline with the last-sync age when disconnected but synced before", () => {
    expect(connectionChip({ connected: false, syncedAt: 1000 }, 1000 + 120000))
      .toEqual({ tier: "offline", label: "Offline · last sync 2m ago" });
  });
  it("is Not connected when never synced", () => {
    expect(connectionChip({ connected: false, syncedAt: null }, 5000)).toEqual({ tier: "none", label: "Not connected" });
  });
});

describe("emptyState", () => {
  it("guides to Settings when no server is configured", () => {
    expect(emptyState(false)).toEqual({ title: "No player data yet.", hint: "Connect a season server in Settings › Sync." });
  });
  it("waits for the server when configured", () => {
    expect(emptyState(true)).toEqual({ title: "No player data yet.", hint: "Waiting for the season server…" });
  });
});
