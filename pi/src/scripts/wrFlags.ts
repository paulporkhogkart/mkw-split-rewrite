import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';
import { backfillSlugs } from '../wr/backfillSlugs';
import { deadJobs } from '../db/wrJobs';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const resolved = resolveFlags(db);
const filled = backfillSlugs(db);
if (resolved) console.log(`resolved ${resolved} flag(s)`);
if (filled) console.log(`backfilled slugs on ${filled} world_records row(s)`);
console.log(reportFlags(db));

const dead = deadJobs(db);
if (dead.length) {
  console.log(`\n${dead.length} dead WR trail job(s) — will not retry without a human (or a corrected mkwrs link):`);
  for (const d of dead) {
    console.log(`  wr_id=${d.wr_id} ${d.course} ${d.record_str}${d.holder_name ? ` by ${d.holder_name}` : ''} — attempts=${d.attempts} last_error=${d.last_error ?? '-'}`);
  }
}
