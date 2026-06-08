import { describe, it, expect } from 'vitest';
import { getMetric, listMetrics, allowsDimension } from './metrics';

describe('metric registry', () => {
  it('exposes coins as an all-status lap-grain metric', () => {
    const m = getMetric('coins');
    expect(m?.kind).toBe('race');
    if (m?.kind === 'race') {
      expect(m.statuses).toBe('all');
      expect(m.joins).toContain('laps');
    }
  });

  it('pb_count is finished-only and pb-restricted', () => {
    const m = getMetric('pb_count');
    expect(m?.kind === 'race' && m.pbOnly).toBe(true);
  });

  it('body_fat is a body metric not groupable by course', () => {
    expect(allowsDimension('body_fat', 'player')).toBe(true);
    expect(allowsDimension('body_fat', 'course')).toBe(false);
  });

  it('unknown metric returns undefined', () => {
    expect(getMetric('nope')).toBeUndefined();
  });

  it('lists both domains', () => {
    const ids = listMetrics().map((m) => m.id);
    expect(ids).toContain('resets');
    expect(ids).toContain('muscle_mass');
  });

  it('sequential metrics allow player/course/cc only', () => {
    expect(allowsDimension('resets_since_pb', 'course')).toBe(true);
    expect(allowsDimension('resets_since_pb', 'character')).toBe(false);
  });
});
