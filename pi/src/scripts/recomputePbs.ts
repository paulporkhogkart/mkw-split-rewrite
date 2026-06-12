// pi/src/scripts/recomputePbs.ts
// Recompute is_pb + was_pb for every (season, player, course, cc) scope that has runs.
// Run after any data surgery that bypasses the upload route - e.g. restoring the
// legacy carryover PBs with server/importer.py - so live runs flagged against an
// empty history (post-wipe) lose stale flags and mixed carryover/live scopes
// reconcile to a single PB. Usage: npm run recompute-pbs
import { openDb, applySchema } from '../db/connect';
import { recomputeIsPb, backfillWasPb } from '../db/pb';

function main() {
  const db = openDb(process.env.MKW_DB ?? 'mkw.db');
  applySchema(db);
  const scopes = db.prepare(
    'SELECT DISTINCT season_id, player_id, course_id, cc FROM runs'
  ).all() as { season_id: number; player_id: number; course_id: number; cc: number }[];
  for (const s of scopes) recomputeIsPb(db, s.season_id, s.player_id, s.course_id, s.cc);
  backfillWasPb(db);
  const pbs = (db.prepare('SELECT COUNT(*) AS n FROM runs WHERE is_pb=1').get() as { n: number }).n;
  const was = (db.prepare('SELECT COUNT(*) AS n FROM runs WHERE was_pb=1').get() as { n: number }).n;
  console.log(`${scopes.length} scopes recomputed: ${pbs} is_pb, ${was} was_pb`);
}
main();
