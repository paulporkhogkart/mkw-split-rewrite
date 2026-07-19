import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import type { ActivityHub } from '../activity/hub';
import { scrapeOnce, type ScrapeOpts } from './scrape';
import type { WrReport } from './reconcile';
import { sweepStuckJobAlerts } from '../db/wrJobs';

type ScrapeFn = (db: DatabaseSync, hub: EventHub, opts: ScrapeOpts) => Promise<WrReport>;

export type SchedulerOpts = {
  url?: string;
  minIntervalSec: number;
  maxIntervalSec: number;
  activity?: ActivityHub;       // optional: emit turf/wr activity on WR changes
  scrape?: ScrapeFn;            // injectable for tests; defaults to scrapeOnce
  sweep?: (db: DatabaseSync, hub: EventHub) => number;  // injectable for tests
  random?: () => number;        // injectable for tests; defaults to Math.random
};

/** Start the in-process WR scraper: one run immediately, then re-poll after a delay
 *  drawn uniformly at random from [minIntervalSec, maxIntervalSec] and re-rolled every
 *  cycle. The jitter avoids a fixed, bot-like request cadence to mkwrs (it looks like a
 *  person checking the board now and then). Each tick is isolated (a failure logs, never
 *  throws) and the next is scheduled only after the current finishes, so ticks never
 *  overlap. maxIntervalSec <= 0 disables it. Returns a stop function. */
export function startWrScraper(db: DatabaseSync, hub: EventHub, opts: SchedulerOpts): () => void {
  const { url, minIntervalSec, maxIntervalSec, activity } = opts;
  const scrape = opts.scrape ?? scrapeOnce;
  const sweep = opts.sweep ?? sweepStuckJobAlerts;
  const random = opts.random ?? Math.random;
  if (!maxIntervalSec || maxIntervalSec <= 0) return () => {};

  const lo = Math.max(0, Math.min(minIntervalSec, maxIntervalSec));
  const hi = Math.max(minIntervalSec, maxIntervalSec);
  const nextDelayMs = () => (lo + random() * (hi - lo)) * 1000;

  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const schedule = () => {
    if (stopped) return;
    timer = setTimeout(() => void tick(), nextDelayMs());
  };

  const tick = async () => {
    try {
      const rep = await scrape(db, hub, { url, activity });
      console.log(`[wr] scrape: ${JSON.stringify(rep)}`);
      const alerted = sweep(db, hub);
      if (alerted) console.log(`[wr] stuck-job sweep alerted ${alerted} job(s)`);
    } catch (e) {
      console.error('[wr] scrape failed:', e);
    } finally {
      schedule();                                         // re-roll the delay each cycle
    }
  };

  void tick();                                            // immediate first run
  return () => { stopped = true; if (timer) clearTimeout(timer); };
}
