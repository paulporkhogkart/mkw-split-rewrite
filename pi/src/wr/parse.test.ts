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

describe('parseWrTable on the v1.8.0 index page (two table.wr blocks)', () => {
  const html180 = readFileSync(new URL('./__fixtures__/mkworld_1_8_0.html', import.meta.url), 'utf8');

  it('reads rows from EVERY table.wr, not just the first (30 base + 10 SNES)', () => {
    const rows = parseWrTable(html180);
    expect(rows.length).toBe(40);
    const snes = rows.filter((r) => r.courseName.startsWith('SNES '));
    expect(snes.map((r) => r.courseName)).toEqual([
      'SNES Mario Circuit 1', 'SNES Mario Circuit 2', 'SNES Mario Circuit 3',
      'SNES Ghost Valley 1', 'SNES Ghost Valley 2', 'SNES Ghost Valley 3',
      'SNES Choco Island 1', 'SNES Choco Island 2', 'SNES Koopa Beach 1', 'SNES Vanilla Lake 1',
    ]);
  });

  it('extracts fields for a SNES track', () => {
    const r = parseWrTable(html180).find((x) => x.courseName === 'SNES Mario Circuit 1')!;
    expect(r.recordMs).toBe(75891);
    expect(r.holder).toBe('JaK');
    expect(r.videoUrl).toBe('https://www.youtube.com/watch?v=53XGlcyHFf4');
    expect(r.character).toBe('Toadette (Explorer)');
  });
});
