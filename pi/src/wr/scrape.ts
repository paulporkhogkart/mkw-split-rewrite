import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import type { ActivityHub } from '../activity/hub';
import { parseWrTable } from './parse';
import { reconcile, type WrReport } from './reconcile';

export const DEFAULT_MKWRS_URL = 'https://mkwrs.com/mkworld/';

export type ScrapeOpts = {
  url?: string;
  cc?: number;
  fetchHtml?: (url: string) => Promise<string>;
  activity?: ActivityHub;
};

export async function scrapeOnce(db: DatabaseSync, hub: EventHub, opts: ScrapeOpts = {}): Promise<WrReport> {
  const url = opts.url ?? DEFAULT_MKWRS_URL;
  const cc = opts.cc ?? 150;
  const fetchHtml = opts.fetchHtml ?? defaultFetchHtml;
  const html = await fetchHtml(url);
  return reconcile(db, hub, parseWrTable(html), cc, opts.activity);
}

async function defaultFetchHtml(url: string): Promise<string> {
  // Manual AbortController + clearTimeout rather than AbortSignal.timeout: the latter
  // leaves a 30s timer pending after the fetch resolves, keeping the event loop alive
  // (and, when force-exited, racing libuv teardown on Windows). Clearing it lets the
  // CLI exit cleanly on its own.
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), 30_000);
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'mkw-pi-wr-scraper/1.0' },
      signal: ac.signal,
    });
    if (!res.ok) throw new Error(`mkwrs fetch failed: HTTP ${res.status}`);
    return await res.text();
  } finally {
    clearTimeout(timer);
  }
}
