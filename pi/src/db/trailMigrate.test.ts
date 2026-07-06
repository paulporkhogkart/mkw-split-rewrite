import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { migrateTrails } from './trailMigrate';
import { getRunPoints, insertTrail } from './trails';
import type { TrailPoint } from './trailCodec';

// Legacy DDL built by the test itself: schema.sql stops creating run_points in a later
// task, and these tests must keep passing after that (IF NOT EXISTS covers both stages).
const LEGACY_DDL = `CREATE TABLE IF NOT EXISTS run_points (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t_ms INTEGER NOT NULL, cx REAL NOT NULL, cy REAL NOT NULL,
    score REAL NOT NULL DEFAULT 1.0, lap INTEGER);
  CREATE INDEX IF NOT EXISTS idx_run_points_run ON run_points(run_id);`;

function legacyDb(nRuns: number, ptsPerRun: number) {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec(LEGACY_DDL);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  const runStmt = db.prepare("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance) VALUES (?,?,1,1,1,150,'finished','live')");
  const ptStmt = db.prepare('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (?,?,?,?,?,?)');
  for (let r = 1; r <= nRuns; r++) {
    runStmt.run(r, `a${r}`);
    for (let i = 0; i < ptsPerRun; i++)
      ptStmt.run(r, i * 40, 100 + r + i * 0.34567, 200 - i * 0.11111, 0.5 + (i % 5) / 10, i % 7 === 0 ? null : 1 + (i % 3));
  }
  return db;
}
const legacyRows = (db: ReturnType<typeof openDb>, r: number) =>
  db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms').all(r) as TrailPoint[];

describe('migrateTrails', () => {
  it('migrates every run bit-exactly, deletes rows, drops the table', () => {
    const db = legacyDb(3, 50);
    const want = [1, 2, 3].map((r) => legacyRows(db, r));
    expect(migrateTrails(db)).toEqual({ migrated: 3, failed: 0, orphaned: 0, dropped: true });
    for (const r of [1, 2, 3]) expect(getRunPoints(db, r)).toEqual(want[r - 1]);
    expect(db.prepare("SELECT 1 FROM sqlite_master WHERE name='run_points'").get()).toBeUndefined();
  });

  it('is a no-op when run_points is absent (fresh or already-migrated DB)', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec('DROP TABLE IF EXISTS run_points');
    expect(migrateTrails(db)).toEqual({ migrated: 0, failed: 0, orphaned: 0, dropped: false });
  });

  it('resumes after an interrupted pass (already-blobbed runs skipped)', () => {
    const db = legacyDb(2, 10);
    const rows1 = legacyRows(db, 1);
    insertTrail(db, 1, rows1);                       // simulate prior pass on run 1…
    db.exec('DELETE FROM run_points WHERE run_id=1');
    const res = migrateTrails(db);
    expect(res.migrated).toBe(1);                     // …only run 2 migrates now
    expect(res.dropped).toBe(true);
    expect(getRunPoints(db, 1)).toEqual(rows1);
  });

  it('keeps rows + table when a run fails verification', () => {
    const db = legacyDb(2, 10);
    db.exec('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (1, 0, 9, 9, 1, 1)');  // duplicate t_ms=0 → encode throws
    const res = migrateTrails(db);
    expect(res.failed).toBe(1);
    expect(res.migrated).toBe(1);
    expect(res.dropped).toBe(false);
    expect((db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=1').get() as any).c).toBe(11);
    expect(getRunPoints(db, 2).length).toBe(10);      // run 2 serves from its blob
  });

  it('orphan rows (run_id not in runs) block the drop, are counted, never deleted', () => {
    const db = legacyDb(1, 5);
    db.exec('PRAGMA foreign_keys=OFF');
    db.exec('INSERT INTO run_points(run_id,t_ms,cx,cy,score,lap) VALUES (999, 0, 1, 1, 1, 1)');
    db.exec('PRAGMA foreign_keys=ON');
    expect(migrateTrails(db)).toEqual({ migrated: 1, failed: 0, orphaned: 1, dropped: false });
    expect((db.prepare('SELECT COUNT(*) c FROM run_points').get() as any).c).toBe(1);
  });

  it('rolls back the partial blob insert when the transaction fails mid-way', () => {
    const db = legacyDb(2, 10);
    // Fail run 2's row-delete AFTER its blob insert succeeded in the same txn:
    db.exec(`CREATE TRIGGER fail_del BEFORE DELETE ON run_points WHEN OLD.run_id = 2
             BEGIN SELECT RAISE(ABORT, 'simulated mid-txn failure'); END`);
    const res = migrateTrails(db);
    expect(res.migrated).toBe(1);
    expect(res.failed).toBe(1);
    expect(res.dropped).toBe(false);
    // ROLLBACK undid run 2's in-txn blob insert and kept all its rows:
    expect(db.prepare('SELECT COUNT(*) c FROM run_trails WHERE run_id=2').get()).toEqual({ c: 0 });
    expect(db.prepare('SELECT COUNT(*) c FROM run_points WHERE run_id=2').get()).toEqual({ c: 10 });
    expect(getRunPoints(db, 1).length).toBe(10);   // run 1 migrated normally
  });
});
