// pi/src/presence/completion.ts
import type { DatabaseSync } from 'node:sqlite';
import { loadCourseModel, loadPlayerAlignment } from '../db/courseModels';
import { prepareModel, projectStep, type Prepared } from '../progress/project';
import type { CourseModel, ProjState } from '../progress/types';
import { courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export type LiveCompletion = (
  course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined,
  playerId?: number, t?: number, stale?: boolean, totLap?: number | null,
) => number | null;

/** Stateful live-completion: projects each player's minimap position onto the stored course
 *  model (cached per course). Completion is cumulative distance across all laps. Resets a player's
 *  state on a new run (course change) or an in-race lap change (so progress re-bootstraps onto the
 *  new lap's route at the seam); past the final lap it holds at 100%. Returns 0..1 or null. */
export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const modelCache = new Map<number, { m: CourseModel; pe: Prepared } | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false, totLap) => {
    if (!course || !pos) { if (playerId != null) pstate.delete(playerId); return null; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = modelCache.get(courseId);
    if (entry === undefined) { const m = loadCourseModel(db, courseId, cc); entry = m ? { m, pe: prepareModel(m) } : null; modelCache.set(courseId, entry); }
    if (!entry) return null;
    const lap = curLap ?? 1;
    const N = entry.m.laps.length;
    const al = playerId != null ? loadPlayerAlignment(db, playerId) : { dx: 0, dy: 0, scale: 1 };
    const x = pos[0] * al.scale + al.dx, y = pos[1] * al.scale + al.dy;
    let ps = playerId != null ? pstate.get(playerId) : undefined;
    if (ps && (ps.course !== courseId || (lap !== ps.lap && lap <= N))) ps = undefined;   // reset on new run OR in-race lap change
    const r = projectStep(ps?.st ?? null, entry.m, entry.pe, { x, y, lap, totLap: totLap ?? N, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return r.completion;
  };
}
