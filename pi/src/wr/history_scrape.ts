import type { DatabaseSync } from 'node:sqlite';
import { parseHistory } from './history_parse';
import { reconcileHistory, type HistoryReport } from './history_reconcile';
import { resolveCourseId } from './courses';

/** The 30 base mkwrs track names (display form). Order matches the mkworld nav. */
export const MKWRS_TRACKS: string[] = [
  'Mario Bros. Circuit', 'Crown City', 'Whistlestop Summit', 'DK Spaceport', 'Desert Hills',
  'Shy Guy Bazaar', 'Wario Stadium', 'Airship Fortress', 'DK Pass', 'Starview Peak',
  'Sky-High Sundae', 'Wario Shipyard', 'Koopa Troopa Beach', 'Faraway Oasis', 'Peach Stadium',
  'Peach Beach', 'Salty Salty Speedway', 'Dino Dino Jungle', 'Great ? Block Ruins',
  'Cheep Cheep Falls', 'Dandelion Depths', 'Boo Cinema', 'Dry Bones Burnout', 'Moo Moo Meadows',
  'Choco Mountain', "Toad's Factory", "Bowser's Castle", 'Acorn Heights', 'Mario Circuit',
  'Rainbow Road',
];

export const DEFAULT_BASE = 'https://mkwrs.com/mkworld/display.php?track=';

/** Build the display.php URL: spaces → +, ? → %3F, ' → %27 (matches mkwrs's own links). */
export function trackUrl(name: string): string {
  return DEFAULT_BASE + encodeURIComponent(name).replace(/%20/g, '+').replace(/'/g, '%27');
}

async function politeFetch(url: string): Promise<string> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), 30_000);       // manual timer (Windows-safe teardown)
  try {
    const res = await fetch(url, {
      signal: ac.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        Referer: 'https://mkwrs.com/mkworld/',
      },
    });
    if (!res.ok) throw new Error(`mkwrs history fetch failed: HTTP ${res.status} for ${url}`);
    return await res.text();
  } finally { clearTimeout(timer); }
}

export type ScrapeTrackOpts = { cc?: number; fetchHtml?: (url: string) => Promise<string> };

/** Fetch + parse + reconcile one track. Unmapped (glitch/unknown) → empty report. */
export async function scrapeTrackHistory(db: DatabaseSync, track: string, opts: ScrapeTrackOpts = {}): Promise<HistoryReport> {
  const cc = opts.cc ?? 150;
  const courseId = resolveCourseId(db, track);
  if (courseId === null) return { course: track, inserted: 0, enriched: 0, unchanged: 0, removed: 0, flagged: 0 };
  const html = await (opts.fetchHtml ?? politeFetch)(trackUrl(track));
  return reconcileHistory(db, courseId, track, cc, parseHistory(html));
}

export type ScrapeAllOpts = ScrapeTrackOpts & {
  minDelayMs?: number; maxDelayMs?: number;
  random?: () => number; sleep?: (ms: number) => Promise<void>;
  log?: (m: string) => void;
};

const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Scrape all 30 tracks sequentially, with a randomized polite delay between requests. */
export async function scrapeAllHistory(db: DatabaseSync, opts: ScrapeAllOpts = {}): Promise<HistoryReport[]> {
  const { minDelayMs = 20_000, maxDelayMs = 60_000, random = Math.random, sleep = realSleep, log = () => {} } = opts;
  const out: HistoryReport[] = [];
  for (let i = 0; i < MKWRS_TRACKS.length; i++) {
    const track = MKWRS_TRACKS[i];
    try {
      const rep = await scrapeTrackHistory(db, track, opts);
      out.push(rep);
      log(`[wr-history] ${track}: ${JSON.stringify(rep)}`);
    } catch (e) {
      out.push({ course: track, inserted: 0, enriched: 0, unchanged: 0, removed: 0, flagged: 0 });
      log(`[wr-history] ${track}: FAILED ${(e as Error).message}`);
    }
    if (i < MKWRS_TRACKS.length - 1) await sleep(minDelayMs + random() * (maxDelayMs - minDelayMs));
  }
  return out;
}
