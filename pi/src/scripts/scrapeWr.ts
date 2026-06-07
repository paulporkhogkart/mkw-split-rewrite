import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import { scrapeOnce } from '../wr/scrape';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
// No process.exit(): once the fetch timer is cleared the event loop drains on its own,
// so the process exits cleanly with code 0. Forcing exit here raced libuv teardown on
// Windows (UV_HANDLE_CLOSING assertion). On failure we set a non-zero exit code instead.
scrapeOnce(db, new EventHub(), { url: process.env.MKWRS_URL })
  .then((rep) => { console.log('[wr] scrape complete:', JSON.stringify(rep)); })
  .catch((e) => { console.error('[wr] scrape failed:', e); process.exitCode = 1; });
