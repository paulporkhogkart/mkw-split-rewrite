// pi/src/presence/pace.ts
// Live ahead/behind-PB delta: pb_delta_ms = elapsed_ms - (earliest race-clock time the
// player's PB run reached the same completion). The PB trail is replayed through the SAME
// projector as the live path (per-point lap stamp, lap-up seeding, current player alignment),
// so both clocks share the race-clock origin (t=0=GO) and the same completion frame.
import type { DatabaseSync } from 'node:sqlite';
import { loadCourseModel, loadPlayerAlignment } from '../db/courseModels';
import { getRunPoints } from '../db/trails';
import { prepareModel, projectStep, type Prepared } from '../progress/project';
import type { CourseModel, ProjState } from '../progress/types';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { pbRunFor } from '../db/pb';

export type PaceDelta = ((
  playerId: number, course: string | null | undefined,
  completion: number | null | undefined, elapsedMs: number | null | undefined,
) => number | null) & {
  /** Drop a course's cached model + pace curves (call alongside the live-completion
   *  invalidation after a model rebuild — rebuilds also refresh player alignments). */
  invalidateCourse(courseId: number): void;
};

interface Curve { t: number[]; c: number[] }   // knots: strictly increasing completion

/** Earliest PB time at `comp`: clamp to the curve's range, then lerp between knots.
 *  Flats in the replay were collapsed to their first timestamp, so this is the
 *  standard ghost-delta (earliest-crossing) inversion. */
function timeAt(curve: Curve, comp: number): number {
  const { t, c } = curve;
  const n = c.length;
  if (comp <= c[0]) return t[0];
  if (comp >= c[n - 1]) return t[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (c[mid] <= comp) lo = mid; else hi = mid; }
  const f = (comp - c[lo]) / (c[hi] - c[lo] || 1);
  return t[lo] + f * (t[hi] - t[lo]);
}

export function makePaceDelta(db: DatabaseSync, cc = 150): PaceDelta {
  const models = new Map<number, { m: CourseModel; pe: Prepared } | null>();
  // Keyed per (course, player); `runId` pins the curve to the PB run that produced it, so a
  // new PB is picked up on the next frame without any external invalidation. `curve: null`
  // remembers an unbuildable PB (no trail / degenerate projection) until the PB changes.
  const curves = new Map<string, { runId: number; curve: Curve | null }>();

  const model = (courseId: number) => {
    let e = models.get(courseId);
    if (e === undefined) {
      const m = loadCourseModel(db, courseId, cc);
      e = m ? { m, pe: prepareModel(m) } : null;
      models.set(courseId, e);
    }
    return e;
  };

  /** Replay the PB trail through the projector -> strictly-increasing (t_ms, completion)
   *  knots. Mirrors the live hub: per-point lap stamp (run_laps fallback), lap-up seeded at
   *  the new lap's start carrying rate/pub, current alignment, stale=false. */
  const buildCurve = (courseId: number, playerId: number, runId: number, totalMs: number): Curve | null => {
    const entry = model(courseId);
    if (!entry) return null;
    const pts = getRunPoints(db, runId);
    if (pts.length === 0) return null;
    const lapRows = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index')
      .all(runId) as { lap_time_ms: number }[];
    let acc = 0;
    const cum = lapRows.map((l) => (acc += l.lap_time_ms));
    const lapOf = (t: number) => { let L = 1; for (const b of cum) { if (t >= b) L++; else break; } return L; };
    const N = entry.m.laps.length;
    const al = loadPlayerAlignment(db, playerId);
    let st: ProjState = null, prevLap = 0, last = -Infinity;
    const t: number[] = [], c: number[] = [];
    for (const p of pts) {
      const lap = p.lap ?? lapOf(p.t_ms);
      const x = p.cx * al.scale + al.dx, y = p.cy * al.scale + al.dy;
      if (st && lap !== prevLap)
        st = (lap > prevLap && lap <= N)
          ? { edge: 0, progress: 0, x, y, t: p.t_ms, rate: st.rate ?? null, pub: st.pub }
          : null;
      const r = projectStep(st, entry.m, entry.pe, { x, y, lap, totLap: N, t: p.t_ms, stale: false });
      st = r.state; prevLap = lap;
      if (r.completion != null && r.completion > last) { t.push(p.t_ms); c.push(r.completion); last = r.completion; }
    }
    if (c.length < 2 || last < 0.5) return null;   // degenerate trail -> no delta
    // Pin the finish to the digit-read total so near-the-line lookups lerp onto ground truth
    // (the trail's last fix can sit slightly short of the line). If completion saturated at
    // 1.0 early, keep the earliest crossing instead.
    if (last < 1 && totalMs > t[t.length - 1]) { t.push(totalMs); c.push(1); }
    return { t, c };
  };

  const fn = ((playerId, course, completion, elapsedMs) => {
    if (!course || completion == null || elapsedMs == null) return null;
    const courseId = courseIdBySlug(db, slugify(course));
    if (courseId == null) return null;
    const pb = pbRunFor(db, activeSeasonId(db), playerId, courseId, cc);
    if (!pb) return null;
    const key = `${courseId}:${playerId}`;
    let entry = curves.get(key);
    if (!entry || entry.runId !== pb.id) {
      entry = { runId: pb.id, curve: buildCurve(courseId, playerId, pb.id, pb.total_time_ms) };
      curves.set(key, entry);
    }
    if (!entry.curve) return null;
    return Math.round(elapsedMs - timeAt(entry.curve, completion));
  }) as PaceDelta;

  fn.invalidateCourse = (courseId: number) => {
    models.delete(courseId);
    const prefix = `${courseId}:`;
    for (const k of [...curves.keys()]) if (k.startsWith(prefix)) curves.delete(k);
  };
  return fn;
}
