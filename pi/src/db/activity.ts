import type { DatabaseSync } from 'node:sqlite';
import type { ActivityInput, ActivityEvent } from '../activity/types';
import type { SessionView } from '../activity/sessionTracker';

/** Map a finalised session view to a persistable activity_events row (type 'session'). The feed
 *  position is the session's start; the payload carries the session detail the client formats. */
export function sessionInput(seasonId: number, v: SessionView): ActivityInput {
  return {
    ts: v.started_ts,
    type: 'session',
    season_id: seasonId,
    player_id: v.player_id,
    course_id: v.course_id,
    cc: null,
    payload: {
      cls: v.cls, character: v.character, costume: v.costume,
      started_ts: v.started_ts, ended_ts: v.ended_ts,
      duration_ms: v.ended_ts != null ? v.ended_ts - v.started_ts : null,
      attempts: v.attempts, pbs: v.pbs,
    },
  };
}

export function insertActivityEvents(db: DatabaseSync, inputs: ActivityInput[]): number[] {
  const stmt = db.prepare(
    `INSERT INTO activity_events(ts, type, season_id, player_id, course_id, cc, payload)
     VALUES (?,?,?,?,?,?,?)`);
  const ids: number[] = [];
  for (const e of inputs)
    ids.push(Number(stmt.run(e.ts, e.type, e.season_id, e.player_id, e.course_id, e.cc, JSON.stringify(e.payload)).lastInsertRowid));
  return ids;
}

type Row = { id: number; ts: number; type: string; player_id: number | null;
  course_id: number | null; payload: string };

function player(db: DatabaseSync, id: number | null) {
  if (id == null) return null;
  const p = db.prepare('SELECT id, display_name, color FROM players WHERE id=?').get(id) as
    { id: number; display_name: string; color: string | null } | undefined;
  return p ? { id: p.id, name: p.display_name, color: p.color } : null;
}

function course(db: DatabaseSync, id: number | null) {
  if (id == null) return null;
  const c = db.prepare('SELECT slug, display_name FROM courses WHERE id=?').get(id) as
    { slug: string; display_name: string } | undefined;
  return c ? { slug: c.slug, name: c.display_name } : null;
}

export function resolveActivity(db: DatabaseSync, row: Row): ActivityEvent {
  const payload = JSON.parse(row.payload) as Record<string, unknown>;
  if (typeof payload.rival_id === 'number') payload.rival = player(db, payload.rival_id as number);
  return {
    id: row.id, ts: row.ts, type: row.type as ActivityEvent['type'],
    player: player(db, row.player_id), course: course(db, row.course_id), payload,
  };
}

export function recentActivity(
  db: DatabaseSync, opts: { seasonId: number; before?: number; limit?: number }): ActivityEvent[] {
  const limit = Math.min(opts.limit ?? 100, 500);
  const rows = (opts.before
    ? db.prepare('SELECT * FROM activity_events WHERE season_id=? AND id<? ORDER BY id DESC LIMIT ?')
        .all(opts.seasonId, opts.before, limit)
    : db.prepare('SELECT * FROM activity_events WHERE season_id=? ORDER BY id DESC LIMIT ?')
        .all(opts.seasonId, limit)) as Row[];
  return rows.map(r => resolveActivity(db, r));
}
