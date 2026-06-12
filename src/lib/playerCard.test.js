import { describe, it, expect } from "vitest";
import { viewModel, lastSeen, pbDelta, liveDelta, fmtTimeMs } from "./playerCard.js";

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
  it("signs the delta vs PB", () => {
    expect(pbDelta("1:21.044", 79880)).toEqual({ text: "+1.16", cls: "slow" });
    expect(pbDelta("1:18.880", 79880)).toEqual({ text: "-1.00", cls: "fast" });
    expect(pbDelta(null, 79880)).toBeNull();
  });
});

describe("liveDelta", () => {
  it("formats a one-decimal signed pace delta", () => {
    expect(liveDelta(432)).toEqual({ text: "+0.4", cls: "slow" });
    expect(liveDelta(-1260)).toEqual({ text: "-1.3", cls: "fast" });
    expect(liveDelta(0)).toEqual({ text: "+0.0", cls: "slow" });
    expect(liveDelta(null)).toBeNull();
  });
});

describe("viewModel", () => {
  it("racing: time is dashes, race cluster populated", () => {
    const vm = viewModel(base, () => 2000);
    expect(vm.state).toBe("racing");
    expect(vm.primary).toEqual({ kind: "time", text: "—" });
    expect(vm.resets).toBe(3);
    expect(vm.pbStr).toBe("1:19.880");
  });
  it("racing bar fill + timer come from the delayed sample; dividers immediate", () => {
    const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 2, tot_lap: 3,
      completion: 0.9, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, { elapsed_ms: 1234, completion: 0.42 });
    expect(vm.bar).toEqual({ fill: 0.42, dividers: [0.31] });   // delayed completion, not e.completion
    expect(vm.primary).toEqual({ kind: "time", text: "0:01.234" });
  });
  it("racing with no delayed sample yet: timer is a dash, bar fill 0", () => {
    const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 1, tot_lap: 3,
      completion: 0.5, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
    const vm = viewModel(e, () => 2, null);
    expect(vm.primary).toEqual({ kind: "time", text: "—" });
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
  it("racing with a live pace delta: one-decimal readout next to PB; none without one", () => {
    expect(viewModel({ ...base, pb_delta_ms: -432 }, () => 2000).delta).toEqual({ text: "-0.4", cls: "fast" });
    expect(viewModel(base, () => 2000).delta).toBeNull();
  });
  it("finished: the exact delta wins over any stale live pace delta", () => {
    const vm = viewModel({ ...base, final_time: "1:21.044", pb_delta_ms: -50 }, () => 2000);
    expect(vm.delta).toEqual({ text: "+1.16", cls: "slow" });
  });
  it("setup: activity phrase, no race cluster", () => {
    const vm = viewModel({ ...base, screen: "KART_SELECT" }, () => 2000);
    expect(vm.state).toBe("setup");
    expect(vm.primary).toEqual({ kind: "activity", text: "Choosing kart" });
    expect(vm.bar).toBeNull();
  });
  it("finished: final time + delta, bar present, no racing-only dot", () => {
    const vm = viewModel({ ...base, final_time: "1:21.044" }, () => 2000);
    expect(vm.state).toBe("finished");
    expect(vm.primary).toEqual({ kind: "time", text: "1:21.044" });
    expect(vm.delta).toEqual({ text: "+1.16", cls: "slow" });
    expect(vm.bar).not.toBeNull();
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
