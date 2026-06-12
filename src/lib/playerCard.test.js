import { describe, it, expect, beforeEach } from "vitest";
import { viewModel, lastSeen, pbDelta, liveDelta, lapDeltaVm, fmtTimeMs, charName, clearHolds } from "./playerCard.js";

const base = { player_id: 1, name: "Paul", color: "#a78bfa", online: true, screen: "RACING",
  course: "Rainbow Road", character: "Mario", kart: "Standard", cur_lap: 2, tot_lap: 3,
  coins: 7, mushrooms: 2, resets: 3, pb_ms: 79880, completion: 0.63, dividers: [0.33, 0.67],
  final_time: null, updated_at: 1000 };

describe("fmtTimeMs", () => {
  it("formats ms as m:ss.SSS", () => {
    expect(fmtTimeMs(79880)).toBe("1:19.880");
    expect(fmtTimeMs(2044)).toBe("0:02.044");
    expect(fmtTimeMs(null)).toBeNull();
  });
});

describe("lastSeen", () => {
  it("buckets a delta", () => {
    expect(lastSeen(5000)).toBe("just now");
    expect(lastSeen(120000)).toBe("2m ago");
    expect(lastSeen(3 * 3600000)).toBe("3h ago");
    expect(lastSeen(2 * 86400000)).toBe("2d ago");
  });
});

describe("pbDelta", () => {
  it("signs the delta vs PB in the sharp LiveSplit shades", () => {
    expect(pbDelta("1:21.044", 79880)).toEqual({ text: "+1.164", cls: "behind-loss" });
    expect(pbDelta("1:18.880", 79880)).toEqual({ text: "-1.000", cls: "ahead-gain" });
    expect(pbDelta(null, 79880)).toBeNull();
  });
});

describe("charName", () => {
  it("puts the costume before the character; Base/none is just the character", () => {
    expect(charName("Toad", "Burger Bud")).toBe("Burger Bud Toad");
    expect(charName("Toad", "Base")).toBe("Toad");
    expect(charName("Toad", null)).toBe("Toad");
    expect(charName(null, "Burger Bud")).toBeNull();
  });
});

describe("liveDelta", () => {
  it("maps sign x trend onto the LiveSplit shades (steady = sharp)", () => {
    expect(liveDelta(432)).toEqual({ text: "+0.432", cls: "behind-loss" });
    expect(liveDelta(432, "gain")).toEqual({ text: "+0.432", cls: "behind-gain" });
    expect(liveDelta(-1260)).toEqual({ text: "-1.260", cls: "ahead-gain" });
    expect(liveDelta(-1260, "loss")).toEqual({ text: "-1.260", cls: "ahead-loss" });
    expect(liveDelta(0)).toEqual({ text: "+0.000", cls: "behind-loss" });
    expect(liveDelta(null)).toBeNull();
  });
});

describe("lapDeltaVm", () => {
  it("maps the server lap delta onto the LiveSplit shades, gold overriding", () => {
    expect(lapDeltaVm({ lap: 1, delta_ms: 1000, gained: false, gold: false }))
      .toEqual({ text: "+1.000", cls: "behind-loss" });
    expect(lapDeltaVm({ lap: 2, delta_ms: 500, gained: true, gold: false }))
      .toEqual({ text: "+0.500", cls: "behind-gain" });
    expect(lapDeltaVm({ lap: 2, delta_ms: -500, gained: false, gold: false }))
      .toEqual({ text: "-0.500", cls: "ahead-loss" });
    expect(lapDeltaVm({ lap: 3, delta_ms: -500, gained: true, gold: false }))
      .toEqual({ text: "-0.500", cls: "ahead-gain" });
    expect(lapDeltaVm({ lap: 1, delta_ms: -2000, gained: true, gold: true }))
      .toEqual({ text: "-2.000", cls: "gold" });
    expect(lapDeltaVm(null)).toBeNull();
  });
});

