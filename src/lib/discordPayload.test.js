import { describe, it, expect } from "vitest";
import { computePresence, UNCHANGED } from "./discordPayload.js";

const base = {
  screen: "RACING", course: "Rainbow Road", character: "Mario", kart: "Pipe Frame",
  resets: 12, curLap: 3, totLap: 3,
  playerSplits: { 1: 40000, 2: 80000 }, pbSplits: { 1: 40420, 2: 80310, 3: 120000 },
  finalTime: null, pbTotalMs: 120000, twitchUrl: "", twitchButtonEnabled: true, twitchLabel: "Watch on Twitch",
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

  it("start time trial -> 'Starting time trial'", () => {
    expect(computePresence({ ...base, screen: "START_TIME_TRIAL" }).details).toBe("Starting time trial");
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

  it("final time on RACING -> finished card (course - time / Finished delta)", () => {
    const p = computePresence({ ...base, finalTime: "1:59.600" });
    expect(p.details).toBe("Rainbow Road · 1:59.600");
    expect(p.state).toBe("Finished 0.400s ahead of PB"); // 119600 - 120000
  });

  it("POST_TIME_TRIAL renders the same finished card (no-op transition)", () => {
    const p = computePresence({ ...base, screen: "POST_TIME_TRIAL", finalTime: "2:00.500" });
    expect(p.details).toBe("Rainbow Road · 2:00.500");
    expect(p.state).toBe("Finished 0.500s behind PB"); // 120500 - 120000
  });

  it("finished with no PB -> Finished + character / kart", () => {
    const p = computePresence({ ...base, finalTime: "2:00.500", pbTotalMs: null });
    expect(p.details).toBe("Rainbow Road · 2:00.500");
    expect(p.state).toBe("Finished · Mario · Pipe Frame");
  });

  it("twitch button shows on racing when enabled + url set", () => {
    const p = computePresence({ ...base, twitchUrl: "https://twitch.tv/me" });
    expect(p.button_label).toBe("Watch on Twitch");
    expect(p.button_url).toBe("https://twitch.tv/me");
  });

  it("twitch button shows on non-racing states too (menus, setup)", () => {
    const menu = computePresence({ ...base, screen: "MAIN_MENU", twitchUrl: "https://twitch.tv/me" });
    expect(menu.button_url).toBe("https://twitch.tv/me");
    const setup = computePresence({ ...base, screen: "CHARACTER_SELECT", twitchUrl: "https://twitch.tv/me" });
    expect(setup.button_url).toBe("https://twitch.tv/me");
  });

  it("no button on idle even with a url", () => {
    const p = computePresence({ ...base, screen: "UNKNOWN", twitchUrl: "https://twitch.tv/me" });
    expect(p.button_url).toBeUndefined();
  });

  it("no button when the button toggle is off, even with a url", () => {
    const p = computePresence({ ...base, twitchUrl: "https://twitch.tv/me", twitchButtonEnabled: false });
    expect(p.button_url).toBeUndefined();
  });

  it("custom button label", () => {
    const p = computePresence({ ...base, twitchUrl: "https://twitch.tv/me", twitchLabel: "Join my stream!" });
    expect(p.button_label).toBe("Join my stream!");
  });
});
