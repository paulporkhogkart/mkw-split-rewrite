import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { openDb, applySchema } from '../db/connect';
import { scrapeTrackHistory } from './history_scrape';
import { resolveFlags, reportFlags } from './flags';

const fx = (name: string) =>
  readFileSync(new URL(`./__fixtures__/history/${name}.html`, import.meta.url), 'utf8');

const FIXTURES: [string, string][] = [
  ['mario_bros_circuit', 'Mario Bros. Circuit'],
  ['mario_circuit', 'Mario Circuit'],
  ['rainbow_road', 'Rainbow Road'],
  ['koopa_troopa_beach', 'Koopa Troopa Beach'],
  ['dk_spaceport', 'DK Spaceport'],
];

describe('history e2e over all 5 fixtures', () => {
  it('parses + reconciles every variant with zero unresolved flags and one current per course', async () => {
    const db = openDb(':memory:');
    applySchema(db);
    for (let i = 0; i < FIXTURES.length; i++) {
      const [fixture, name] = FIXTURES[i];
      const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      db.prepare(`INSERT INTO courses(id, slug, display_name) VALUES (?,?,?)`).run(i + 1, slug, name);
      const rep = await scrapeTrackHistory(db, name, { fetchHtml: async () => fx(fixture) });
      expect(rep.inserted).toBeGreaterThan(80);
      const currents = db.prepare(
        `SELECT COUNT(*) c FROM world_records w JOIN courses c2 ON c2.id=w.course_id
         WHERE c2.display_name=? AND w.is_current=1`).get(name) as any;
      expect(currents.c).toBe(1);
    }
    resolveFlags(db);
    expect(reportFlags(db)).toBe('No unresolved name flags.');   // 3 kart aliases cover everything
  });
});
