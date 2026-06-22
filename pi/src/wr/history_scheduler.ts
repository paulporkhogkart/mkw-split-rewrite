import type { DatabaseSync } from 'node:sqlite';
import { MKWRS_TRACKS, scrapeTrackHistory } from './history_scrape';

function getCursor(db: DatabaseSync): number {
  const row = db.prepare(`SELECT value FROM wr_meta WHERE key='history_cursor'`).get() as { value: string } | undefined;
  const n = row ? Number(row.value) : 0;
  return Number.isFinite(n) ? n : 0;
}

function setCursor(db: DatabaseSync, n: number): void {
  db.prepare(
    `INSERT INTO wr_meta(key, value) VALUES ('history_cursor', ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value`
  ).run(String(n));
}

export type HistorySchedulerOpts = {
  minIntervalSec: number;
  maxIntervalSec: number;
  tracks?: string[];
  scrapeTrack?: (db: DatabaseSync, track: string) => Promise<unknown>;
  random?: () => number;
};

/** Scrape ONE track per tick, round-robin via the persisted wr_meta.history_cursor, at a random
 *  interval in [min,max] re-rolled each cycle (looks like a person, near-zero request volume).
 *  maxIntervalSec <= 0 disables. Returns a stop function. */
export function startWrHistoryScraper(db: DatabaseSync, opts: HistorySchedulerOpts): () => void {
  const tracks = opts.tracks ?? MKWRS_TRACKS;
  const scrapeTrack = opts.scrapeTrack ?? ((d, t) => scrapeTrackHistory(d, t));
  const random = opts.random ?? Math.random;
  if (!opts.maxIntervalSec || opts.maxIntervalSec <= 0) return () => {};

  const lo = Math.max(0, Math.min(opts.minIntervalSec, opts.maxIntervalSec));
  const hi = Math.max(opts.minIntervalSec, opts.maxIntervalSec);
  const nextDelayMs = () => (lo + random() * (hi - lo)) * 1000;

  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = () => { if (!stopped) timer = setTimeout(() => void tick(), nextDelayMs()); };

  const tick = async () => {
    const i = getCursor(db) % tracks.length;
    try {
      await scrapeTrack(db, tracks[i]);
    } catch (e) {
      console.error('[wr-history] drip failed:', e);
    } finally {
      setCursor(db, (i + 1) % tracks.length);
      schedule();
    }
  };

  void tick();                                              // immediate first tick
  return () => { stopped = true; if (timer) clearTimeout(timer); };
}
