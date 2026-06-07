import { describe, it, expect, vi } from 'vitest';
import { startWrScraper } from './scheduler';

const emptyReport = { inserted: 0, reflagged: 0, backfilled: 0, unchanged: 0, unmapped: [] };

describe('startWrScraper', () => {
  it('runs once immediately, then on the interval, and stops on demand', async () => {
    vi.useFakeTimers();
    try {
      const scrape = vi.fn(async () => emptyReport);
      const stop = startWrScraper({} as any, {} as any, { url: 'x', intervalSec: 1, scrape });
      expect(scrape).toHaveBeenCalledTimes(1);                 // immediate
      await vi.advanceTimersByTimeAsync(1000);
      expect(scrape).toHaveBeenCalledTimes(2);                 // one interval
      stop();
      await vi.advanceTimersByTimeAsync(3000);
      expect(scrape).toHaveBeenCalledTimes(2);                 // stopped
    } finally { vi.useRealTimers(); }
  });

  it('is disabled when intervalSec <= 0', () => {
    const scrape = vi.fn(async () => emptyReport);
    const stop = startWrScraper({} as any, {} as any, { url: 'x', intervalSec: 0, scrape });
    expect(scrape).not.toHaveBeenCalled();
    stop();
  });
});
