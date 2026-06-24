// web/src/activityClient.test.js
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import { activity } from "../../src/lib/stores.js";
import { pushActivity, loadActivityHistory, startActivityStream } from "./activityClient.js";

class FakeWS {
  constructor(url) { this.url = url; this.listeners = {}; FakeWS.last = this; }
  addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); }
  emit(t, ev) { (this.listeners[t] || []).forEach((fn) => fn(ev)); }
  close() {}
}
const e = (id) => ({ id, ts: id, type: "pb", payload: {} });

beforeEach(() => { activity.set([]); FakeWS.last = null; });

describe("pushActivity", () => {
  it("merges events newest-first into the store", () => {
    pushActivity([e(1)]); pushActivity([e(3), e(2)]);
    expect(get(activity).map(x => x.id)).toEqual([3, 2, 1]);
  });
});

describe("loadActivityHistory", () => {
  it("fetches /v1/activity and merges the result", async () => {
    const fetchImpl = async () => ({ json: async () => [e(5), e(4)] });
    await loadActivityHistory("http://localhost:8787", { fetchImpl });
    expect(get(activity).map(x => x.id)).toEqual([5, 4]);
  });
});

describe("startActivityStream", () => {
  it("opens the stream ws and prepends a live event", () => {
    const stop = startActivityStream("http://localhost:8787", { WebSocketImpl: FakeWS });
    expect(FakeWS.last.url).toBe("ws://localhost:8787/v1/activity/stream");
    FakeWS.last.emit("message", { data: JSON.stringify(e(9)) });
    expect(get(activity)[0].id).toBe(9);
    stop();
  });
  it("stop() prevents reconnect", () => {
    let scheduled = null;
    const setTimeoutImpl = (fn, ms) => { scheduled = { fn, ms }; return 1; };
    const stop = startActivityStream("http://x", { WebSocketImpl: FakeWS, setTimeoutImpl });
    stop();
    FakeWS.last.emit("close");
    expect(scheduled).toBeNull();
  });
});
