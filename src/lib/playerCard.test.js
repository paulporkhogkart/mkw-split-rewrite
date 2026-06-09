import { describe, it, expect } from "vitest";
import { viewModel, lapSegments, lastSeen, pbDelta, fmtTimeMs } from "./playerCard.js";

const base = { player_id: 1, name: "Paul", color: "#a78bfa", online: true, screen: "RACING",
  course: "Rainbow Road", character: "Mario", kart: "Standard", cur_lap: 2, tot_lap: 3,
  coins: 7, mushrooms: 2, resets: 3, pb_ms: 79880, completion: 0.63, final_time: null, updated_at: 1000 };

describe("fmtTimeMs", () => {
  it("formats ms as m:ss.SSS", () => {
    expect(fmtTimeMs(79880)).toBe("1:19.880");
    expect(fmtTimeMs(2044)).toBe("0:02.044");
    expect(fmtTimeMs(null)).toBeNull();
  });
});

describe("lapSegments", () => {
  it("splits completion across laps", () => {
    const s = lapSegments(0.63, 3);
    expect(s.length).toBe(3);
    expect(s[0]).toBe(1);
    expect(s[1]).toBeCloseTo(0.89, 2);
    expect(s[2]).toBe(0);
  });
  it("defaults to 3 segments when tot_lap is missing", () => {
    expect(lapSegments(0, null).length).toBe(3);
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

describe("viewModel", () => {
  it("racing: time is dashes, race cluster populated", () => {
    const vm = viewModel(base, () => 2000);
    expect(vm.state).toBe("racing");
    expect(vm.primary).toEqual({ kind: "time", text: "—" });
    expect(vm.resets).toBe(3);
    expect(vm.pbStr).toBe("1:19.880");
    expect(vm.dotPct).toBeCloseTo(63, 0);
    expect(vm.segments.length).toBe(3);
  });
  it("setup: activity phrase, no race cluster", () => {
    const vm = viewModel({ ...base, screen: "KART_SELECT" }, () => 2000);
    expect(vm.state).toBe("setup");
    expect(vm.primary).toEqual({ kind: "activity", text: "Choosing kart" });
    expect(vm.segments).toBeNull();
  });
  it("finished: final time + delta, full bar, no dot", () => {
    const vm = viewModel({ ...base, final_time: "1:21.044" }, () => 2000);
    expect(vm.state).toBe("finished");
    expect(vm.primary).toEqual({ kind: "time", text: "1:21.044" });
    expect(vm.delta).toEqual({ text: "+1.16", cls: "slow" });
    expect(vm.dotPct).toBeNull();
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
