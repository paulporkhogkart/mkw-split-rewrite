import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';
import { backfillSlugs } from '../wr/backfillSlugs';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const resolved = resolveFlags(db);
const filled = backfillSlugs(db);
if (resolved) console.log(`resolved ${resolved} flag(s)`);
if (filled) console.log(`backfilled slugs on ${filled} world_records row(s)`);
console.log(reportFlags(db));
