import { describe, it, expect } from 'vitest';
import { lapTimeToMs, parsePerLap } from './lap';

describe('lapTimeToMs', () => {
  it('parses SS.mmm', () => { expect(lapTimeToMs('37.000')).toBe(37000); });
  it('parses sub-minute with real ms', () => { expect(lapTimeToMs('35.263')).toBe(35263); });
  it('parses M:SS.mmm', () => { expect(lapTimeToMs('1:13.164')).toBe(73164); });
  it('returns null for dash and empty', () => {
    expect(lapTimeToMs('-')).toBeNull();
    expect(lapTimeToMs('')).toBeNull();
  });
});

describe('parsePerLap', () => {
  it('splits single-digit per-lap', () => { expect(parsePerLap('8-0-0')).toEqual([8, 0, 0]); });
  it('handles multi-digit', () => { expect(parsePerLap('8-12-0-0')).toEqual([8, 12, 0, 0]); });
  it('returns null for dash and empty', () => {
    expect(parsePerLap('-')).toBeNull();
    expect(parsePerLap('')).toBeNull();
  });
});
