import { describe, it, expect } from "vitest";
import { computePresence, UNCHANGED } from "./discordPayload.js";

const base = {
  screen: "RACING", course: "Rainbow Road", character: "Mario", kart: "Pipe Frame",
  resets: 12, curLap: 3, totLap: 3,
  playerSplits: { 1: 40000, 2: 80000 }, pbSplits: { 1: 40420, 2: 80310, 3: 120000 },
  finalTime: null, isNewPb: false, twitchUrl: "",
};

describe("computePresence", () => {
  it("idle -> penguin", () => {
    const p = computePresence({ ...base, screen: "UNKNOWN" });
    expect(p.large_image).toBe("penguin");
    expect(p.small_image).toBeUndefined();
    expect(p.details).toBe("Idle");
  });

  it("character select -> penguin + text", () => {
    const p = computePresence({ ...base, screen: "CHARACTER_SELECT" });
    expect(p.large_image).toBe("penguin");
    expect(p.details).toBe("Choosing a character");
  });

  it("course select -> 'Choosing a track'", () => {
    expect(computePresence({ ...base, screen: "COURSE_SELECT" }).details).toBe("Choosing a track");
  });

  it("unmapped menu -> In the menus", () => {
    expect(computePresence({ ...base, screen: "MAIN_MENU" }).details).toBe("In the menus");
  });

  it("ignore screens -> UNCHANGED", () => {
    for (const s of ["RACE_MENU", "RESET", "GHOST_RESET", "UNKNOWN_RESET", "HOME"])
      expect(computePresence({ ...base, screen: s })).toBe(UNCHANGED);
  });

  it("racing lap 3 with PB -> delta from last completed lap", () => {
    const p = computePresence(base); // last completed lap = 2: 80000-80310 = -310
    expect(p.large_image).toBe("rainbow_road");
    expect(p.small_image).toBe("penguin");
    expect(p.details).toBe("Rainbow Road · 12 resets");
    expect(p.state).toBe("Lap 3/3 · 0.310s ahead of PB");
  });

  it("racing lap 1 -> character / kart (no delta yet)", () => {
    const p = computePresence({ ...base, curLap: 1, playerSplits: {} });
    expect(p.state).toBe("Lap 1/3 · Mario · Pipe Frame");
  });

  it("racing with no PB -> character / kart", () => {
    const p = computePresence({ ...base, pbSplits: null });
    expect(p.state).toBe("Lap 3/3 · Mario · Pipe Frame");
  });

  it("singular reset", () => {
    expect(computePresence({ ...base, resets: 1 }).details).toBe("Rainbow Road · 1 reset");
  });

  it("ghost -> course art + Watching a ghost", () => {
    const p = computePresence({ ...base, screen: "GHOST" });
    expect(p.large_image).toBe("rainbow_road");
    expect(p.small_image).toBe("penguin");
    expect(p.details).toBe("Rainbow Road");
    expect(p.state).toBe("Watching a ghost");
  });

  it("results new PB", () => {
    const p = computePresence({ ...base, screen: "POST_TIME_TRIAL", finalTime: "1:57.812", isNewPb: true });
    expect(p.details).toBe("Rainbow Road · finished");
    expect(p.state).toBe("1:57.812 · New personal best");
  });

  it("results not a PB -> delta vs PB total", () => {
    const p = computePresence({ ...base, screen: "POST_TIME_TRIAL", finalTime: "2:00.500", isNewPb: false });
    expect(p.state).toBe("2:00.500 · 0.500s behind PB"); // 120500 - 120000
  });

  it("twitch url adds a button on racing", () => {
    const p = computePresence({ ...base, twitchUrl: "https://twitch.tv/me" });
    expect(p.button_label).toBe("Watch on Twitch");
    expect(p.button_url).toBe("https://twitch.tv/me");
  });
});
