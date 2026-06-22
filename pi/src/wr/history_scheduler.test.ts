import { describe, it, expect, vi } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { startWrHistoryScraper } from './history_scheduler';

function freshDb() { const db = openDb(':memory:'); applySchema(db); return db; }

describe('startWrHistoryScraper', () => {
  it('scrapes one track per tick round-robin and persists the cursor', async () => {
    vi.useFakeTimers();
    try {
      const db = freshDb();
      const seen: string[] = [];
      const scrapeTrack = vi.fn(async (_db: any, track: string) => { seen.push(track); });
      const stop = startWrHistoryScraper(db, {
        minIntervalSec: 100, maxIntervalSec: 100, random: () => 0,
        tracks: ['A', 'B', 'C'], scrapeTrack,
      });
      expect(seen).toEqual(['A']);                          // immediate first tick
      await vi.advanceTimersByTimeAsync(100_000);
      await vi.advanceTimersByTimeAsync(100_000);
      expect(seen).toEqual(['A', 'B', 'C']);
      const cur = db.prepare(`SELECT value FROM wr_meta WHERE key='history_cursor'`).get() as any;
      expect(cur.value).toBe('0');                          // wrapped back to start (3 % 3)
      stop();
    } finally { vi.useRealTimers(); }
  });

  it('is disabled when maxIntervalSec <= 0', () => {
    const scrapeTrack = vi.fn();
    const stop = startWrHistoryScraper(freshDb(), { minIntervalSec: 100, maxIntervalSec: 0, scrapeTrack });
    expect(scrapeTrack).not.toHaveBeenCalled();
    stop();
  });
});
