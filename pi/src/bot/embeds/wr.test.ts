import { describe, it, expect } from 'vitest';
import { buildWrEmbed } from './wr';

describe('buildWrEmbed', () => {
  it('renders title/fields/colour and a reign footer (over)', () => {
    const e = buildWrEmbed({
      holder: 'Paul', track: 'Rainbow Road', record: '1:39.000',
      improvement_str: '-1.000s',
      reign: { previous_holder: 'Luke', reign_ms: 3 * 86400_000, is_same_person: false },
    }).toJSON();
    expect(e.title).toBe('WORLD RECORD BY PAUL');
    expect(e.color).toBe(0xf3f3f3);
    expect(e.fields).toEqual([
      { name: 'TRACK', value: '`Rainbow Road`', inline: true },
      { name: 'TIME', value: '`1:39.000`', inline: true },
      { name: 'DELTA', value: '`-1.000s`', inline: true },
    ]);
    expect(e.footer?.text).toBe('THE 3 DAY REIGN OF LUKE IS OVER');
  });
  it('shows First WR and no footer when there is no delta/reign', () => {
    const e = buildWrEmbed({ holder: 'Luke', track: 'RR', record: '1:40.000', improvement_str: null, reign: null }).toJSON();
    expect(e.fields?.[2]).toEqual({ name: 'DELTA', value: '`First WR`', inline: true });
    expect(e.footer).toBeUndefined();
  });
});
