// pi/src/presence/completion.ts
import type { DatabaseSync } from 'node:sqlite';
import { loadCourseModel, loadPlayerAlignment } from '../db/courseModels';
import { prepareModel, projectStep, type Prepared } from '../progress/project';
import type { CourseModel, ProjState } from '../progress/types';
import { courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export interface LiveResult { completion: number | null; dividers: number[]; }

export type LiveCompletion = (
  course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined,
  playerId?: number, t?: number, stale?: boolean, totLap?: number | null,
) => LiveResult;

/** The model's interior lap boundaries as completion fractions: laps[1..N-1].startOffsetPx / total.
 *  Lap 1 begins at 0 and the finish is 1.0 — neither is drawn. Constant per course model. */
function modelDividers(m: CourseModel): number[] {
  const total = m.totalLengthPx;
  if (!(total > 0)) return [];
  return m.laps.slice(1).map((l) => l.startOffsetPx / total);
}

/** Stateful live-completion: projects each player's minimap position onto the stored course
 *  model (cached per course). Completion is cumulative distance across all laps. Resets a player's
 *  state on a new run (course change) or an in-race lap change (so progress re-bootstraps onto the
 *  new lap's route at the seam); past the final lap it holds at 100%. `dividers` are the model's
 *  known interior lap boundaries, returned from the first frame (even before any position) so the
 *  bar shows its lap segments from the start. Returns { completion: 0..1 | null, dividers }. */
export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const modelCache = new Map<number, { m: CourseModel; pe: Prepared; dividers: number[] } | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false, totLap) => {
    if (!course) { if (playerId != null) pstate.delete(playerId); return { completion: null, dividers: [] }; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return { completion: null, dividers: [] };
    let entry = modelCache.get(courseId);
    if (entry === undefined) {
      const m = loadCourseModel(db, courseId, cc);
      entry = m ? { m, pe: prepareModel(m), dividers: modelDividers(m) } : null;
      modelCache.set(courseId, entry);
    }
    if (!entry) return { completion: null, dividers: [] };
    const dividers = entry.dividers;
    if (!pos) { if (playerId != null) pstate.delete(playerId); return { completion: null, dividers }; }
    const lap = curLap ?? 1;
    const N = entry.m.laps.length;
    const al = playerId != null ? loadPlayerAlignment(db, playerId) : { dx: 0, dy: 0, scale: 1 };
    const x = pos[0] * al.scale + al.dx, y = pos[1] * al.scale + al.dy;
    const ps = playerId != null ? pstate.get(playerId) : undefined;
    let seed: ProjState = ps?.st ?? null;
    if (ps && ps.course === courseId) {
      // Crossing the line (lap up, in-race) seeds the NEW lap at progress 0 — its start. A blind
      // reset would let the global-nearest bootstrap snap to the lap's f≈1 END instead, which sits on
      // the very same start/finish line, freezing the bar near the boundary for the whole lap.
      if (lap > ps.lap && lap <= N) seed = { edge: 0, progress: 0, x, y, t };
      else if (lap < ps.lap) seed = null;                                  // restart / new run -> cold bootstrap
    } else if (ps) seed = null;                                            // course changed -> cold bootstrap
    const r = projectStep(seed, entry.m, entry.pe, { x, y, lap, totLap: totLap ?? N, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return { completion: r.completion, dividers };
  };
}
