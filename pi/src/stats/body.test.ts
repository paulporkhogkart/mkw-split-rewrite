import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openPorker, resolveBody } from './body';
import { resolvePeriod } from './period';
import { DateTime } from 'luxon';

let dir: string, path: string;

function mkPorker(p: string) {
  const d = new DatabaseSync(p);
  const cols = `"Timestamp" INTEGER, "Weight" REAL, "BodyMassIndex" REAL, "BodyFat" REAL,
    "FatFreeBodyWeight" REAL, "SubcutaneousFat" REAL, "VisceralFat" REAL, "BodyWater" REAL,
    "SkeletalMuscle" REAL, "MuscleMass" REAL, "BoneMass" REAL, "Protein" REAL,
    "BasalMetabolicRate" REAL, "MetabolicAge" REAL`;
  for (const t of ['Measurements', 'EunoraMeasurements']) d.exec(`CREATE TABLE "${t}" (${cols})`);
  const ins = (t: string, ts: number, fat: number, muscle: number) => d.prepare(
    `INSERT INTO "${t}"("Timestamp","Weight","BodyMassIndex","BodyFat","FatFreeBodyWeight","SubcutaneousFat","VisceralFat","BodyWater","SkeletalMuscle","MuscleMass","BoneMass","Protein","BasalMetabolicRate","MetabolicAge")
     VALUES (?,80,22,?,60,15,5,55,50,?,3,18,1700,25)`).run(ts, fat, muscle);
  const day = (iso: string) => Math.floor(DateTime.fromISO(iso, { zone: 'utc' }).toSeconds());
  // Luke (Eunora): fat 20 -> 18 over June; Paul (Measurements): muscle 52 latest
  ins('EunoraMeasurements', day('2026-06-02T00:00:00'), 20, 49);
  ins('EunoraMeasurements', day('2026-06-20T00:00:00'), 18, 50);
  ins('Measurements', day('2026-06-05T00:00:00'), 16, 52);
  d.close();
}

beforeAll(() => { dir = mkdtempSync(join(tmpdir(), 'porker-')); path = join(dir, 'porker.db'); mkPorker(path); });
afterAll(() => rmSync(dir, { recursive: true, force: true }));

const june = () => resolvePeriod('this_month', 'Australia/Melbourne',
  { now: DateTime.fromISO('2026-06-15T12:00:00', { zone: 'Australia/Melbourne' }) });

describe('resolveBody', () => {
  it('change = last - first in window, per player', () => {
    const pk = openPorker(path);
    const r = resolveBody(pk, { metric: 'body_fat', agg: 'change', period: june(), filters: { player: 'Luke' } });
    expect(r.total).toBeCloseTo(-2, 5); // 18 - 20
    pk.close();
  });

  it('current (no player) sums latest across the roster', () => {
    const pk = openPorker(path);
    const r = resolveBody(pk, { metric: 'muscle_mass', agg: 'current', period: june(), filters: {} });
    expect(r.total).toBeCloseTo(102, 5); // Luke 50 + Paul 52
    pk.close();
  });
});
