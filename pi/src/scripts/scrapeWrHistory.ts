import { openDb, applySchema } from '../db/connect';
import { scrapeAllHistory, scrapeTrackHistory } from '../wr/history_scrape';
import { resolveFlags, reportFlags } from '../wr/flags';

// Usage: scrape-wr-history --all | --track="Rainbow Road"
const args = process.argv.slice(2);
const trackArg = args.find((a) => a.startsWith('--track='))?.slice('--track='.length);
const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);

async function main() {
  if (trackArg) {
    const rep = await scrapeTrackHistory(db, trackArg);
    console.log('[wr-history]', JSON.stringify(rep));
  } else {
    const reps = await scrapeAllHistory(db, { log: (m) => console.log(m) });
    const totals = reps.reduce((a, r) => ({
      inserted: a.inserted + r.inserted, enriched: a.enriched + r.enriched,
      removed: a.removed + r.removed, flagged: a.flagged + r.flagged,
    }), { inserted: 0, enriched: 0, removed: 0, flagged: 0 });
    console.log('[wr-history] totals:', JSON.stringify(totals));
  }
  resolveFlags(db);
  console.log(reportFlags(db));
}

main().catch((e) => { console.error('[wr-history] failed:', e); process.exitCode = 1; });
