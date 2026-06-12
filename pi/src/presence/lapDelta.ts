// pi/src/presence/lapDelta.ts
// LiveSplit-style per-lap delta: computed only from the completed laps' digit-read
// durations (presence frame splits_ms) against the PB run's run_laps. The card holds
// the latest value until the next lap line. `gained` = this lap beat the PB run's
// same lap; `gold` = it beat the player's best-ever time for that lap (any finished
// run); ahead/behind is the sign of delta_ms. Colour mapping happens client-side.
import type { DatabaseSync } from 'node:sqlite';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { pbRunFor } from '../db/pb';

export interface LapDeltaResult { lap: number; delta_ms: number; gained: boolean; gold: boolean; }
/** PB lap durations + one delta row per completed lap (the race rail renders all
 *  rows; the card shows the latest). */
export interface LapInfo { pb_laps_ms: number[]; deltas: LapDeltaResult[]; }

export type LapDelta = ((
  playerId: number, course: string | null | undefined, splitsMs: number[] | null | undefined,
  pinnedRunId?: number | null,
) => LapInfo | null) & {
  /** Drop a course's cached PB laps + golds (chained into the model-rebuild
   *  invalidation, which fires on every finished trailed upload - keeps golds
   *  fresh across a session without per-frame queries). */
  invalidateCourse(courseId: number): void;
};

export function makeLapDelta(db: DatabaseSync, cc = 150): LapDelta {
  // Per (course, player): the PB run's lap durations (keyed by run id, so a new PB
  // self-heals) + lazily-filled best-ever segment per lap (one query per lap line).
  const cache = new Map<string, { runId: number; pbLaps: number[]; golds: Map<number, number | null> }>();

  const fn = ((playerId, course, splitsMs, pinnedRunId = null) => {
    if (!course) return null;
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    const season = activeSeasonId(db);
    // The hub pins the pre-race PB run for the duration of a race (the finish
    // upload flips is_pb within a second); honour the pin so the rail's
    // reference column never flips to the run that was just set.
    const pb = pinnedRunId != null ? { id: pinnedRunId }
      : pbRunFor(db, season, playerId, courseId, cc);
    if (!pb) return null;
    const key = `${courseId}:${playerId}`;
    let entry = cache.get(key);
    if (!entry || entry.runId !== pb.id) {
      const pbLaps = (db.prepare(
        'SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index'
      ).all(pb.id) as { lap_time_ms: number }[]).map((r) => r.lap_time_ms);
      entry = { runId: pb.id, pbLaps, golds: new Map() };
      cache.set(key, entry);
    }
    // No per-lap data on the PB (e.g. a carryover seed): no comparison to run.
    if (entry.pbLaps.length === 0) return null;
    const goldFor = (lapIdx: number): number | null => {
      let g = entry!.golds.get(lapIdx);
      if (g === undefined) {
        g = (db.prepare(
          `SELECT MIN(rl.lap_time_ms) AS m FROM run_laps rl JOIN runs r ON r.id = rl.run_id
           WHERE r.season_id=? AND r.player_id=? AND r.course_id=? AND r.cc=?
             AND r.status='finished' AND rl.lap_index=?`
        ).get(season, playerId, courseId, cc, lapIdx) as { m: number | null }).m;
        entry!.golds.set(lapIdx, g);
      }
      return g;
    };
    const deltas: LapDeltaResult[] = [];
    const n = Math.min(splitsMs?.length ?? 0, entry.pbLaps.length);
    let live = 0, ref = 0;
    for (let i = 0; i < n; i++) {
      live += splitsMs![i]; ref += entry.pbLaps[i];
      const gold = goldFor(i + 1);
      deltas.push({
        lap: i + 1,
        delta_ms: live - ref,
        gained: splitsMs![i] < entry.pbLaps[i],
        gold: gold != null && splitsMs![i] < gold,
      });
    }
    return { pb_laps_ms: entry.pbLaps, deltas };
  }) as LapDelta;

  fn.invalidateCourse = (courseId: number) => {
    const prefix = `${courseId}:`;
    for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key);
  };
  return fn;
}
