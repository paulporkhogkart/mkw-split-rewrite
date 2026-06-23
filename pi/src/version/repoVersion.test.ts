import { describe, it, expect } from 'vitest';
import { repoVersion } from './repoVersion';

describe('repoVersion', () => {
  it('reads a semver string from the root package.json', () => {
    expect(repoVersion()).toMatch(/^\d+\.\d+\.\d+/);
  });
});
