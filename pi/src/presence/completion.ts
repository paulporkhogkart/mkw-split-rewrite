// pi/src/presence/completion.ts
import type { DatabaseSync } from 'node:sqlite';
import { loadCourseModel, loadPlayerAlignment } from '../db/courseModels';
import { prepareEdges, projectStep, type Prepared } from '../progress/project';
import type { CourseGraph, ProjState } from '../progress/types';
import { courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export type LiveCompletion = (
  course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined,
  playerId?: number, t?: number, stale?: boolean, totLap?: number | null,
) => number | null;

/** Stateful live-completion: projects each player's minimap position onto the stored course
 *  model (cached per course). Resets a player's state on a new run (course change or lap CHANGE,
 *  so within-lap progress wraps at the start/finish seam) or when pos clears. Returns 0..1 or null. */
export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const modelCache = new Map<number, { g: CourseGraph; pe: Prepared } | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false, totLap = 3) => {
    if (!course || !pos) { if (playerId != null) pstate.delete(playerId); return null; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = modelCache.get(courseId);
    if (entry === undefined) {
      const g = loadCourseModel(db, courseId, cc);
      entry = g ? { g, pe: prepareEdges(g) } : null;
      modelCache.set(courseId, entry);
    }
    if (!entry) return null;
    const lap = curLap ?? 1;
    const al = playerId != null ? loadPlayerAlignment(db, playerId) : { dx: 0, dy: 0, scale: 1 };
    const x = pos[0] * al.scale + al.dx, y = pos[1] * al.scale + al.dy;
    let ps = playerId != null ? pstate.get(playerId) : undefined;
    // Reset on a new run OR an in-race lap change (wraps within-lap progress at the seam). Do NOT
    // reset once past the final lap (post-finish frames report lap > totLap): hold at 100% instead
    // of re-bootstrapping to ~0 as the coast wraps the position past the line.
    if (ps && (ps.course !== courseId || (lap !== ps.lap && lap <= (totLap ?? 3)))) ps = undefined;
    const r = projectStep(ps?.st ?? null, entry.g, entry.pe, { x, y, lap, totLap: totLap ?? 3, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return r.completion;
  };
}
