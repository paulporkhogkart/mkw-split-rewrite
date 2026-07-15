import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { upsertFlag, resolveFlags, reportFlags, markFlagAlerted } from './flags';

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

  it('reports shouldAlert until markFlagAlerted is called, then stays false across repeats', () => {
    const db = freshDb();
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).shouldAlert).toBe(true);
    // Caller hasn't stamped yet (e.g. history reconciler) -> still owed.
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).shouldAlert).toBe(true);
    markFlagAlerted(db, 'kart', 'Tiny Titan');
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).shouldAlert).toBe(false);
    const row = db.prepare('SELECT occurrences FROM wr_name_flags WHERE raw_value=?').get('Tiny Titan') as any;
    expect(row.occurrences).toBe(3);
  });

  it('keeps distinct raw values separate', () => {
    const db = freshDb();
    expect(upsertFlag(db, { category: 'kart', rawValue: 'A', slugGuess: 'a' }).shouldAlert).toBe(true);
    expect(upsertFlag(db, { category: 'kart', rawValue: 'B', slugGuess: 'b' }).shouldAlert).toBe(true);
  });

  it('re-alerts after a resolved flag breaks again', () => {
    const db = freshDb();
    upsertFlag(db, { category: 'kart', rawValue: 'Mach Rocket', slugGuess: 'mach_rocket' });
    markFlagAlerted(db, 'kart', 'Mach Rocket');
    db.exec(`UPDATE wr_name_flags SET resolved_at = datetime('now') WHERE raw_value='Mach Rocket'`);
    // Broken again: alerted_at should clear along with resolved_at, so it's owed again.
    expect(upsertFlag(db, { category: 'kart', rawValue: 'Mach Rocket', slugGuess: 'mach_rocket' }).shouldAlert).toBe(true);
  });
});

describe('resolveFlags', () => {
  it('resolves a course flag once the course exists', () => {
    const d = freshDb();
    upsertFlag(d, { category: 'course', rawValue: 'Wario Shipyard', slugGuess: 'wario_shipyard' });
    expect(resolveFlags(d)).toBe(0);                        // no such course yet
    d.exec("INSERT INTO courses(id,slug,display_name) VALUES (9,'warios_galleon','Warios Galleon')");
    expect(resolveFlags(d)).toBe(1);                        // MKWRS_ALIASES maps it
    const row = d.prepare('SELECT resolved_at FROM wr_name_flags WHERE raw_value=?').get('Wario Shipyard') as any;
    expect(row.resolved_at).not.toBeNull();
  });

  it('still resolves item flags', () => {
    const d = freshDb();
    upsertFlag(d, { category: 'kart', rawValue: 'Mach Rocket', slugGuess: 'mach_rocket' });
    expect(resolveFlags(d)).toBe(1);
  });
});
