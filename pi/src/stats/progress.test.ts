import { describe, it, expect } from 'vitest';
import { buildReference, lapBoundaries } from './progress';

const LOOP = [
  { cx: 0, cy: 0, t_ms: 0 }, { cx: 10, cy: 0, t_ms: 50 }, { cx: 0, cy: 0, t_ms: 100 },
  { cx: 10, cy: 0, t_ms: 150 }, { cx: 0, cy: 0, t_ms: 200 },
];

describe('buildReference', () => {
  it('normalises arc length to s in [0,1]', () => {
    const ref = buildReference([{ cx: 0, cy: 0, t_ms: 0 }, { cx: 5, cy: 0, t_ms: 50 }, { cx: 10, cy: 0, t_ms: 100 }]);
    expect(ref.map((p) => p.s)).toEqual([0, 0.5, 1]);
  });
});

describe('lapBoundaries', () => {
  it('places the lap-1 boundary at the route fraction matching its end-time', () => {
    expect(lapBoundaries(buildReference(LOOP), [100, 200])).toEqual([0.5, 1]);
  });
});
