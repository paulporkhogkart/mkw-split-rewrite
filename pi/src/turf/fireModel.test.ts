import { describe, it, expect } from 'vitest';
import { isOnFire, fireBarPct, E0, K } from './fireModel';

describe('fireModel', () => {
  it('keeps the web constants', () => { expect(E0).toBe(0.2); expect(K).toBe(4); });
  it('bar rises exponentially with off-WR %', () => {
    expect(fireBarPct(0)).toBeCloseTo(0.2, 6);
    expect(fireBarPct(4)).toBeCloseTo(0.2 * Math.E, 6);
  });
  it('on fire when the lead over #2 clears the bar', () => {
    // wr=100000; leader=100100 (0.1% off), #2=100400 -> lead 0.3% >= bar ~0.2005%
    expect(isOnFire(100100, 100400, 100000)).toBe(true);
  });
  it('not on fire without wr / #2 / when #2 faster', () => {
    expect(isOnFire(100100, 100400, null)).toBe(false);
    expect(isOnFire(100100, null, 100000)).toBe(false);
    expect(isOnFire(100100, 100050, 100000)).toBe(false);
  });
});
