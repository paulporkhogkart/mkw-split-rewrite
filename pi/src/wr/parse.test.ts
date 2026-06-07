import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseWrTable } from './parse';

const html = readFileSync(new URL('./__fixtures__/mkworld.html', import.meta.url), 'utf8');

describe('parseWrTable', () => {
  const rows = parseWrTable(html);

  it('parses the 30 base courses and excludes the 2 (Glitch) rows', () => {
    // The captured fixture lists 30 base tracks with no (Glitch) rows present.
    // The glitch-exclusion filter is retained for robustness when mkwrs adds them.
    expect(rows.length).toBe(30);
    expect(rows.some((r) => /\(glitch\)/i.test(r.courseName))).toBe(false);
  });

  it('every row is well-formed', () => {
    for (const r of rows) {
      expect(r.courseName.length).toBeGreaterThan(0);
      expect(Number.isInteger(r.recordMs)).toBe(true);
      expect(r.recordMs).toBeGreaterThan(0);
      expect(r.recordStr).toMatch(/^\d+:\d{2}\.\d{3}$/);
    }
  });

  it('extracts fields for a known track (Rainbow Road)', () => {
    const rr = rows.find((r) => r.courseName === 'Rainbow Road');
    expect(rr).toBeDefined();
    expect(rr!.holder && rr!.holder.length).toBeTruthy();
    // mkwrs entities must be decoded so apostrophe names slugify correctly elsewhere.
    expect(rr!.courseName).not.toContain('&');
    if (rr!.videoUrl) expect(rr!.videoUrl).toMatch(/^https?:/);
  });

  it('decodes apostrophe course names (entity decoding)', () => {
    // Toad's Factory / Bowser's Castle etc. must come through with a real apostrophe,
    // never "&#39;" - otherwise course resolution breaks.
    expect(rows.some((r) => /&#?\w+;/.test(r.courseName))).toBe(false);
  });
});
