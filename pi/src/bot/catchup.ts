import type { DatabaseSync } from 'node:sqlite';
import type { EmbedBuilder } from 'discord.js';
import type { ServerEvent } from '../db/types';
import { activeSeasonId } from '../db/seasons';
import { courseLeaderboard } from '../db/reads';
import { dispatch } from './dispatch';
import type { BotState } from './state';

export interface CatchupCtx {
  send: (e: EmbedBuilder) => void;
  state: BotState;
  persist: (s: BotState) => void;
}

/** Announce every PB-setting run newer than the watermark, reconstructing the same
 *  pb_achieved event the server would have sent (from the stored was_pb run + the DB) and
 *  feeding it through the live dispatch path. Advances + persists the watermark per run, so
 *  it never double-announces - the live event becomes a redundant nudge. Returns the count. */
export function announceMissedPbs(db: DatabaseSync, ctx: CatchupCtx): number {
  const season = activeSeasonId(db);
  // Watermark ahead of every existing run id = the runs table was wiped/re-seeded
  // (ids restart at 1) while the bot kept its old state file. Treat everything
  // currently in the db as unannounced rather than silently skipping new PBs forever.
  const maxId = (db.prepare('SELECT COALESCE(MAX(id),0) AS m FROM runs').get() as { m: number }).m;
  if (ctx.state.lastPbRunId > maxId) {
    ctx.state.lastPbRunId = 0;
    ctx.persist(ctx.state);
  }
  const rows = db.prepare(
    `SELECT r.id, r.cc, r.player_id, r.course_id, r.total_time_ms, r.total_time_str,
            p.display_name AS player, c.display_name AS course
     FROM runs r JOIN players p ON p.id = r.player_id JOIN courses c ON c.id = r.course_id
     WHERE r.was_pb = 1 AND r.season_id = ? AND r.id > ? AND r.total_time_str IS NOT NULL
     ORDER BY r.id`
  ).all(season, ctx.state.lastPbRunId) as Array<{
    id: number; cc: number; player_id: number; course_id: number;
    total_time_ms: number; total_time_str: string; player: string; course: string;
  }>;

  let n = 0;
  for (const r of rows) {
    // The previous PB on this course = the prior was_pb run; its time gives the delta.
    const prev = db.prepare(
      `SELECT total_time_ms FROM runs
       WHERE was_pb = 1 AND season_id = ? AND player_id = ? AND course_id = ? AND cc = ? AND id < ?
       ORDER BY id DESC LIMIT 1`
    ).get(season, r.player_id, r.course_id, r.cc, r.id) as { total_time_ms: number } | undefined;
    const delta = prev ? r.total_time_ms - prev.total_time_ms : null;

    const lb = courseLeaderboard(db, season, r.course_id, r.cc);
    const idx = lb.findIndex((x) => x.player_id === r.player_id);
    const rank = idx >= 0 ? idx + 1 : null;

    const ev: ServerEvent = {
      type: 'pb_achieved', player: r.player, course: r.course, cc: r.cc,
      total_time: r.total_time_str, delta_vs_prev_ms: delta, rank,
    };
    dispatch(db, ev, ctx.send);
    ctx.state.lastPbRunId = r.id;
    ctx.persist(ctx.state);
    n++;
  }
  return n;
}
