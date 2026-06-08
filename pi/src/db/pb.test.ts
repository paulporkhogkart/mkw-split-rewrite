import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { openDb, applySchema } from './connect';
import { recomputeIsPb, recomputeWasPb, backfillWasPb } from './pb';

function withRuns() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  const ins = db.prepare("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at) VALUES (1,1,1,150,'finished','live',?,?)");
  ins.run(110000, '2026-01-01'); ins.run(108000, '2026-02-01'); ins.run(112000, '2026-03-01');
  return db;
}

describe('recomputeIsPb', () => {
  it('flags the single fastest finished run for the scope', () => {
    const db = withRuns();
    recomputeIsPb(db, 1, 1, 1, 150);
    const pbs = db.prepare('SELECT total_time_ms FROM runs WHERE is_pb=1').all() as { total_time_ms: number }[];
    expect(pbs.map(r => r.total_time_ms)).toEqual([108000]);
  });
});

function seedRun(db: DatabaseSync, id: number, ms: number, endedAt: string, status = 'finished') {
  db.prepare(
    `INSERT INTO runs(id, season_id, player_id, course_id, cc, status, provenance, ended_at, total_time_ms)
     VALUES (?, 1, 1, 1, 150, ?, 'live', ?, ?)`
  ).run(id, status, endedAt, status === 'finished' ? ms : null);
}

function baseDb(): DatabaseSync {
  const db = new DatabaseSync(':memory:');
  applySchema(db);
  db.exec(`INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1);
           INSERT INTO players(id,display_name) VALUES (1,'Paul');
           INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle');`);
  return db;
}

describe('recomputeWasPb', () => {
  it('flags the record progression: first finish + each strict improvement', () => {
    const db = baseDb();
    seedRun(db, 1, 160000, '2026-06-01T00:00:00+00:00');  // first -> PB
    seedRun(db, 2, 165000, '2026-06-02T00:00:00+00:00');  // slower -> not
    seedRun(db, 3, 160000, '2026-06-03T00:00:00+00:00');  // ties prev best -> not (strict)
    seedRun(db, 4, 158000, '2026-06-04T00:00:00+00:00');  // faster -> PB
    seedRun(db, 5, 0,      '2026-06-05T00:00:00+00:00', 'reset'); // reset -> never
    recomputeWasPb(db, 1, 1, 1, 150);
    const flags = db.prepare('SELECT id, was_pb FROM runs ORDER BY id').all();
    expect(flags).toEqual([
      { id: 1, was_pb: 1 }, { id: 2, was_pb: 0 }, { id: 3, was_pb: 0 },
      { id: 4, was_pb: 1 }, { id: 5, was_pb: 0 },
    ]);
  });
});

describe('backfillWasPb', () => {
  it('populates every (season,player,course,cc) group', () => {
    const db = baseDb();
    seedRun(db, 1, 160000, '2026-06-01T00:00:00+00:00');
    seedRun(db, 2, 158000, '2026-06-02T00:00:00+00:00');
    db.prepare('UPDATE runs SET was_pb=0').run(); // simulate pre-migration state
    backfillWasPb(db);
    const pbs = db.prepare('SELECT id FROM runs WHERE was_pb=1 ORDER BY id').all();
    expect(pbs).toEqual([{ id: 1 }, { id: 2 }]);
  });
});
