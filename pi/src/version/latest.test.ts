import { describe, it, expect } from 'vitest';
import { parseSemver, compareSemver, pickLatestTag, resolveRelease, makeLatestFetcher } from './latest';

describe('semver helpers', () => {
  it('parses + compares, tolerating a leading v and missing parts', () => {
    expect(parseSemver('v2.10.3')).toEqual([2, 10, 3]);
    expect(parseSemver('nope')).toBeNull();
    expect(compareSemver('2.2.0', '2.10.0')).toBe(-1);   // numeric, not lexical
    expect(compareSemver('v2.1.0', '2.1.0')).toBe(0);
    expect(compareSemver('bad', '2.1.0')).toBe(0);       // unparseable -> 0
  });
  it('pickLatestTag returns the highest semver, v-stripped', () => {
    expect(pickLatestTag(['v2.1.0', 'v2.10.0', 'v2.2.0', 'garbage'])).toBe('2.10.0');
    expect(pickLatestTag([])).toBeNull();
  });
});

describe('resolveRelease', () => {
  it('parses owner/repo from an updater manifest env override', () => {
    const savedM = process.env.MKW_UPDATER_MANIFEST, savedR = process.env.MKW_RELEASE_REPO;
    process.env.MKW_UPDATER_MANIFEST = 'https://github.com/foo/bar/releases/latest/download/latest.json';
    delete process.env.MKW_RELEASE_REPO;
    const r = resolveRelease();
    expect(r.repo).toBe('foo/bar');
    expect(r.manifest).toContain('latest.json');
    if (savedM === undefined) delete process.env.MKW_UPDATER_MANIFEST; else process.env.MKW_UPDATER_MANIFEST = savedM;
    if (savedR === undefined) delete process.env.MKW_RELEASE_REPO; else process.env.MKW_RELEASE_REPO = savedR;
  });
});

describe('makeLatestFetcher', () => {
  it('picks the tag + app version and caches within the TTL', async () => {
    let calls = 0;
    const fetchImpl = (async (url: string) => {
      calls++;
      if (url.includes('/tags'))
        return { ok: true, json: async () => [{ name: 'v2.1.0' }, { name: 'v2.10.0' }, { name: 'v2.2.0' }] };
      return { ok: true, json: async () => ({ version: '2.1.0' }) };
    }) as unknown as typeof fetch;
    let t = 1000;
    const getLatest = makeLatestFetcher({ fetchImpl, now: () => t, repo: 'o/r', manifest: 'https://x/latest.json', ttlMs: 500 });
    expect(await getLatest()).toMatchObject({ tag: '2.10.0', app: '2.1.0', errors: [] });
    const after = calls;
    await getLatest();                       // within TTL -> served from cache
    expect(calls).toBe(after);
    t = 2000;                                // past TTL -> refetch
    await getLatest();
    expect(calls).toBeGreaterThan(after);
  });

  it('degrades to the last-good value and records an error when a source fails', async () => {
    const fetchImpl = (async (url: string) => {
      if (url.includes('/tags')) return { ok: false, status: 503 };
      return { ok: true, json: async () => ({ version: '2.1.0' }) };
    }) as unknown as typeof fetch;
    const getLatest = makeLatestFetcher({ fetchImpl, now: () => 1, repo: 'o/r', manifest: 'https://x/latest.json' });
    const r = await getLatest();
    expect(r.app).toBe('2.1.0');
    expect(r.tag).toBeNull();
    expect(r.errors.some((e) => e.startsWith('tags:'))).toBe(true);
  });

  it('retains the last-good tag across TTL expiry when the source later fails', async () => {
    let tagsFail = false;
    const fetchImpl = (async (url: string) => {
      if (url.includes('/tags')) {
        if (tagsFail) return { ok: false, status: 503 };
        return { ok: true, json: async () => [{ name: 'v2.10.0' }] };
      }
      return { ok: true, json: async () => ({ version: '2.1.0' }) };
    }) as unknown as typeof fetch;
    let t = 1000;
    const getLatest = makeLatestFetcher({ fetchImpl, now: () => t, repo: 'o/r', manifest: 'https://x/latest.json', ttlMs: 500 });
    const primed = await getLatest();        // prime cache with a good tag+app
    expect(primed.tag).toBe('2.10.0');
    tagsFail = true;                          // now only the tags fetch fails
    t = 2000;                                 // past TTL -> refetch
    const r = await getLatest();
    expect(r.tag).toBe('2.10.0');             // last-good retained
    expect(r.app).toBe('2.1.0');
    expect(r.errors.some((e) => e.startsWith('tags:'))).toBe(true);
  });
});
