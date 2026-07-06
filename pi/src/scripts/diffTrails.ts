// Bit-compares every run's trail between two DBs, reading through the SAME access layer
// the server uses (blob or legacy rows) — proves a migrated DB serves exactly what the
// original served. Opens read-write so WAL-sidecar copies self-recover: run it on
// COPIES, never on a live DB. Usage: npm run diff-trails -- <a.db> <b.db>
import { DatabaseSync } from 'node:sqlite';
import { assertTrailsIdentical } from '../db/trailCodec';
import type { TrailPoint } from '../db/trailCodec';
import { getRunPoints } from '../db/trails';

function trailRunIds(db: DatabaseSync): number[] {
  const ids = new Set<number>();
  try { for (const r of db.prepare('SELECT DISTINCT run_id id FROM run_points').all() as { id: number }[]) ids.add(r.id); } catch { /* dropped */ }
  try { for (const r of db.prepare('SELECT run_id id FROM run_trails').all() as { id: number }[]) ids.add(r.id); } catch { /* pre-schema */ }
  return [...ids].sort((x, y) => x - y);
}

function hasTable(db: DatabaseSync, name: string): boolean {
  return !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
}

/** Per-DB trail reader: normal DBs go through the production access layer; a pre-schema
 *  DB (e.g. a pre-deploy backup with only legacy run_points) is read directly. */
function makeReader(db: DatabaseSync): (id: number) => TrailPoint[] {
  if (hasTable(db, 'run_trails')) return (id) => getRunPoints(db, id);
  const stmt = db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
  return (id) => stmt.all(id) as TrailPoint[];
}

function main() {
  const [a, b] = process.argv.slice(2);
  if (!a || !b) { console.error('usage: npm run diff-trails -- <a.db> <b.db>'); process.exitCode = 1; return; }
  const dbA = new DatabaseSync(a);   // read-write on purpose: recovers a copied WAL sidecar
  const dbB = new DatabaseSync(b);
  const idsA = trailRunIds(dbA), idsB = trailRunIds(dbB);
  if (idsA.length !== idsB.length || idsA.some((v, i) => v !== idsB[i])) {
    console.error(`run-id sets differ: ${idsA.length} vs ${idsB.length}`); process.exitCode = 1; return;
  }
  const readA = makeReader(dbA);
  const readB = makeReader(dbB);
  let pts = 0;
  for (const id of idsA) {
    const ta = readA(id), tb = readB(id);
    try { assertTrailsIdentical(ta, tb); } catch (e) {
      console.error(`run ${id}: MISMATCH — ${(e as Error).message}`); process.exitCode = 1; return;
    }
    pts += ta.length;
  }
  console.log(`OK: ${idsA.length} runs, ${pts} points bit-identical.`);
}
main();
