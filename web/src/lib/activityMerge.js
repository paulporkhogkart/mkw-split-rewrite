// Normalize the activity stream's wire items into store rows and merge them by a stable key,
// newest (by feed timestamp) first. A row: { key, feedTs, kind:'event'|'session', ... }.
//   - milestone event            -> key `evt:<id>`,  kind 'event',   event: <ActivityEvent>
//   - persisted session (REST)   -> key `evt:<id>`,  kind 'session', live:false, fields from payload
//   - live session (SessionWire) -> key `sess:<id>`, kind 'session', live:(state==='open')
// A session is only ever live-in-memory (`sess:`) OR persisted (`evt:`) for a given client, so
// the two key spaces never collide for the same logical session.

/** An ActivityEvent (milestone, or a persisted type:'session' row) -> store row. */
export function rowFromEvent(e) {
  if (e.type === "session") {
    const p = e.payload || {};
    return {
      key: `evt:${e.id}`, feedTs: e.ts, kind: "session", live: false,
      player: e.player ?? null, course: e.course ?? null, cls: p.cls,
      character: p.character ?? null, costume: p.costume ?? null,
      started_ts: p.started_ts ?? e.ts, ended_ts: p.ended_ts ?? null,
      duration_ms: p.duration_ms ?? null, attempts: p.attempts ?? null, pbs: p.pbs ?? null,
    };
  }
  return { key: `evt:${e.id}`, feedTs: e.ts, kind: "event", event: e };
}

/** A live SessionWire -> store row (keyed in its own space so open->final updates in place). */
export function rowFromSession(s) {
  return {
    key: `sess:${s.session_id}`, feedTs: s.started_ts, kind: "session", live: s.state === "open",
    player: s.player ?? null, course: s.course ?? null, cls: s.cls,
    character: s.character ?? null, costume: s.costume ?? null,
    started_ts: s.started_ts, ended_ts: s.ended_ts ?? null,
    duration_ms: s.duration_ms ?? null, attempts: s.attempts ?? null, pbs: s.pbs ?? null,
  };
}

const sortCap = (arr, cap) => arr.sort((a, b) => b.feedTs - a.feedTs).slice(0, cap);

/** Upsert rows (by key) into the existing list; newest feed-ts first; capped. */
export function upsertRows(existing, rows, cap = 300) {
  const byKey = new Map(existing.map((r) => [r.key, r]));
  for (const r of rows) byKey.set(r.key, r);
  return sortCap([...byKey.values()], cap);
}

/** Remove a row by key (e.g. a dropped session). */
export function dropRow(existing, key) {
  return existing.filter((r) => r.key !== key);
}

/** Replace every live-session row (`sess:*`) with a fresh snapshot; keep milestones + persisted. */
export function replaceSessions(existing, sessionRows, cap = 300) {
  return upsertRows(existing.filter((r) => !r.key.startsWith("sess:")), sessionRows, cap);
}
