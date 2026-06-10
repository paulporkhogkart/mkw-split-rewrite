// pi/src/db/courseModels.ts
import type { DatabaseSync } from 'node:sqlite';
import type { CourseGraph, Transform } from '../progress/types';

export function saveCourseModel(db: DatabaseSync, courseId: number, cc: number, g: CourseGraph, sourceRuns: number): void {
  db.prepare(
    `INSERT INTO course_models(course_id, cc, model_json, lap_length_px, status, source_run_count, version, built_at)
     VALUES (?,?,?,?,?,?,?, datetime('now'))
     ON CONFLICT(course_id, cc) DO UPDATE SET
       model_json=excluded.model_json, lap_length_px=excluded.lap_length_px, status=excluded.status,
       source_run_count=excluded.source_run_count, version=excluded.version, built_at=excluded.built_at`
  ).run(courseId, cc, JSON.stringify(g), g.lapLengthPx, g.status, sourceRuns, g.version);
}

export function loadCourseModel(db: DatabaseSync, courseId: number, cc: number): CourseGraph | null {
  const r = db.prepare('SELECT model_json FROM course_models WHERE course_id=? AND cc=?').get(courseId, cc) as { model_json: string } | undefined;
  return r ? (JSON.parse(r.model_json) as CourseGraph) : null;
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
