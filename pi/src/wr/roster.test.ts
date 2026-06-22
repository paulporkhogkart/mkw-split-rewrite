import { describe, it, expect } from 'vitest';
import { resolveItem } from './roster';

describe('resolveItem', () => {
  it('resolves a plain character by slug', () => {
    expect(resolveItem('character', 'Baby Daisy').slug).toBe('baby_daisy');
    expect(resolveItem('character', 'Para-Biddybud').slug).toBe('para_biddybud');
  });
  it('resolves a costume by slug', () => {
    expect(resolveItem('costume', 'Conductor').slug).toBe('conductor');
  });
  it('resolves karts by slug and by alias', () => {
    expect(resolveItem('kart', 'Mach Rocket').slug).toBe('mach_rocket');
    expect(resolveItem('kart', 'R.O.B. H.O.G.').slug).toBe('rob_hog');     // slug r_o_b_h_o_g
    expect(resolveItem('kart', 'Biddybuggy').slug).toBe('buggybud');
    expect(resolveItem('kart', 'Tiny Titan').slug).toBe('rally_romper');
  });
  it('returns null + slugGuess for an unknown name', () => {
    const r = resolveItem('kart', 'Totally Fake Kart');
    expect(r.slug).toBeNull();
    expect(r.slugGuess).toBe('totally_fake_kart');
  });
});
