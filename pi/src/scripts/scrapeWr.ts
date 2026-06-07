import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import { scrapeOnce } from '../wr/scrape';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
scrapeOnce(db, new EventHub(), { url: process.env.MKWRS_URL })
  .then((rep) => { console.log('[wr] scrape complete:', JSON.stringify(rep)); process.exit(0); })
  .catch((e) => { console.error('[wr] scrape failed:', e); process.exit(1); });
