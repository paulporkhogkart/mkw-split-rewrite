import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { EventHub } from './events';
import type { AttemptPayload } from '../db/types';
import { requireToken } from './auth';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { upsertRun, findGhostMatch, enrichRunFromGhost, timeToMs } from '../db/ingest';
import { recordGhostImport } from '../db/ghostImport';
import { recomputeIsPb, recomputeWasPb } from '../db/pb';
import { courseLeaderboard, currentWr } from '../db/reads';
import { rebuildCourseModel } from '../db/courseModels';
import { buildRunCascade } from '../activity/cascade';
import { commitActivity } from '../activity/publish';
import type { ActivityHub } from '../activity/hub';

export function runsRoutes(db: DatabaseSync, hub: EventHub, activity: ActivityHub,
                           invalidateModel?: (courseId: number) => void): Hono<Env> {
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

    // Ghost import: dedup by player+course+exact total time. A match (e.g. a Season-0
    // carryover) is enriched in place and NOT announced; no match becomes a new run.
    if (p.source === 'ghost' && p.status === 'finished') {
      const totalMs = timeToMs(p.total_time);
      const matchId = findGhostMatch(db, seasonId, playerId, courseId, cc, totalMs);
      if (matchId !== null) {
        const { trailAdded } = enrichRunFromGhost(db, matchId, p);
        recordGhostImport(db, { runId: matchId, playerId, courseId, cc, totalMs, action: 'enriched' });
        if (trailAdded) {
          try {
            const built = rebuildCourseModel(db, courseId, cc);
            if (built) invalidateModel?.(courseId);
          } catch (e) { console.error('[course-model] ghost-enrich rebuild failed:', e); }
        }
        console.log(`[ghost-import] enriched run ${matchId} (${playerName}, ${slugify(p.course)}, ${p.total_time})`);
        return c.json({ deduped: true, is_pb: false, rank: null, gap_to_leader_ms: null, gap_to_wr_ms: null });
      }
    }

    const beforeBoard = courseLeaderboard(db, seasonId, courseId, cc);
    const prevLeader = beforeBoard[0] ?? null;
    const prevMine = db.prepare(
      'SELECT total_time_ms FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1'
    ).get(seasonId, playerId, courseId, cc) as { total_time_ms: number } | undefined;
    const prevMineMs = prevMine ? prevMine.total_time_ms : null;
    upsertRun(db, p, playerId, seasonId);

    if (p.status !== 'finished') return c.json({ is_pb: false, rank: null, gap_to_leader_ms: null, gap_to_wr_ms: null });

    recomputeIsPb(db, seasonId, playerId, courseId, cc);
    recomputeWasPb(db, seasonId, playerId, courseId, cc);

    // Models heal themselves: a finished run with a trail rebuilds this
    // course's model and drops the presence hub's cached copy, so the very
    // next frame projects on the fresh geometry. Uploads are ~once per race;
    // the build is cheap at the <=40-run window.
    if ((p.points?.length ?? 0) > 0) {
      try {
        const built = rebuildCourseModel(db, courseId, cc);
        if (built) {
          invalidateModel?.(courseId);
          console.log(`[course-model] ${slugify(p.course)} cc${cc}: ${built.status}, ${built.laps} laps, ${built.runs} runs`);
        }
      } catch (e) {
        console.error('[course-model] rebuild failed:', e);
      }
    }
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

    if (isPb) {
      const wrMs = wr ? wr.record_ms : null;
      const inputs = buildRunCascade({
        ts: Date.now(), seasonId, cc, courseId, moverId: playerId, moverName: playerName,
        before: beforeBoard, after: lb, beforeWr: wrMs, afterWr: wrMs,
        prevPbMs: prevMineMs, attempts: null,
      });
      commitActivity(db, activity, inputs);
    }

    if (p.source === 'ghost') {
      const newRun = db.prepare("SELECT id FROM runs WHERE attempt_id=?").get(p.attempt_id) as { id: number } | undefined;
      recordGhostImport(db, { runId: newRun ? newRun.id : null, playerId, courseId, cc,
        totalMs: timeToMs(p.total_time), action: 'new' });
      console.log(`[ghost-import] new run (${playerName}, ${slugify(p.course)}, ${p.total_time})`);
    }

    return c.json(result);
  });

  r.post('/v1/runs/start', requireToken(db), async (c) => {
    const p = await c.req.json() as { course?: string; cc?: number };
    if (p?.course) hub.publish({ type: 'run_started', player: c.get('playerName'), course: p.course, cc: p.cc ?? 150 });
    return c.json({ ok: true });
  });

  return r;
}
