import { describe, it, expect } from "vitest";
import { screen, selection, race, minimap } from "./stores.js";
import { resets } from "./resets.js";
import { serverUrl, authToken } from "./syncSettings.js";
import { frame, wsUrl } from "./presence.js";

describe("presence frame()", () => {
  it("maps the live stores into a frame", () => {
    screen.set("RACING");
    selection.set({ char: "Mario", costume: "Base", kart: "Std", course: "Bowsers Castle" });
    race.set({ curLap: 2, totLap: 3, coins: 7, mushrooms: 1, splits: {}, finishTime: null, elapsedMs: 5000 });
    minimap.set({ cx: 12, cy: 34, radius: 5, trackState: "tracking", roi: [0, 0, 1, 1] });
    resets.set(4);
    expect(frame()).toEqual({
      screen: "RACING", course: "Bowsers Castle", character: "Mario", kart: "Std", costume: "Base",
      cur_lap: 2, tot_lap: 3, coins: 7, mushrooms: 1, pos: [12, 34], final_time: null, resets: 4,
      track_state: "tracking", elapsed_ms: 5000,
    });
  });
  it("pos is null with no minimap fix", () => {
    minimap.set(null);
    expect(frame().pos).toBeNull();
    minimap.set({ cx: null, cy: null, radius: 0, trackState: null, roi: null });
    expect(frame().pos).toBeNull();   // a cleared-but-present store must not send [null,null]
  });
});

describe("presence wsUrl()", () => {
  it("converts http(s) base to ws(s) and appends the token", () => {
    serverUrl.set("http://127.0.0.1:8787/");
    authToken.set("abc");
    expect(wsUrl()).toBe("ws://127.0.0.1:8787/v1/presence?token=abc");
    serverUrl.set("https://srv.example.com");
    expect(wsUrl()).toBe("wss://srv.example.com/v1/presence?token=abc");
  });
  it("is null when no server is configured", () => {
    serverUrl.set("");
    expect(wsUrl()).toBeNull();
  });
});
