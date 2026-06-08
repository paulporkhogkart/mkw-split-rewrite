import { describe, it, expect } from 'vitest';
import { DateTime } from 'luxon';
import { resolvePeriod, toEpochSeconds } from './period';

const MEL = 'Australia/Melbourne';

describe('resolvePeriod', () => {
  it('today uses the tz midnight and is DST-correct (AEDT +11 in January)', () => {
    const now = DateTime.fromISO('2026-01-15T09:00:00', { zone: MEL }); // AEDT (+11)
    const p = resolvePeriod('today', MEL, { now });
    // Midnight Melbourne 2026-01-15 == 2026-01-14 13:00:00 UTC
    expect(p.startUtc).toBe('2026-01-14 13:00:00');
    expect(p.endUtc).toBe('2026-01-15 13:00:00');
  });

  it('today is DST-correct in winter (AEST +10 in July)', () => {
    const now = DateTime.fromISO('2026-07-15T09:00:00', { zone: MEL }); // AEST (+10)
    const p = resolvePeriod('today', MEL, { now });
    expect(p.startUtc).toBe('2026-07-14 14:00:00');
  });

  it('this_week starts Monday', () => {
    const now = DateTime.fromISO('2026-06-10T12:00:00', { zone: MEL }); // a Wednesday
    const p = resolvePeriod('this_week', MEL, { now });
    // Monday 2026-06-08 00:00 Melbourne (AEST +10) == 2026-06-07 14:00 UTC
    expect(p.startUtc).toBe('2026-06-07 14:00:00');
    expect(p.endUtc).toBe('2026-06-14 14:00:00');
  });

  it('all_time has open bounds', () => {
    const p = resolvePeriod('all_time', MEL);
    expect(p.startUtc).toBeNull();
    expect(p.endUtc).toBeNull();
  });

  it('rejects an invalid timezone', () => {
    expect(() => resolvePeriod('today', 'Mars/Olympus')).toThrow(/invalid tz/);
  });
});

describe('toEpochSeconds', () => {
  it('parses a UTC sql string to epoch seconds', () => {
    expect(toEpochSeconds('2026-06-07 14:00:00')).toBe(
      DateTime.fromISO('2026-06-07T14:00:00', { zone: 'utc' }).toSeconds());
  });
});
