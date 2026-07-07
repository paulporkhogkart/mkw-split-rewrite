// pi/src/scripts/wipeRuns.ts
// Deletes ALL recorded runs (run_laps/run_trails cascade), course models and
// player alignments. Players/seasons/rosters/courses/world_records survive.
// Usage: npm run wipe-runs -- --confirm
import { openDb, applySchema } from '../db/connect';

function count(db: ReturnType<typeof openDb>, table: string): number {
  return (db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
}

function main() {
  if (!process.argv.includes('--confirm')) {
    console.error('refusing without --confirm (this deletes ALL runs, models and alignments)');
    process.exitCode = 1;
    return;
  }
  const db = openDb(process.env.MKW_DB ?? 'mkw.db');
  applySchema(db);
  const tables = ['runs', 'run_laps', 'run_trails', 'course_models', 'player_alignment'];
  const before = Object.fromEntries(tables.map((t) => [t, count(db, t)]));
  db.exec('BEGIN');
  try {
    db.exec('DELETE FROM runs');             // run_laps + run_trails cascade
    db.exec('DELETE FROM course_models');
    db.exec('DELETE FROM player_alignment');
    db.exec('COMMIT');
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
  for (const t of tables) console.log(`${t}: ${before[t]} -> ${count(db, t)}`);
}
main();
