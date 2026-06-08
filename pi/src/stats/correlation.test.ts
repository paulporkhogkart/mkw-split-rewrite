import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DateTime } from 'luxon';
import { applySchema } from '../db/connect';
import { pearson, resolveCorrelation } from './correlation';
import { openPorker } from './body';
import { resolvePeriod } from './period';

describe('pearson', () => {
  it('a perfect line gives r=1 and the right slope/intercept', () => {
    const c = pearson([[16, 180], [18, 190], [20, 200]]); // y = 5x + 100
    expect(c.r).toBeCloseTo(1, 9);
    expect(c.slope).toBeCloseTo(5, 9);
    expect(c.intercept).toBeCloseTo(100, 9);
  });
  it('returns nulls below 2 points and on zero variance', () => {
    expect(pearson([[1, 2]]).r).toBeNull();
    expect(pearson([[5, 1], [5, 9]]).r).toBeNull(); // x constant
  });
});

const epoch = (iso: string) => Math.floor(DateTime.fromISO(iso, { zone: 'utc' }).toSeconds());
let dir: string, path: string;

function mkPorker(p: string) {
  const d = new DatabaseSync(p);
  d.exec(`CREATE TABLE "EunoraMeasurements" ("Timestamp" INTEGER,"Weight" REAL,"BodyMassIndex" REAL,"BodyFat" REAL,
    "FatFreeBodyWeight" REAL,"SubcutaneousFat" REAL,"VisceralFat" REAL,"BodyWater" REAL,"SkeletalMuscle" REAL,
    "MuscleMass" REAL,"BoneMass" REAL,"Protein" REAL,"BasalMetabolicRate" REAL,"MetabolicAge" REAL)`);
  const ins = (iso: string, fat: number) => d.prepare(
    `INSERT INTO "EunoraMeasurements" VALUES (?,80,22,?,60,15,5,55,50,50,3,18,1700,25)`).run(epoch(iso), fat);
  ins('2026-06-01T00:00:00', 20);
  ins('2026-06-02T00:00:00', 18);
  ins('2026-06-03T00:00:00', 16);
  d.close();
}

beforeAll(() => { dir = mkdtempSync(join(tmpdir(), 'corr-')); path = join(dir, 'porker.db'); mkPorker(path); });
afterAll(() => rmSync(dir, { recursive: true, force: true }));

function mkw(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES(1,'bc','BC');`);
  const run = (id: number, ms: number, ended: string) => d.prepare(
    `INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms)
     VALUES(?,1,1,1,150,'finished','live',?,?)`).run(id, ended, ms);
  run(0, 210000, '2026-05-01T00:00:00+00:00'); // before any weigh-in -> dropped
  run(1, 200000, '2026-06-01T00:00:00+00:00');
  run(2, 190000, '2026-06-02T00:00:00+00:00');
  run(3, 180000, '2026-06-03T00:00:00+00:00');
  return d;
}

describe('resolveCorrelation', () => {
  it('correlates finish time against as-of body fat (dropping pre-weigh-in runs)', () => {
    const pk = openPorker(path);
    const c = resolveCorrelation(mkw(), pk, {
      body: 'body_fat', player: 'Luke', course: 'bc',
      period: resolvePeriod('all_time', 'Australia/Melbourne'), seasonId: 1,
    });
    expect(c.n).toBe(3);              // run 0 dropped (no prior weigh-in)
    expect(c.r).toBeCloseTo(1, 6);    // fat down -> time down
    expect(c.slope).toBeCloseTo(5000, 3);
    pk.close();
  });
});
