import type { DatabaseSync } from 'node:sqlite';
import { courseReference, completionFraction, type RefEntry } from '../stats/completion';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

export type LiveCompletion = (course: string | null | undefined, curLap: number | null | undefined, pos: [number, number] | null | undefined) => number | null;

/** A live-completion function for the presence hub: projects a player's current minimap
 *  position onto the course's reference path (cached per course), lap-gated by their current
 *  lap. Returns 0..1, or null when there's no position / no reference yet. cc fixed at 150
 *  (the competition cc; live runs are 150). Reference is cached - a course route is stable. */
export function makeLiveCompletion(db: DatabaseSync, cc = 150): LiveCompletion {
  const cache = new Map<number, RefEntry | null>();
  return (course, curLap, pos) => {
    if (!course || !pos) return null;
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    let entry = cache.get(courseId);
    if (entry === undefined) { entry = courseReference(db, activeSeasonId(db), courseId, cc); cache.set(courseId, entry); }
    if (!entry || entry.ref.length === 0) return null;
    const completed = curLap != null ? Math.max(0, curLap - 1) : 0;
    const lowerS = completed > 0 && completed <= entry.bounds.length ? entry.bounds[completed - 1] : 0;
    return completionFraction(entry.ref, lowerS, pos[0], pos[1]);
  };
}
