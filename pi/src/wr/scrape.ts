import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import { parseWrTable } from './parse';
import { reconcile, type WrReport } from './reconcile';

export const DEFAULT_MKWRS_URL = 'https://mkwrs.com/mkworld/';

export type ScrapeOpts = {
  url?: string;
  cc?: number;
  fetchHtml?: (url: string) => Promise<string>;
};

export async function scrapeOnce(db: DatabaseSync, hub: EventHub, opts: ScrapeOpts = {}): Promise<WrReport> {
  const url = opts.url ?? DEFAULT_MKWRS_URL;
  const cc = opts.cc ?? 150;
  const fetchHtml = opts.fetchHtml ?? defaultFetchHtml;
  const html = await fetchHtml(url);
  return reconcile(db, hub, parseWrTable(html), cc);
}

async function defaultFetchHtml(url: string): Promise<string> {
  const res = await fetch(url, {
    headers: { 'User-Agent': 'mkw-pi-wr-scraper/1.0' },
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`mkwrs fetch failed: HTTP ${res.status}`);
  return res.text();
}
