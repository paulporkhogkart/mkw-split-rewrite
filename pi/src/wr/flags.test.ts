import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { upsertFlag, resolveFlags, reportFlags } from './flags';

function freshDb() { const db = openDb(':memory:'); applySchema(db); return db; }

describe('flags', () => {
  it('inserts then increments occurrences on the same (category, raw_value)', () => {
    const db = freshDb();
    upsertFlag(db, { category: 'kart', rawValue: 'Mystery Kart', slugGuess: 'mystery_kart' });
    upsertFlag(db, { category: 'kart', rawValue: 'Mystery Kart', slugGuess: 'mystery_kart' });
    const row = db.prepare(`SELECT occurrences FROM wr_name_flags WHERE raw_value='Mystery Kart'`).get() as any;
    expect(row.occurrences).toBe(2);
  });

  it('resolveFlags stamps resolved_at for names now resolvable (alias) and reportFlags hides them', () => {
    const db = freshDb();
    upsertFlag(db, { category: 'kart', rawValue: 'R.O.B. H.O.G.', slugGuess: 'r_o_b_h_o_g' });
    upsertFlag(db, { category: 'kart', rawValue: 'Still Unknown', slugGuess: 'still_unknown' });
    expect(resolveFlags(db)).toBe(1);                       // R.O.B. resolves via alias
    expect(reportFlags(db)).toContain('Still Unknown');
    expect(reportFlags(db)).not.toContain('R.O.B. H.O.G.');
  });

  it('reports isNew only on the first sighting', () => {
    const db = freshDb();
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).isNew).toBe(true);
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).isNew).toBe(false);
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).isNew).toBe(false);
    const row = db.prepare('SELECT occurrences FROM wr_name_flags WHERE raw_value=?').get('Tiny Titan') as any;
    expect(row.occurrences).toBe(3);
  });

  it('keeps distinct raw values separate', () => {
    const db = freshDb();
    expect(upsertFlag(db, { category: 'kart', rawValue: 'A', slugGuess: 'a' }).isNew).toBe(true);
    expect(upsertFlag(db, { category: 'kart', rawValue: 'B', slugGuess: 'b' }).isNew).toBe(true);
  });
});
