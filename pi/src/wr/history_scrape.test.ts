import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { openDb, applySchema } from '../db/connect';
import { trackUrl, scrapeTrackHistory, scrapeAllHistory, MKWRS_TRACKS } from './history_scrape';

const fx = (name: string) =>
  readFileSync(new URL(`./__fixtures__/history/${name}.html`, import.meta.url), 'utf8');

function dbWith(slug: string, name: string) {
  const db = openDb(':memory:');
  applySchema(db);
  db.prepare(`INSERT INTO courses(id, slug, display_name) VALUES (7,?,?)`).run(slug, name);
  return db;
}

describe('trackUrl', () => {
  it('encodes spaces as + and ? as %3F', () => {
    expect(trackUrl('Great ? Block Ruins')).toBe('https://mkwrs.com/mkworld/display.php?track=Great+%3F+Block+Ruins');
    expect(trackUrl("Toad's Factory")).toContain('Toad%27s+Factory');
  });
  it('lists all 30 tracks', () => { expect(MKWRS_TRACKS.length).toBe(30); });
});

describe('scrapeTrackHistory', () => {
  it('parses + reconciles a fixture via injected fetch', async () => {
    const db = dbWith('rainbow_road', 'Rainbow Road');
    const rep = await scrapeTrackHistory(db, 'Rainbow Road', { fetchHtml: async () => fx('rainbow_road') });
    expect(rep.inserted).toBeGreaterThan(100);
    const cur = db.prepare(`SELECT kart_slug FROM world_records WHERE course_id=7 AND is_current=1`).get() as any;
    expect(cur.kart_slug).toBe('big_horn');
  });
});

describe('scrapeAllHistory', () => {
  it('runs sequentially with no real delay (sleep injected)', async () => {
    const db = dbWith('mario_bros_circuit', 'Mario Bros. Circuit');
    const reps = await scrapeAllHistory(db, {
      fetchHtml: async () => fx('mario_bros_circuit'),
      sleep: async () => {}, random: () => 0,
    });
    expect(reps.length).toBe(30);
    // Only Mario Bros. Circuit maps to a course here; others resolve to null and are skipped cleanly.
    const mbc = reps.find((r) => r.course === 'Mario Bros. Circuit');
    expect(mbc!.inserted).toBeGreaterThan(80);
  });
});
