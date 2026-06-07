import { describe, it, expect } from 'vitest';
import { mkwrsTimeToMs, msToTimeStr } from './time';

describe('mkwrsTimeToMs', () => {
  it('parses M\'SS"mmm with 3-digit ms', () => {
    expect(mkwrsTimeToMs('1\'47"414')).toBe(107414);
  });
  it('normalizes 1- and 2-digit ms (hundredths/tenths)', () => {
    expect(mkwrsTimeToMs('1\'47"41')).toBe(107410);
    expect(mkwrsTimeToMs('1\'47"4')).toBe(107400);
  });
  it('handles surrounding whitespace', () => {
    expect(mkwrsTimeToMs('  2\'00"000 ')).toBe(120000);
  });
  it('throws on garbage', () => {
    expect(() => mkwrsTimeToMs('1:47.414')).toThrow();
    expect(() => mkwrsTimeToMs('nope')).toThrow();
  });
});

describe('msToTimeStr', () => {
  it('formats canonical M:SS.mmm', () => {
    expect(msToTimeStr(107414)).toBe('1:47.414');
    expect(msToTimeStr(120000)).toBe('2:00.000');
  });
  it('round-trips with mkwrsTimeToMs', () => {
    expect(msToTimeStr(mkwrsTimeToMs('1\'39"008'))).toBe('1:39.008');
  });
});
