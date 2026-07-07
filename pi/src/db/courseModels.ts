// pi/src/db/courseModels.ts
import type { DatabaseSync } from 'node:sqlite';
import type { CourseModel, Transform, RunInput } from '../progress/types';
import { buildCourseModel } from '../progress/build';
import { activeSeasonId } from './seasons';
import { getRunPoints } from './trails';

export function saveCourseModel(db: DatabaseSync, courseId: number, cc: number, m: CourseModel, sourceRuns: number): void {
  db.prepare(`INSERT INTO course_models(course_id, cc, model_json, lap_length_px, status, source_run_count, version, built_at)
     VALUES (?,?,?,?,?,?,?, datetime('now'))
     ON CONFLICT(course_id, cc) DO UPDATE SET model_json=excluded.model_json, lap_length_px=excluded.lap_length_px,
       status=excluded.status, source_run_count=excluded.source_run_count, version=excluded.version, built_at=excluded.built_at`)
    .run(courseId, cc, JSON.stringify(m), m.totalLengthPx, m.status, sourceRuns, m.version);
}

export function loadCourseModel(db: DatabaseSync, courseId: number, cc: number): CourseModel | null {
  const r = db.prepare('SELECT model_json FROM course_models WHERE course_id=? AND cc=?').get(courseId, cc) as { model_json: string } | undefined;
  return r ? (JSON.parse(r.model_json) as CourseModel) : null;
}

export function savePlayerAlignment(db: DatabaseSync, playerId: number, t: Transform, sampleCount: number): void {
  db.prepare(
    `INSERT INTO player_alignment(player_id, dx, dy, scale, updated_at, sample_count)
     VALUES (?,?,?,?, datetime('now'), ?)
     ON CONFLICT(player_id) DO UPDATE SET dx=excluded.dx, dy=excluded.dy, scale=excluded.scale,
       updated_at=excluded.updated_at, sample_count=excluded.sample_count`
  ).run(playerId, t.dx, t.dy, t.scale, sampleCount);
}

export function loadPlayerAlignment(db: DatabaseSync, playerId: number): Transform {
  const r = db.prepare('SELECT dx, dy, scale FROM player_alignment WHERE player_id=?').get(playerId) as Transform | undefined;
  return r ?? { dx: 0, dy: 0, scale: 1 };
}

/** Rebuild + persist the (course, cc) model from the latest <=window finished
 *  runs that carry points. Returns a summary or null when nothing usable.
 *  Shared by the build-course-model CLI and the run-upload auto-rebuild. */
export function rebuildCourseModel(db: DatabaseSync, courseId: number, cc: number,
                                   window = 40): { status: string; laps: number; runs: number } | null {
  const season = activeSeasonId(db);
  const runs = db.prepare(
    `SELECT r.id, r.player_id FROM runs r
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND EXISTS (SELECT 1 FROM run_trails p WHERE p.run_id=r.id)
     ORDER BY r.id DESC LIMIT ?`).all(season, courseId, cc, window) as { id: number; player_id: number }[];
  if (runs.length === 0) return null;
  const lapStmt = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index');
  const inputs: RunInput[] = runs.map((r) => {
    // run_laps.lap_time_ms is a per-lap DURATION; foldRun's `f` expects cumulative end-times.
    let c = 0;
    const cum = (lapStmt.all(r.id) as { lap_time_ms: number }[]).map((l) => (c += l.lap_time_ms));
    return { playerId: r.player_id, lapCumMs: cum, points: getRunPoints(db, r.id) as RunInput['points'] };
  });
  const res = buildCourseModel(inputs);
  if (!res) return null;
  saveCourseModel(db, courseId, cc, res.model, inputs.length);
  for (const a of res.alignments) savePlayerAlignment(db, a.playerId, a.transform, 1);
  return { status: res.model.status, laps: res.model.laps.length, runs: inputs.length };
}
