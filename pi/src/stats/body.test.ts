import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DateTime } from 'luxon';
import { PORKER_MAP, openPorker, resolveBody } from './body';
import { resolvePeriod } from './period';

const epoch = (iso: string) => Math.floor(DateTime.fromISO(iso, { zone: 'utc' }).toSeconds());
let dir: string, path: string;

// Paul's porker `person` key is 'paul'; his players.display_name was renamed 'Paul' -> 'paul pork'
// (see db/playerRenames.ts). PORKER_MAP.player must track that rename or he silently drops out of
// every body / correlation / body_condition stat.
const PORKER_SCHEMA = `CREATE TABLE measurements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, person TEXT NOT NULL, timestamp INTEGER NOT NULL,
  weight REAL, body_mass_index REAL, body_fat REAL, fat_free_body_weight REAL,
  subcutaneous_fat REAL, visceral_fat REAL, body_water REAL, skeletal_muscle REAL,
  muscle_mass REAL, bone_mass REAL, protein REAL, basal_metabolic_rate REAL, metabolic_age REAL,
  UNIQUE(person, timestamp))`;
const PORKER_COLS = `person,timestamp,weight,body_mass_index,body_fat,fat_free_body_weight,subcutaneous_fat,visceral_fat,body_water,skeletal_muscle,muscle_mass,bone_mass,protein,basal_metabolic_rate,metabolic_age`;

function mkPorker(p: string) {
  const d = new DatabaseSync(p);
  d.exec(PORKER_SCHEMA);
  d.prepare(`INSERT INTO measurements (${PORKER_COLS}) VALUES ('paul',?,90,27,25,65,20,8,50,45,48,3,17,1800,30)`).run(epoch('2026-06-01T00:00:00'));
  d.close();
}

beforeAll(() => { dir = mkdtempSync(join(tmpdir(), 'body-')); path = join(dir, 'porker.db'); mkPorker(path); });
afterAll(() => rmSync(dir, { recursive: true, force: true }));

describe('PORKER_MAP identity bridge', () => {
  it('maps the "paul" person to the renamed display name "paul pork"', () => {
    const entry = PORKER_MAP.find((m) => m.person === 'paul');
    expect(entry?.player).toBe('paul pork');
  });

  it("surfaces paul pork's BMI keyed by his current display name, and matches a player filter", () => {
    const pk = openPorker(path);
    const period = resolvePeriod('all_time', 'Australia/Melbourne');

    const all = resolveBody(pk, { metric: 'bmi', agg: 'current', period, filters: {} });
    expect(all.rows.map((r) => r.key)).toContain('paul pork');
    expect(all.rows.find((r) => r.key === 'paul pork')?.value).toBe(27);

    const filtered = resolveBody(pk, { metric: 'bmi', agg: 'current', period, filters: { player: 'paul pork' } });
    expect(filtered.rows).toEqual([{ key: 'paul pork', value: 27 }]);

    pk.close();
  });
});
