import { describe, it, expect } from 'vitest';
import { assertTrailsIdentical, decodeTrail, encodeTrail, packTrail, unpackTrail, type TrailPoint } from './trailCodec';

// Hand-computed golden vector — pins varint flavour (unsigned LEB128) and byte-plane
// order (LSB→MSB) so a reimplementation can't silently change the format.
const GOLDEN_PTS: TrailPoint[] = [
  { t_ms: 0, cx: 1.5, cy: -2.5, score: 0.5, lap: null },
  { t_ms: 40, cx: 1.5, cy: -2.5, score: 0.5, lap: 1 },
];
// head: n=2 | flags=0 | t0=0 | Δ=40 | lapRLE (null,1)(1,1) = ff 01 01 01
// planes: 1.5→…f8 3f, -2.5→…04 c0, 0.5→…e0 3f (each: 12 zero bytes then 2+2 plane bytes)
const GOLDEN_HEX =
  '02000028ff010101' +
  '000000000000000000000000f8f83f3f' +
  '0000000000000000000000000404c0c0' +
  '000000000000000000000000e0e03f3f';

// deterministic PRNG so failures reproduce (Math.random forbidden in fixtures)
function rnd(seed: number) { let s = seed >>> 0; return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32; }
function randomTrail(n: number, seed: number): TrailPoint[] {
  const r = rnd(seed); const pts: TrailPoint[] = []; let t = Math.floor(r() * 1000);
  for (let i = 0; i < n; i++) {
    t += 1 + Math.floor(r() * 9000);
    pts.push({ t_ms: t, cx: (r() - 0.5) * 4000, cy: (r() - 0.5) * 4000,
               score: r(), lap: r() < 0.1 ? null : 1 + Math.floor(r() * 7) });
  }
  return pts;
}

describe('trailCodec', () => {
  it('packTrail matches the golden vector', () => {
    expect(packTrail(GOLDEN_PTS).toString('hex')).toBe(GOLDEN_HEX);
  });

  it('unpackTrail inverts the golden vector', () => {
    assertTrailsIdentical(unpackTrail(Buffer.from(GOLDEN_HEX, 'hex')), GOLDEN_PTS);
  });

  it('encode→decode round-trips random trails bit-exactly (sizes cross varint boundaries)', () => {
    for (const [n, seed] of [[1, 1], [2, 2], [257, 3], [5000, 4]] as const)
      assertTrailsIdentical(decodeTrail(encodeTrail(randomTrail(n, seed))), randomTrail(n, seed));
  });

  it('round-trips full-entropy EMA-like doubles bit-exactly', () => {
    let cx = 960.0, cy = 540.0; const pts: TrailPoint[] = [];
    for (let i = 0; i < 2000; i++) {
      cx += 0.35 * ((((i * 7919) % 100) - 50) - cx * 0.001);
      cy += 0.35 * ((((i * 104729) % 100) - 50) - cy * 0.001);
      pts.push({ t_ms: i * 40, cx, cy, score: 0.4 + 0.6 / (1 + (i % 13)), lap: 1 + Math.floor(i / 500) });
    }
    assertTrailsIdentical(decodeTrail(encodeTrail(pts)), pts);
  });

  it('rejects malformed trails instead of packing them', () => {
    const p = (over: Partial<TrailPoint>): TrailPoint[] => [
      { t_ms: 0, cx: 1, cy: 2, score: 0.5, lap: 1 },
      { t_ms: 40, cx: 1, cy: 2, score: 0.5, lap: 1, ...over } as TrailPoint,
    ];
    expect(() => packTrail([])).toThrow('empty');
    expect(() => packTrail(p({ t_ms: 0 }))).toThrow('strictly increasing');   // duplicate t
    expect(() => packTrail(p({ t_ms: -5 }))).toThrow();                       // negative
    expect(() => packTrail(p({ t_ms: 40.5 }))).toThrow();                     // non-integer
    expect(() => packTrail(p({ lap: 255 }))).toThrow('bad lap');
    expect(() => packTrail(p({ cx: Infinity }))).toThrow('finite');
  });
});
