// pi/src/scripts/buildCourseModel.ts
import { openDb, applySchema } from '../db/connect';
import { activeSeasonId } from '../db/seasons';
import { buildCourseModel } from '../progress/build';
import { saveCourseModel, savePlayerAlignment } from '../db/courseModels';
import type { RunInput } from '../progress/types';

const arg = (k: string, d?: string) => {
  const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d;
};

function main() {
  const db = openDb(process.env.MKW_DB ?? 'mkw.db');
  applySchema(db);
  const slug = arg('--course');
  const cc = Number(arg('--cc', '150'));
  const window = Number(arg('--window', '40'));
  if (!slug) { console.error('usage: build-course-model --course <slug> [--cc 150] [--window 40]'); process.exitCode = 1; return; }

  const course = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  if (!course) { console.error(`unknown course: ${slug}`); process.exitCode = 1; return; }
  const season = activeSeasonId(db);

  const runs = db.prepare(
    `SELECT r.id, r.player_id FROM runs r
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND EXISTS (SELECT 1 FROM run_points p WHERE p.run_id=r.id)
     ORDER BY r.id DESC LIMIT ?`).all(season, course.id, cc, window) as { id: number; player_id: number }[];

  const ptsStmt = db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
  const lapStmt = db.prepare('SELECT lap_time_ms FROM run_laps WHERE run_id=? ORDER BY lap_index');
  const inputs: RunInput[] = runs.map((r) => {
    // run_laps.lap_time_ms is a per-lap DURATION (verified across runs: SUM(lap_time_ms) == runs.total_time_ms,
    // values non-monotonic). Accumulate into cumulative lap end-times, which is what foldRun's `f` expects.
    // (Do NOT treat these as already-cumulative splits - the reads.ts:myPbSplits docstring mislabels them.)
    let c = 0; const cum = (lapStmt.all(r.id) as { lap_time_ms: number }[]).map((l) => (c += l.lap_time_ms));
    return { playerId: r.player_id, lapCumMs: cum,
      points: ptsStmt.all(r.id) as RunInput['points'] };
  });

  const res = buildCourseModel(inputs);
  if (!res) { console.error(`no usable runs for ${slug}`); process.exitCode = 1; return; }
  // TODO(Task-5): replace with saveCourseModel accepting CourseModel v2.
  // For now, persist lap 1's graph so existing storage schema is not broken.
  const lap1Graph = res.model.laps[0].graph;
  saveCourseModel(db, course.id, cc, lap1Graph, inputs.length);
  for (const a of res.alignments) savePlayerAlignment(db, a.playerId, a.transform, 1);
  console.log(`[course-model] ${slug} cc${cc}: ${res.model.status}, laps=${res.model.laps.length}, ${inputs.length} runs, totalLen=${res.model.totalLengthPx.toFixed(0)}px`);
}
main();
