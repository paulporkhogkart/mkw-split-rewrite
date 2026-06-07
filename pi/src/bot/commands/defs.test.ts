import { describe, it, expect } from 'vitest';
import { commandDefs, filterChoices } from './defs';

describe('commandDefs', () => {
  it('has 3 entries with names leaderboard/wr/nemesis', () => {
    expect(commandDefs).toHaveLength(3);
    expect(commandDefs[0].name).toBe('leaderboard');
    expect(commandDefs[1].name).toBe('wr');
    expect(commandDefs[2].name).toBe('nemesis');
  });

  it("wr's track option is required", () => {
    const wr = commandDefs[1];
    expect(wr.options).toBeDefined();
    expect(wr.options![0].required).toBe(true);
  });

  it("leaderboard's track option is not required", () => {
    const lb = commandDefs[0];
    expect(lb.options).toBeDefined();
    expect(lb.options![0].required).toBe(false);
  });

  it("nemesis's player option is not required", () => {
    const nem = commandDefs[2];
    expect(nem.options).toBeDefined();
    expect(nem.options![0].required).toBe(false);
  });
});

describe('filterChoices', () => {
  it('filters case-insensitively by substring', () => {
    expect(filterChoices(['Rainbow Road', 'DK Pass'], 'ra')).toEqual([
      { name: 'Rainbow Road', value: 'Rainbow Road' },
    ]);
  });

  it('returns empty array when nothing matches', () => {
    expect(filterChoices(['Rainbow Road', 'DK Pass'], 'zzz')).toEqual([]);
  });

  it('returns all when query is empty string', () => {
    const result = filterChoices(['Rainbow Road', 'DK Pass'], '');
    expect(result).toHaveLength(2);
  });

  it('caps at 25 entries (Discord limit)', () => {
    const values = Array.from({ length: 30 }, (_, i) => `Track ${i}`);
    const result = filterChoices(values, '');
    expect(result).toHaveLength(25);
  });

  it('maps each match to { name, value } with the original casing', () => {
    const result = filterChoices(['Rainbow Road', 'DK Pass'], 'dk');
    expect(result).toEqual([{ name: 'DK Pass', value: 'DK Pass' }]);
  });
});
