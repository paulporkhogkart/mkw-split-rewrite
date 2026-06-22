import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseHistory, splitCharacter } from './history_parse';

const fx = (name: string) =>
  readFileSync(new URL(`./__fixtures__/history/${name}.html`, import.meta.url), 'utf8');

describe('splitCharacter', () => {
  it('splits Char (Costume)', () => {
    expect(splitCharacter('Toadette (Conductor)')).toEqual({ character: 'Toadette', costume: 'Conductor' });
  });
  it('treats a bare name as base costume', () => {
    expect(splitCharacter('Baby Daisy')).toEqual({ character: 'Baby Daisy', costume: null });
  });
});

describe('parseHistory — flat 3-lap (Mario Bros. Circuit)', () => {
  const rows = parseHistory(fx('mario_bros_circuit'));
  it('parses the full progression, oldest first', () => {
    expect(rows.length).toBeGreaterThan(80);
    expect(rows[0].datePrecision).toBe('pre_release');         // first row is Pre-release
    expect(rows[rows.length - 1].recordMs).toBe(107414);       // current WR 1'47"414
  });
  it('extracts a known row (current WR by Toadette/Conductor)', () => {
    const cur = rows[rows.length - 1];
    expect(cur.holderName).toBe('あつき');
    expect(cur.nation).toBe('JP');
    expect(cur.lapSplitsMs).toEqual([37000, 35263, 35151]);
    expect(cur.coins).toEqual([8, 0, 0]);
    expect(cur.mushrooms).toEqual([1, 1, 1]);
    expect(cur.characterRaw).toBe('Toadette (Conductor)');
    expect(cur.kartRaw).toBe('Mach Rocket');
    expect(cur.videoUrl).toMatch(/^https?:/);
  });
  it('tolerates a no-video plain-text time and a missing-data (-) row', () => {
    expect(rows.some((r) => r.videoUrl === null)).toBe(true);
    expect(rows.some((r) => r.coins === null)).toBe(true);
  });
});

describe('parseHistory — stacked variants', () => {
  it('Rainbow Road: 4 laps, M:SS.mmm lap, multi-digit coins, stacked char/kart', () => {
    const rows = parseHistory(fx('rainbow_road'));
    const cur = rows[rows.length - 1];
    expect(cur.lapSplitsMs.length).toBe(4);
    expect(cur.lapSplitsMs[3]).toBe(73164);                    // 1:13.164
    expect(cur.coins).toEqual([8, 12, 0, 0]);
    expect(cur.mushrooms).toEqual([0, 1, 1, 1]);
    expect(cur.characterRaw).toBe('Wiggler');
    expect(cur.kartRaw).toBe('Big Horn');
  });
  it('DK Spaceport: 6 laps', () => {
    const rows = parseHistory(fx('dk_spaceport'));
    const cur = rows[rows.length - 1];
    expect(cur.lapSplitsMs.length).toBe(6);
    expect(cur.coins!.length).toBe(6);
    expect(cur.mushrooms!.length).toBe(6);
  });
  it('Koopa Troopa Beach: 5 laps', () => {
    const rows = parseHistory(fx('koopa_troopa_beach'));
    expect(rows[rows.length - 1].lapSplitsMs.length).toBe(5);
  });
});

describe('parseHistory — patch-row skip (Mario Circuit)', () => {
  it('never emits a patch/info row as a record', () => {
    const rows = parseHistory(fx('mario_circuit'));
    expect(rows.every((r) => !/Patch Released/i.test(r.kartRaw ?? ''))).toBe(true);
    expect(rows.every((r) => Number.isInteger(r.recordMs) && r.recordMs > 0)).toBe(true);
  });
});
