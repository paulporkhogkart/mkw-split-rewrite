import { DateTime } from 'luxon';
import type { Period, PeriodKey } from './types';

const SQL = 'yyyy-MM-dd HH:mm:ss';

export interface PeriodOpts { from?: string; to?: string; now?: DateTime; }

/** Resolve a period key + IANA tz into UTC [start,end) bounds (+ ISO for display).
 *  Weeks start Monday (Luxon ISO weeks). all_time -> open (null) bounds. */
export function resolvePeriod(key: PeriodKey, tz: string, opts: PeriodOpts = {}): Period {
  if (key === 'all_time') return { key, tz, startUtc: null, endUtc: null, startIso: null, endIso: null };

  const now = (opts.now ?? DateTime.now()).setZone(tz);
  if (!now.isValid) throw new Error(`invalid tz: ${tz}`);

  let start: DateTime, end: DateTime;
  if (key === 'today') { start = now.startOf('day'); end = start.plus({ days: 1 }); }
  else if (key === 'this_week') { start = now.startOf('week'); end = start.plus({ weeks: 1 }); }
  else if (key === 'this_month') { start = now.startOf('month'); end = start.plus({ months: 1 }); }
  else if (key === 'range') {
    if (!opts.from || !opts.to) throw new Error('range requires from and to');
    start = DateTime.fromISO(opts.from, { zone: tz });
    end = DateTime.fromISO(opts.to, { zone: tz });
    if (!start.isValid || !end.isValid) throw new Error('invalid range bounds');
  } else throw new Error(`unknown period: ${key}`);

  return {
    key, tz,
    startUtc: start.toUTC().toFormat(SQL),
    endUtc: end.toUTC().toFormat(SQL),
    startIso: start.toISO(),
    endIso: end.toISO(),
  };
}

/** A UTC sql string ('YYYY-MM-DD HH:MM:SS') -> epoch seconds (for porker's integer Timestamp). */
export function toEpochSeconds(utcSql: string): number {
  return DateTime.fromFormat(utcSql, SQL, { zone: 'utc' }).toSeconds();
}
