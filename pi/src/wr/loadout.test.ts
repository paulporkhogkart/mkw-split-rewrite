import { describe, it, expect } from 'vitest';
import { resolveLoadout } from './loadout';

describe('resolveLoadout', () => {
  it('splits a costume out of the character and resolves all three', () => {
    const lo = resolveLoadout('Toadette (Conductor)', 'Mach Rocket');
    expect(lo.character).toBe('Toadette');
    expect(lo.costume).toBe('Conductor');
    expect(lo.characterSlug).toBe('toadette');
    expect(lo.costumeSlug).toBe('conductor');
    expect(lo.kartSlug).toBe('mach_rocket');
    expect(lo.unresolved).toEqual([]);
  });

  it('treats a bare character as the base costume (costume slug null, NOT unresolved)', () => {
    const lo = resolveLoadout('Bowser', 'Reel Racer');
    expect(lo.costume).toBeNull();
    expect(lo.costumeSlug).toBeNull();
    expect(lo.unresolved).toEqual([]);
  });

  it('applies the kart alias map', () => {
    expect(resolveLoadout('Swoop', 'R.O.B. H.O.G.').kartSlug).toBe('rob_hog');
    expect(resolveLoadout('Swoop', 'Tiny Titan').kartSlug).toBe('rally_romper');
  });

  it('reports unresolvable names with a slug guess and a null slug', () => {
    const lo = resolveLoadout('Zzz Nobody', 'Fake Kart');
    expect(lo.characterSlug).toBeNull();
    expect(lo.kartSlug).toBeNull();
    expect(lo.unresolved).toEqual([
      { category: 'character', raw: 'Zzz Nobody', slugGuess: 'zzz_nobody' },
      { category: 'kart', raw: 'Fake Kart', slugGuess: 'fake_kart' },
    ]);
  });

  it('handles nulls and the mkwrs empty-cell dash', () => {
    const lo = resolveLoadout(null, null);
    expect(lo).toMatchObject({ character: null, costume: null, kart: null,
      characterSlug: null, costumeSlug: null, kartSlug: null, unresolved: [] });
    expect(resolveLoadout('-', '-').characterSlug).toBeNull();
    expect(resolveLoadout('-', '-').unresolved).toEqual([]);
  });
});
