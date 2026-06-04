import { describe, it, expect } from 'vitest';
import { slugify } from './slug';

describe('slugify', () => {
  it('strips apostrophes and collapses punctuation (matches A)', () => {
    expect(slugify("Bowser's Castle")).toBe('bowsers_castle');
    expect(slugify("Toad's Factory")).toBe('toads_factory');
    expect(slugify("Wario's Galleon")).toBe('warios_galleon');
    expect(slugify('Mario Bros. Circuit')).toBe('mario_bros_circuit');
    expect(slugify('Great ? Block Ruins')).toBe('great_block_ruins');
    expect(slugify('Sky-High Sundae')).toBe('sky_high_sundae');
    expect(slugify('DK Pass')).toBe('dk_pass');
  });
});
