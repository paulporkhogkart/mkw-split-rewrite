import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import { scrapeOnce, type ScrapeOpts } from './scrape';
import type { WrReport } from './reconcile';

type ScrapeFn = (db: DatabaseSync, hub: EventHub, opts: ScrapeOpts) => Promise<WrReport>;

export type SchedulerOpts = {
  url?: string;
  intervalSec: number;
  scrape?: ScrapeFn;            // injectable for tests; defaults to scrapeOnce
};

/** Start the in-process WR scraper: one run immediately, then every intervalSec.
 *  Each tick is isolated (a failure logs, never throws); overlapping ticks are skipped.
 *  intervalSec <= 0 disables it. Returns a stop function. */
export function startWrScraper(db: DatabaseSync, hub: EventHub, opts: SchedulerOpts): () => void {
  const { url, intervalSec } = opts;
  const scrape = opts.scrape ?? scrapeOnce;
  if (!intervalSec || intervalSec <= 0) return () => {};

  let running = false;
  const tick = async () => {
    if (running) return;
    running = true;
    try {
      const rep = await scrape(db, hub, { url });
      console.log(`[wr] scrape: ${JSON.stringify(rep)}`);
    } catch (e) {
      console.error('[wr] scrape failed:', e);
    } finally {
      running = false;
    }
  };

  void tick();                                          // immediate, non-blocking
  const id = setInterval(() => void tick(), intervalSec * 1000);
  return () => clearInterval(id);
}
