// Bit-compares every run's trail between two DBs, reading through the SAME access layer
// the server uses (blob or legacy rows) — proves a migrated DB serves exactly what the
// original served. Opens read-write so WAL-sidecar copies self-recover: run it on
// COPIES, never on a live DB. Usage: npm run diff-trails -- <a.db> <b.db>
import { DatabaseSync } from 'node:sqlite';
import { assertTrailsIdentical } from '../db/trailCodec';
import { getRunPoints } from '../db/trails';

function trailRunIds(db: DatabaseSync): number[] {
  const ids = new Set<number>();
  try { for (const r of db.prepare('SELECT DISTINCT run_id id FROM run_points').all() as { id: number }[]) ids.add(r.id); } catch { /* dropped */ }
  try { for (const r of db.prepare('SELECT run_id id FROM run_trails').all() as { id: number }[]) ids.add(r.id); } catch { /* pre-schema */ }
  return [...ids].sort((x, y) => x - y);
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
  let pts = 0;
  for (const id of idsA) {
    const ta = getRunPoints(dbA, id), tb = getRunPoints(dbB, id);
    try { assertTrailsIdentical(ta, tb); } catch (e) {
      console.error(`run ${id}: MISMATCH — ${(e as Error).message}`); process.exitCode = 1; return;
    }
    pts += ta.length;
  }
  console.log(`OK: ${idsA.length} runs, ${pts} points bit-identical.`);
}
main();
