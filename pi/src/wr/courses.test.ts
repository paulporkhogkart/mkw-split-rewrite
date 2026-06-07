import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mkwrsNameToSlug, resolveCourseId } from './courses';
import { MKWRS_NAMES, seedCanonicalCourses } from './__fixtures__/courses';

function db() {
  const d = openDb(':memory:');
  applySchema(d);
  seedCanonicalCourses(d);
  return d;
}

describe('mkwrsNameToSlug', () => {
  it('aliases Wario Shipyard to warios_galleon', () => {
    expect(mkwrsNameToSlug('Wario Shipyard')).toBe('warios_galleon');
  });
  it('slugifies the rest (apostrophes dropped)', () => {
    expect(mkwrsNameToSlug("Toad's Factory")).toBe('toads_factory');
    expect(mkwrsNameToSlug('Great ? Block Ruins')).toBe('great_block_ruins');
  });
});

describe('resolveCourseId', () => {
  it('resolves every one of the 30 mkwrs course names (completeness)', () => {
    const d = db();
    const unresolved = MKWRS_NAMES.filter((n) => resolveCourseId(d, n) === null);
    expect(unresolved).toEqual([]);
  });
  it('returns null for glitch categories and unknown names', () => {
    const d = db();
    expect(resolveCourseId(d, 'Mario Bros. Circuit (Glitch)')).toBeNull();
    expect(resolveCourseId(d, 'Crown City (Glitch)')).toBeNull();
    expect(resolveCourseId(d, 'Totally Fake Track')).toBeNull();
  });
});
