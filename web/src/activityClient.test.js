// web/src/activityClient.test.js
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import { activity } from "../../src/lib/stores.js";
import { pushEvents, applyStreamMsg, loadActivityHistory, startActivityStream } from "./activityClient.js";

class FakeWS {
  constructor(url) { this.url = url; this.listeners = {}; FakeWS.last = this; }
  addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); }
  emit(t, ev) { (this.listeners[t] || []).forEach((fn) => fn(ev)); }
  close() {}
}
const ev = (id, ts = id) => ({ id, ts, type: "pb", payload: {} });
const sess = (sid, started, state = "open") => ({
  session_id: sid, state, started_ts: started, player: null, course: null, cls: "menus",
  character: null, costume: null, ended_ts: state === "final" ? started + 1000 : null,
  duration_ms: state === "final" ? 1000 : null, attempts: null, pbs: null,
});

beforeEach(() => { activity.set([]); FakeWS.last = null; });

describe("pushEvents", () => {
  it("merges events newest-first (by feed ts) into the store", () => {
    pushEvents([ev(1, 100)]); pushEvents([ev(3, 300), ev(2, 200)]);
    expect(get(activity).map((r) => r.key)).toEqual(["evt:3", "evt:2", "evt:1"]);
  });
});

describe("applyStreamMsg", () => {
  it("kind:event merges a milestone", () => {
    applyStreamMsg({ kind: "event", event: ev(9, 900) });
    expect(get(activity)[0].key).toBe("evt:9");
  });
  it("kind:session upserts a live session; a final replaces it in place", () => {
    applyStreamMsg({ kind: "session", session: sess(3, 1000) });
    expect(get(activity)[0]).toMatchObject({ key: "sess:3", live: true });
    applyStreamMsg({ kind: "session", session: sess(3, 1000, "final") });
    expect(get(activity)).toHaveLength(1);
    expect(get(activity)[0]).toMatchObject({ key: "sess:3", live: false });
  });
  it("session_drop removes the live session", () => {
    applyStreamMsg({ kind: "session", session: sess(3, 1000) });
    applyStreamMsg({ kind: "session_drop", session_id: 3 });
    expect(get(activity)).toHaveLength(0);
  });
  it("sessions_snapshot replaces sess:* but keeps milestones", () => {
    pushEvents([ev(9, 900)]);
    applyStreamMsg({ kind: "session", session: sess(3, 1000) });
    applyStreamMsg({ kind: "sessions_snapshot", sessions: [sess(4, 2000)] });
    expect(get(activity).map((r) => r.key).sort()).toEqual(["evt:9", "sess:4"]);
  });
});

describe("loadActivityHistory", () => {
  it("fetches /v1/activity and merges the result", async () => {
    const fetchImpl = async () => ({ json: async () => [ev(5, 500), ev(4, 400)] });
    await loadActivityHistory("http://localhost:8787", { fetchImpl });
    expect(get(activity).map((r) => r.key)).toEqual(["evt:5", "evt:4"]);
  });
});

describe("startActivityStream", () => {
  it("opens the stream ws and applies a live message", () => {
    const stop = startActivityStream("http://localhost:8787", { WebSocketImpl: FakeWS });
    expect(FakeWS.last.url).toBe("ws://localhost:8787/v1/activity/stream");
    FakeWS.last.emit("message", { data: JSON.stringify({ kind: "event", event: ev(9, 900) }) });
    expect(get(activity)[0].key).toBe("evt:9");
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
  it("reloads persisted history on (re)connect so finalised sessions survive the snapshot", async () => {
    let fetched = 0;
    const fetchImpl = async () => { fetched++; return { json: async () => [ev(5, 500)] }; };
    const stop = startActivityStream("http://localhost:8787", { WebSocketImpl: FakeWS, fetchImpl });
    FakeWS.last.emit("open");
    await new Promise((r) => setTimeout(r, 0));   // flush the async loadActivityHistory
    expect(fetched).toBe(1);
    expect(get(activity).map((r) => r.key)).toContain("evt:5");
    stop();
  });
});
