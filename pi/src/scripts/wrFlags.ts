import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';
import { backfillSlugs } from '../wr/backfillSlugs';
import { stuckJobs } from '../db/wrJobs';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const resolved = resolveFlags(db);
const filled = backfillSlugs(db);
if (resolved) console.log(`resolved ${resolved} flag(s)`);
if (filled) console.log(`backfilled slugs on ${filled} world_records row(s)`);
console.log(reportFlags(db));

const stuck = stuckJobs(db);
if (stuck.length) {
  console.log(`\n${stuck.length} stuck WR trail job(s) — retrying on cooldown (time_mismatch: parked until the mkwrs link changes):`);
  for (const d of stuck) {
    console.log(`  wr_id=${d.wr_id} ${d.course} ${d.record_str}${d.holder_name ? ` by ${d.holder_name}` : ''} — attempts=${d.attempts} last_error=${d.last_error ?? '-'}`);
  }
}

// Trail coverage: the direct answer to "did we process a WR for every course yet?"
type CovRow = { course: string; cc: number; record_str: string; holder_name: string | null;
  video_url: string | null; trailed: number };
const cov = db.prepare(
  `SELECT c.display_name AS course, w.cc, w.record_str, w.holder_name, w.video_url,
          EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = w.id) AS trailed
   FROM world_records w JOIN courses c ON c.id = w.course_id
   WHERE w.is_current = 1 AND w.removed_at IS NULL
   ORDER BY c.display_name, w.cc`
).all() as unknown as CovRow[];
const missing = cov.filter((r) => !r.trailed);
console.log(`\ntrail coverage: ${cov.length - missing.length}/${cov.length} current WRs have a trail`);
for (const m of missing) {
  const why = m.video_url ? 'queued' : 'NO VIDEO on mkwrs — cannot be processed';
  console.log(`  missing: ${m.course} ${m.cc}cc ${m.record_str}${m.holder_name ? ` by ${m.holder_name}` : ''} (${why})`);
}
