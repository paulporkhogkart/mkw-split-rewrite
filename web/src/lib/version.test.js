import { describe, it, expect } from "vitest";
import { compareSemver, status, formatLastSeen, componentRows, playerRows } from "./version.js";

describe("compareSemver / status", () => {
  it("compares numerically and tolerates a leading v; null when unparseable", () => {
    expect(compareSemver("2.2.0", "2.10.0")).toBe(-1);
    expect(compareSemver("v2.1.0", "2.1.0")).toBe(0);
    expect(compareSemver("dev", "2.1.0")).toBeNull();
  });
  it("maps a comparison to a status word", () => {
    expect(status("2.1.0", "2.1.0")).toBe("current");
    expect(status("2.0.0", "2.1.0")).toBe("behind");
    expect(status("2.2.0", "2.1.0")).toBe("ahead");
    expect(status(null, "2.1.0")).toBe("unknown");
    expect(status("2.1.0", null)).toBe("unknown");
  });
});

describe("formatLastSeen", () => {
  it("renders online / relative / never", () => {
    const now = 1_000_000_000;
    expect(formatLastSeen(now - 5_000, now)).toBe("online");      // < 60s
    expect(formatLastSeen(now - 5 * 60_000, now)).toBe("5m ago");
    expect(formatLastSeen(now - 3 * 3_600_000, now)).toBe("3h ago");
    expect(formatLastSeen(now - 2 * 86_400_000, now)).toBe("2d ago");
    expect(formatLastSeen(null, now)).toBe("never");
  });
});

const payload = {
  latest: { tag: "2.1.5", app: "2.1.0", fetched_at: 0, errors: [] },
  deployed: { server: { version: "2.1.5", booted_at: 0 }, bot: { version: "2.1.4", booted_at: 0 } },
  players: [
    { player_id: 1, name: "Paul", color: "#a78bfa", app_version: "2.1.0", last_seen_at: 1_000_000_000 },
    { player_id: 2, name: "Gub", color: "#38bdf8", app_version: "2.0.0", last_seen_at: 900_000_000 },
    { player_id: 3, name: "Aliias", color: null, app_version: null, last_seen_at: null },
  ],
};

describe("componentRows", () => {
  it("builds app/server/bot/site rows with statuses; site uses the passed bundle version", () => {
    const rows = componentRows(payload, "2.1.5");
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by.server.status).toBe("current");
    expect(by.bot.status).toBe("behind");           // 2.1.4 < 2.1.5
    expect(by.site).toMatchObject({ deployed: "2.1.5", status: "current" });
    expect(by.app).toMatchObject({ latest: "2.1.0", summary: "1/2 on latest" });  // Paul on, Gub off, Aliias unreported
  });
});

describe("playerRows", () => {
  it("maps each player to installed version, last-seen, and status", () => {
    const rows = playerRows(payload, 1_000_000_000);
    expect(rows.find((r) => r.name === "Paul")).toMatchObject({ app_version: "2.1.0", last_seen: "online", status: "current" });
    expect(rows.find((r) => r.name === "Gub").status).toBe("behind");
    expect(rows.find((r) => r.name === "Aliias")).toMatchObject({ app_version: null, last_seen: "never", status: "unknown" });
  });
});
