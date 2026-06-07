import { describe, it, expect } from 'vitest';
import { buildPbEmbed, pbTitle } from './pb';
import type { PbEmbedData } from '../types';

const base: PbEmbedData = {
  player: 'Paul', track: 'Rainbow Road', time: '1:46.000', improvement_str: '-4.000s',
  is_new_track_record: false, reign: null,
  positions: { track: { old: 2, new: 1 }, total: { old: 3, new: 2 } },
  overtaken: [{ name: 'Luke', diff_str: '+2.000s' }],
  still_ahead: { name: 'WR', diff_str: '-6.000s' },
};

describe('pbTitle', () => {
  it('uses "<NAME> PERSONAL BEST" when not a track record', () => {
    expect(pbTitle(base)).toBe('PAUL PERSONAL BEST');
  });
  it('uses NEW TRACK RECORD when a record without reign', () => {
    expect(pbTitle({ ...base, is_new_track_record: true, reign: null })).toBe('NEW TRACK RECORD');
  });
  it('uses the reign title when dethroning', () => {
    expect(pbTitle({ ...base, is_new_track_record: true, reign: { previous_holder: 'Luke', reign_ms: 2 * 86400_000, is_same_person: false } }))
      .toBe('THE 2 DAY REIGN OF LUKE IS OVER');
  });
});

describe('buildPbEmbed', () => {
  it('renders fields, colour, thumbnail, and the still-ahead footer', () => {
    const e = buildPbEmbed(base, { thumbnail: 'http://gif', footerIcon: 'http://icon' }).toJSON();
    expect(e.title).toBe('PAUL PERSONAL BEST');
    expect(e.color).toBe(0x6cca5f);
    expect(e.thumbnail?.url).toBe('http://gif');
    expect(e.fields).toEqual([
      { name: 'TRACK', value: '`Rainbow Road`' },
      { name: 'TIME', value: '`1:46.000`' },
      { name: 'DELTA', value: '`-4.000s`' },
      { name: 'OVERTOOK', value: '`Luke (+2.000s)`', inline: true },
      { name: 'POSITION', value: '`Track: 2 → 1`\n`Total: 3 → 2`', inline: true },
    ]);
    expect(e.footer).toEqual({ text: 'The WR is still ahead! (-6.000s)', icon_url: 'http://icon' });
  });
});
