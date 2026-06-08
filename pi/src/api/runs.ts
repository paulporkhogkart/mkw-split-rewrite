import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { EventHub } from './events';
import type { AttemptPayload } from '../db/types';
import { requireToken } from './auth';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { upsertRun } from '../db/ingest';
import { recomputeIsPb, recomputeWasPb } from '../db/pb';
import { courseLeaderboard, currentWr } from '../db/reads';

export function runsRoutes(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const r = new Hono<Env>();

  r.post('/v1/runs', requireToken(db), async (c) => {
    const playerId = c.get('playerId');
    const playerName = c.get('playerName');
    const p = (await c.req.json()) as AttemptPayload;
    if (!p?.attempt_id || !p?.course || !p?.status) return c.json({ error: 'bad payload' }, 400);
    const cc = p.cc ?? 150;
    const seasonId = activeSeasonId(db);
    const courseId = courseIdBySlug(db, slugify(p.course));
    if (courseId === null) return c.json({ error: `unknown course: ${p.course}` }, 400);

    const prevLeader = courseLeaderboard(db, seasonId, courseId, cc)[0] ?? null;
    const prevMine = db.prepare(
      'SELECT total_time_ms FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1'
    ).get(seasonId, playerId, courseId, cc) as { total_time_ms: number } | undefined;
    const prevMineMs = prevMine ? prevMine.total_time_ms : null;
    upsertRun(db, p, playerId, seasonId);

    if (p.status !== 'finished') return c.json({ is_pb: false, rank: null, gap_to_leader_ms: null, gap_to_wr_ms: null });

    recomputeIsPb(db, seasonId, playerId, courseId, cc);
    recomputeWasPb(db, seasonId, playerId, courseId, cc);
    const lb = courseLeaderboard(db, seasonId, courseId, cc);
    const mine = lb.find(x => x.player_id === playerId) ?? null;
    const newMineMs = mine ? mine.total_time_ms : null;
    const isPb = newMineMs !== null && (prevMineMs === null || newMineMs < prevMineMs);
    const wr = currentWr(db, courseId, cc);
    const leader = lb[0] ?? null;
    const result = {
      is_pb: isPb,
      rank: mine ? mine.rank : null,
      gap_to_leader_ms: mine && leader ? mine.total_time_ms - leader.total_time_ms : null,
      gap_to_wr_ms: mine && wr ? mine.total_time_ms - wr.record_ms : null,
    };

    hub.publish({ type: 'run_finished', player: playerName, course: p.course, cc, total_time: p.total_time ?? null, is_pb: isPb, rank: result.rank });
    if (isPb && p.total_time)
      hub.publish({ type: 'pb_achieved', player: playerName, course: p.course, cc, total_time: p.total_time,
        delta_vs_prev_ms: prevMineMs !== null && newMineMs !== null ? newMineMs - prevMineMs : null, rank: result.rank });
    if (leader && leader.player_id === playerId && prevLeader && prevLeader.player_id !== playerId && p.total_time)
      hub.publish({ type: 'lead_change', course: p.course, cc, new_leader: playerName, prev_leader: prevLeader.display_name, total_time: p.total_time });
    if (wr && mine && mine.total_time_ms < wr.record_ms && p.total_time)
      hub.publish({ type: 'wr_beaten', player: playerName, course: p.course, cc, total_time: p.total_time, wr_time: wr.record_str });

    return c.json(result);
  });

  r.post('/v1/runs/start', requireToken(db), async (c) => {
    const p = await c.req.json() as { course?: string; cc?: number };
    if (p?.course) hub.publish({ type: 'run_started', player: c.get('playerName'), course: p.course, cc: p.cc ?? 150 });
    return c.json({ ok: true });
  });

  return r;
}
