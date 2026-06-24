import { describe, it, expect } from "vitest";
import { rowFromEvent, rowFromSession, upsertRows, dropRow, replaceSessions } from "./activityMerge.js";

const ev = (id, ts = id) => ({ id, ts, type: "pb", payload: {} });
const sess = (sid, started, over = {}) => ({
  session_id: sid, state: "open", started_ts: started, player: null, course: null, cls: "menus",
  character: null, costume: null, ended_ts: null, duration_ms: null, attempts: null, pbs: null, ...over,
});

describe("rowFromEvent", () => {
  it("milestone -> evt: key, kind event, feedTs=ts", () => {
    const r = rowFromEvent(ev(5, 1000));
    expect(r).toMatchObject({ key: "evt:5", feedTs: 1000, kind: "event" });
    expect(r.event.id).toBe(5);
  });
  it("persisted session -> evt: key, kind session, fields from payload", () => {
    const r = rowFromEvent({ id: 7, ts: 2000, type: "session",
      player: { id: 1, name: "Gub", color: "#0" }, course: { slug: "cc", name: "Crown City" },
      payload: { cls: "racing", character: "Peach", costume: "Base", started_ts: 2000, ended_ts: 5000, duration_ms: 3000, attempts: 4, pbs: 1 } });
    expect(r).toMatchObject({ key: "evt:7", kind: "session", live: false, cls: "racing", attempts: 4, pbs: 1, duration_ms: 3000 });
    expect(r.course.name).toBe("Crown City");
  });
});

describe("rowFromSession", () => {
  it("open -> sess: key, live true, feedTs=started_ts", () => {
    expect(rowFromSession(sess(3, 1500))).toMatchObject({ key: "sess:3", kind: "session", live: true, feedTs: 1500 });
  });
  it("final -> live false", () => {
    expect(rowFromSession(sess(3, 1500, { state: "final", ended_ts: 9000, duration_ms: 7500 })).live).toBe(false);
  });
});

describe("upsertRows / ordering", () => {
  it("upserts by key, newest feedTs first", () => {
    const a = upsertRows([], [rowFromEvent(ev(1, 100)), rowFromEvent(ev(2, 300))]);
    expect(a.map((r) => r.key)).toEqual(["evt:2", "evt:1"]);
  });
  it("a re-upsert replaces in place (open -> final, same key)", () => {
    let s = upsertRows([], [rowFromSession(sess(3, 1500))]);
    s = upsertRows(s, [rowFromSession(sess(3, 1500, { state: "final", ended_ts: 9000, duration_ms: 7500 }))]);
    expect(s).toHaveLength(1);
    expect(s[0]).toMatchObject({ key: "sess:3", live: false, duration_ms: 7500 });
  });
  it("orders a live session below a newer milestone by feed ts", () => {
    const rows = upsertRows([], [rowFromSession(sess(3, 1000)), rowFromEvent(ev(9, 5000))]);
    expect(rows.map((r) => r.key)).toEqual(["evt:9", "sess:3"]);
  });
  it("caps to the most recent N", () => {
    const rows = upsertRows([], [ev(1, 1), ev(2, 2), ev(3, 3)].map(rowFromEvent), 2);
    expect(rows.map((r) => r.feedTs)).toEqual([3, 2]);
  });
});

describe("dropRow / replaceSessions", () => {
  it("dropRow removes a session by key", () => {
    const rows = upsertRows([], [rowFromSession(sess(3, 1000)), rowFromEvent(ev(9, 5000))]);
    expect(dropRow(rows, "sess:3").map((r) => r.key)).toEqual(["evt:9"]);
  });
  it("replaceSessions swaps all sess:* but keeps evt:*", () => {
    const rows = upsertRows([], [rowFromSession(sess(3, 1000)), rowFromEvent(ev(9, 5000))]);
    const out = replaceSessions(rows, [rowFromSession(sess(4, 2000))]);
    expect(out.map((r) => r.key).sort()).toEqual(["evt:9", "sess:4"]);
  });
});
