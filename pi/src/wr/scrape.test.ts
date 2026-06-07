import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import { scrapeOnce } from './scrape';
import { seedCanonicalCourses } from './__fixtures__/courses';

const html = readFileSync(new URL('./__fixtures__/mkworld.html', import.meta.url), 'utf8');

describe('scrapeOnce', () => {
  it('drives parse->reconcile from an injected fetcher and seeds all current WRs', async () => {
    const db = openDb(':memory:');
    applySchema(db);
    seedCanonicalCourses(db);
    const rep = await scrapeOnce(db, new EventHub(), { fetchHtml: async () => html });
    // First scrape of a freshly seeded DB inserts a current WR for each parsed course.
    expect(rep.inserted).toBe(30);
    expect(rep.unmapped).toEqual([]);
    const currents = db.prepare('SELECT COUNT(*) c FROM world_records WHERE is_current=1').get() as { c: number };
    expect(currents.c).toBe(30);
  });

  it('is idempotent across two runs', async () => {
    const db = openDb(':memory:');
    applySchema(db);
    seedCanonicalCourses(db);
    await scrapeOnce(db, new EventHub(), { fetchHtml: async () => html });
    const rep = await scrapeOnce(db, new EventHub(), { fetchHtml: async () => html });
    expect(rep.inserted).toBe(0);
    expect(rep.reflagged).toBe(0);
    expect(rep.unchanged).toBe(30);
  });
});
