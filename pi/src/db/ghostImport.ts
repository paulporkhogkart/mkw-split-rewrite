import type { DatabaseSync } from 'node:sqlite';

export interface GhostAudit {
  runId: number | null; playerId: number; courseId: number; cc: number;
  totalMs: number | null; action: 'enriched' | 'new';
}

/** Append a durable audit row recording that a run was submitted via ghost import. */
export function recordGhostImport(db: DatabaseSync, a: GhostAudit): void {
  db.prepare(
    'INSERT INTO ghost_imports(run_id, player_id, course_id, cc, total_time_ms, action) VALUES (?,?,?,?,?,?)'
  ).run(a.runId, a.playerId, a.courseId, a.cc, a.totalMs, a.action);
}
