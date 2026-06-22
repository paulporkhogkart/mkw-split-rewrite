import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
resolveFlags(db);                                           // auto-clear any now-resolvable flags first
console.log(reportFlags(db));
