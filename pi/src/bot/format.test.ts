import { describe, it, expect } from 'vitest';
import { formatTimeDifference, formatDuration, formatOvertaken, formatPositions, msToDisplay, alignDiffColumn } from './format';

describe('formatTimeDifference', () => {
  it('formats zero, positive, negative (ms)', () => {
    expect(formatTimeDifference(0)).toBe('±0.000s');
    expect(formatTimeDifference(1234)).toBe('+1.234s');
    expect(formatTimeDifference(-842)).toBe('-0.842s');
    expect(formatTimeDifference(123)).toBe('+0.123s');
  });
});

describe('formatDuration', () => {
  it('buckets to the largest whole unit, singular labels (legacy style)', () => {
    expect(formatDuration(5_000)).toBe('5 SECOND');
    expect(formatDuration(120_000)).toBe('2 MINUTE');
    expect(formatDuration(3 * 3600_000)).toBe('3 HOUR');
    expect(formatDuration(2 * 86400_000)).toBe('2 DAY');
    expect(formatDuration(40 * 86400_000)).toBe('1 MONTH');
    expect(formatDuration(400 * 86400_000)).toBe('1 YEAR');
  });
});

describe('formatOvertaken', () => {
  it('returns No-one when empty', () => {
    expect(formatOvertaken([])).toBe('`No-one`');
  });
  it('aligns names + decimals, WR kept as a name', () => {
    const out = formatOvertaken([
      { name: 'WR', diff_str: '+1.200s' },
      { name: 'Luke', diff_str: '+0.034s' },
    ]);
    expect(out).toBe('`WR    (+1.200s)`\n`Luke  (+0.034s)`');
  });
});

describe('formatPositions', () => {
  it('renders track + total transitions', () => {
    expect(formatPositions({ track: { old: 3, new: 1 }, total: { old: 4, new: 2 } }))
      .toBe('`Track: 3 → 1`\n`Total: 4 → 2`');
  });
  it('uses New when there is no old position', () => {
    expect(formatPositions({ track: { old: null, new: 1 }, total: { old: null, new: 5 } }))
      .toBe('`Track: New → 1`\n`Total: New → 5`');
  });
  it('omits an unchanged total and falls back to New record', () => {
    expect(formatPositions({ track: { old: null, new: null }, total: { old: 2, new: 2 } }))
      .toBe('`New record`');
  });
});

describe('msToDisplay', () => {
  it('formats sub-minute and minute+ times (ports TimeUtils.milliseconds_to_display)', () => {
    expect(msToDisplay(23456)).toBe('23.456');
    expect(msToDisplay(83456)).toBe('1:23.456');
    expect(msToDisplay(120000)).toBe('2:00.000');
    expect(msToDisplay(59999)).toBe('59.999');
  });
});

describe('alignDiffColumn', () => {
  it('right-justifies the integer part to a common width; empty stays empty', () => {
    expect(alignDiffColumn(['+1.200s', '+12.030s', '', null])).toEqual([' +1.200s', '+12.030s', '', '']);
  });
});
