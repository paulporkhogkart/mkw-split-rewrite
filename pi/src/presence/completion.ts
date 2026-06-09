import type { DatabaseSync } from 'node:sqlite';
import { courseReference } from '../stats/completion';
import { step, type Reference, type ProjState } from '../stats/progress';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export type LiveCompletion = (
  course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined,
  playerId?: number, t?: number, stale?: boolean,
) => number | null;

/** A stateful live-completion function for the presence hub: projects each player's minimap
 *  position onto the course route (reference cached per course) with per-player continuity.
 *  Resets a player's state on a new run (course change or lap decrease) or when pos clears.
 *  Returns 0..1, or null with no position / no reference. cc fixed at 150 (live runs are 150). */
export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const refCache = new Map<number, Reference | null>();
  const pstate = new Map<number, { st: ProjState; course: number; lap: number }>();
  return (course, curLap, pos, playerId, t = Date.now(), stale = false) => {
    if (!course || !pos) { if (playerId != null) pstate.delete(playerId); return null; }
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = refCache.get(courseId);
    if (entry === undefined) { entry = courseReference(db, activeSeasonId(db), courseId, cc); refCache.set(courseId, entry); }
    if (!entry || entry.ref.length === 0) return null;
    const lap = curLap ?? 1;
    let ps = playerId != null ? pstate.get(playerId) : undefined;
    if (ps && (ps.course !== courseId || lap < ps.lap)) ps = undefined;   // new run -> reset
    const r = step(ps?.st ?? null, entry, { x: pos[0], y: pos[1], lap, t, stale });
    if (playerId != null) pstate.set(playerId, { st: r.state, course: courseId, lap });
    return r.s;
  };
}