describe("viewModel", () => {
  beforeEach(() => clearHolds());
  it("racing with no sample yet: timer reads 0:00.000 (never a dash), race cluster populated", () => {
    const vm = viewModel(base, () => 2000);
    expect(vm.state).toBe("racing");
    expect(vm.primary).toEqual({ kind: "time", text: "0:00.000" });
    expect(vm.resets).toBe(3);
    expect(vm.pbStr).toBe("1:19.880");
  });
  it("composes the costume into the character name", () => {
    expect(viewModel({ ...base, costume: "Burger Bud", character: "Toad" }, () => 2000).char).toBe("Burger Bud Toad");
    expect(viewModel({ ...base, costume: "Base", character: "Toad" }, () => 2000).char).toBe("Toad");
  });
  it("racing bar fill + timer come from the delayed sample; dividers immediate", () => {
    const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 2, tot_lap: 3,
      completion: 0.9, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, { elapsed_ms: 1234, completion: 0.42 });
    expect(vm.bar).toEqual({ fill: 0.42, dividers: [0.31] });   // delayed completion, not e.completion
    expect(vm.primary).toEqual({ kind: "time", text: "0:01.234" });
  });
  it("racing with a sample but no clock yet (countdown): 0:00.000, bar fill 0", () => {
    const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 1, tot_lap: 3,
      completion: 0.5, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, { elapsed_ms: null, completion: null });
    expect(vm.primary).toEqual({ kind: "time", text: "0:00.000" });
    expect(vm.bar).toEqual({ fill: 0, dividers: [0.31] });
  });
  it("has no bar when not racing/finished", () => {
    const e = { online: true, screen: "MAIN_MENU", updated_at: 1, name: "P", color: "#888" };
    expect(viewModel(e, () => 2).bar).toBeNull();
  });
  it("no course model yet: empty calibrating bar with even placeholder dividers", () => {
    const e = { online: true, screen: "RACING", course: "Acorn Heights", cur_lap: 1, tot_lap: 3,
      completion: null, dividers: [], has_model: false, updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, { elapsed_ms: 500, completion: null });
    expect(vm.bar).toEqual({ fill: 0, dividers: [1 / 3, 2 / 3], calibrating: true });
  });
  it("finished without a model: full calibrating bar (flips once the upload builds it)", () => {
    const e = { online: true, screen: "RACING", course: "Acorn Heights", tot_lap: 5,
      final_time: "1:21.044", has_model: false, updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2);
    expect(vm.bar).toEqual({ fill: 1, dividers: [0.2, 0.4, 0.6, 0.8], calibrating: true });
  });
  it("calibrating with an unknown lap count: bare bar, no placeholder dividers", () => {
    const e = { online: true, screen: "RACING", course: "Acorn Heights", tot_lap: null,
      has_model: false, updated_at: 1, name: "P", color: "#888" };
    expect(viewModel(e, () => 2).bar).toEqual({ fill: 0, dividers: [], calibrating: true });
  });
  it("racing pace delta reads from the delayed sample (same clock as timer/bar)", () => {
    const delayed = { elapsed_ms: 1234, completion: 0.42, pb_delta_ms: -432 };
    expect(viewModel(base, () => 2000, delayed).delta).toEqual({ text: "-0.432", cls: "ahead-gain" });
    expect(viewModel(base, () => 2000, delayed, { trend: "loss" }).delta)
      .toEqual({ text: "-0.432", cls: "ahead-loss" });
    expect(viewModel(base, () => 2000, { ...delayed, pb_delta_ms: null }).delta).toBeNull();
    // the raw entry field doesn't drive the readout - it rides the buffer
    expect(viewModel({ ...base, pb_delta_ms: -432 }, () => 2000, null).delta).toBeNull();
  });
  it("laps mode renders the held per-lap delta instead of the fluid one", () => {
    const e = { ...base, lap_delta: { lap: 2, delta_ms: -800, gained: true, gold: false } };
    const delayed = { elapsed_ms: 1234, completion: 0.42, pb_delta_ms: 5000 };
    expect(viewModel(e, () => 2000, delayed, { deltaMode: "laps" }).delta)
      .toEqual({ text: "-0.800", cls: "ahead-gain" });
    expect(viewModel({ ...e, lap_delta: null }, () => 2000, delayed, { deltaMode: "laps" }).delta)
      .toBeNull();                                            // no lap completed yet
    // finished state ignores the mode and keeps the exact delta
    expect(viewModel({ ...e, final_time: "1:21.044" }, () => 2000, null, { deltaMode: "laps" }).delta)
      .toEqual({ text: "+1.164", cls: "behind-loss" });
  });
  it("finished: the exact delta wins over any stale live pace delta", () => {
    const vm = viewModel({ ...base, final_time: "1:21.044", pb_delta_ms: -50 }, () => 2000);
    expect(vm.delta).toEqual({ text: "+1.164", cls: "behind-loss" });
  });
  it("setup: activity phrase, no race cluster", () => {
    const vm = viewModel({ ...base, screen: "KART_SELECT" }, () => 2000);
    expect(vm.state).toBe("setup");
    expect(vm.primary).toEqual({ kind: "activity", text: "Choosing kart" });
    expect(vm.bar).toBeNull();
  });
  it("finished: final time + delta + FIN badge, bar present", () => {
    const vm = viewModel({ ...base, final_time: "1:21.044" }, () => 2000);
    expect(vm.state).toBe("finished");
    expect(vm.primary).toEqual({ kind: "time", text: "1:21.044" });
    expect(vm.delta).toEqual({ text: "+1.164", cls: "behind-loss" });
    expect(vm.badge).toBe("fin");
    expect(vm.bar).not.toBeNull();
  });
  it("finPb: green final only when it beat the PB; a first-ever finish counts", () => {
    expect(viewModel({ ...base, final_time: "1:18.880" }, () => 2000).finPb).toBe(true);   // faster
    expect(viewModel({ ...base, final_time: "1:21.044" }, () => 2000).finPb).toBe(false);  // slower
    expect(viewModel({ ...base, final_time: "1:21.044", pb_ms: null }, () => 2000).finPb).toBe(true); // first finish
    expect(viewModel(base, () => 2000).finPb).toBe(false);                                 // racing: n/a
  });
  it("a reset keeps the last readout on screen (no In the menus) until a real menu", () => {
    const delayed = { elapsed_ms: 51234, completion: 0.4, pb_delta_ms: 800 };
    const racing = viewModel(base, () => 2000, delayed);            // stashes the readout
    const held = viewModel({ ...base, screen: "RESET" }, () => 3000, null);
    expect(held.state).toBe("held");
    expect(held.primary).toEqual(racing.primary);                   // frozen time persists
    expect(held.delta).toEqual(racing.delta);
    expect(held.bar).toEqual(racing.bar);
    expect(held.badge).toBe("reset");                               // reset icon, not pause
    expect(viewModel({ ...base, screen: "MAIN_MENU" }, () => 4000).state).toBe("menus");
    expect(viewModel({ ...base, screen: "RESET" }, () => 5000).state).toBe("menus");  // dropped
  });
  it("the pause menu shows the frozen readout with the pause badge", () => {
    viewModel(base, () => 2000, { elapsed_ms: 51234, completion: 0.4, pb_delta_ms: 800 });
    const paused = viewModel({ ...base, screen: "RACE_MENU" }, () => 3000);
    expect(paused.state).toBe("held");
    expect(paused.badge).toBe("pause");
    expect(paused.primary).toEqual({ kind: "time", text: "0:51.234" });
  });
  it("a finished readout (FIN + verdict colour) persists through the HOME flow", () => {
    viewModel({ ...base, final_time: "1:18.880" }, () => 2000);     // a PB finish
    const home = viewModel({ ...base, screen: "HOME", final_time: null }, () => 3000);
    expect(home.state).toBe("finished");
    expect(home.badge).toBe("fin");
    expect(home.primary).toEqual({ kind: "time", text: "1:18.880" });
    expect(home.finPb).toBe(true);
  });
  it("a cold pause/reset with nothing held falls back to the menus", () => {
    expect(viewModel({ ...base, screen: "RESET" }, () => 2000).state).toBe("menus");
  });
  it("offline seen: last seen line; never-seen: plain offline", () => {
    const seen = viewModel({ ...base, online: false, updated_at: 1000 }, () => 1000 + 3 * 3600000);
    expect(seen.state).toBe("offline");
    expect(seen.primary).toEqual({ kind: "seen", text: "last seen 3h ago" });
    expect(seen.char).toBeNull();
    const never = viewModel({ ...base, online: false, updated_at: 0 }, () => 5000);
    expect(never.primary).toEqual({ kind: "seen", text: "offline" });
  });
});
