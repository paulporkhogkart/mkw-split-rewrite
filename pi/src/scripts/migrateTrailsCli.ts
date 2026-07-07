// Manual run_points → run_trails migration (same routine the server runs at boot).
// Usage: npm run migrate-trails -- <path.db> [--vacuum]     (defaults to $MKW_DB / mkw.db)
import { openDb, applySchema } from '../db/connect';
import { migrateTrails } from '../db/trailMigrate';

const args = process.argv.slice(2).filter((a) => a !== '--vacuum');
const db = openDb(args[0] ?? process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const res = migrateTrails(db);
console.log(res);
if (process.argv.includes('--vacuum')) { console.log('VACUUM…'); db.exec('VACUUM'); }
if (res.failed > 0) process.exitCode = 1;
