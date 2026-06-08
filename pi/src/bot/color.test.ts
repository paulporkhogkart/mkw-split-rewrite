import { describe, it, expect } from 'vitest';
import { discordColor } from './color';

const channels = (n: number) => [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];

describe('discordColor', () => {
  it('returns null for missing/invalid input', () => {
    expect(discordColor(null)).toBeNull();
    expect(discordColor(undefined)).toBeNull();
    expect(discordColor('')).toBeNull();
    expect(discordColor('nope')).toBeNull();
    expect(discordColor('#12345')).toBeNull();     // not 6 hex digits
  });

  it('parses a #rrggbb colour to a number in range', () => {
    const c = discordColor('#a78bfa')!;
    expect(typeof c).toBe('number');
    expect(c).toBeGreaterThanOrEqual(0);
    expect(c).toBeLessThanOrEqual(0xffffff);
  });

  it('lightens a near-black colour so it is visible on dark mode', () => {
    const [r, g, b] = channels(discordColor('#0a0a0a')!);
    expect(Math.max(r, g, b)).toBeGreaterThan(0x66);   // brightened well above near-black
  });

  it('keeps a true grey grey (only clamps brightness)', () => {
    const [r, g, b] = channels(discordColor('#000000')!);
    expect(r).toBe(g);
    expect(g).toBe(b);
  });

  it('accepts hex without a leading #', () => {
    expect(discordColor('a78bfa')).toBe(discordColor('#a78bfa'));
  });
});
