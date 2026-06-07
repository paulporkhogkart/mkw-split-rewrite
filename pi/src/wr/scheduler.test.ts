import { describe, it, expect, vi } from 'vitest';
import { startWrScraper } from './scheduler';

const emptyReport = { inserted: 0, reflagged: 0, backfilled: 0, unchanged: 0, unmapped: [] };

describe('startWrScraper', () => {
  it('runs once immediately, then re-polls after a randomized delay, and stops on demand', async () => {
    vi.useFakeTimers();
    try {
      const scrape = vi.fn(async () => emptyReport);
      // random() === 0 -> delay = min (900s) every cycle.
      const stop = startWrScraper({} as any, {} as any, {
        minIntervalSec: 900, maxIntervalSec: 1800, scrape, random: () => 0,
      });
      expect(scrape).toHaveBeenCalledTimes(1);            // immediate first run
      await vi.advanceTimersByTimeAsync(900_000);         // one min-length cycle
      expect(scrape).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(900_000);         // delay is re-rolled each cycle
      expect(scrape).toHaveBeenCalledTimes(3);
      stop();
      await vi.advanceTimersByTimeAsync(1_800_000);
      expect(scrape).toHaveBeenCalledTimes(3);            // stopped: no further polls
    } finally { vi.useRealTimers(); }
  });

  it('maps random() across the full [min,max] range (delay = min + r*(max-min))', async () => {
    vi.useFakeTimers();
    try {
      const scrape = vi.fn(async () => emptyReport);
      // r=0.5 -> midpoint = 900 + 0.5*(1800-900) = 1350s.
      const stop = startWrScraper({} as any, {} as any, {
        minIntervalSec: 900, maxIntervalSec: 1800, scrape, random: () => 0.5,
      });
      expect(scrape).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1_349_999);       // 1ms short of the midpoint
      expect(scrape).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);               // hits 1350s exactly
      expect(scrape).toHaveBeenCalledTimes(2);
      stop();
    } finally { vi.useRealTimers(); }
  });

  it('is disabled when maxIntervalSec <= 0', () => {
    const scrape = vi.fn(async () => emptyReport);
    const stop = startWrScraper({} as any, {} as any, {
      minIntervalSec: 900, maxIntervalSec: 0, scrape,
    });
    expect(scrape).not.toHaveBeenCalled();
    stop();
  });
});
