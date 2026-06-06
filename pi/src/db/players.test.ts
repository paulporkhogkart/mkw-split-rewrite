import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { mintToken, playerByToken, hashToken, setPlayerColor } from './players';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  return db;
}

describe('token auth', () => {
  it('mintToken stores a hash and returns the plaintext once', () => {
    const db = seeded();
    const token = mintToken(db, 'Paul');
    expect(token).toMatch(/^[a-f0-9]{64}$/);
    const row = db.prepare('SELECT auth_token_hash FROM players WHERE display_name=?').get('Paul') as any;
    expect(row.auth_token_hash).toBe(hashToken(token));
  });
  it('playerByToken resolves the player, null for a bad token', () => {
    const db = seeded();
    const token = mintToken(db, 'Paul');
    expect(playerByToken(db, token)?.display_name).toBe('Paul');
    expect(playerByToken(db, 'deadbeef')).toBeNull();
  });
  it('mintToken throws for an unknown player', () => {
    expect(() => mintToken(seeded(), 'Nobody')).toThrow();
  });
});

describe('player colour', () => {
  it('setPlayerColor sets the colour (case-insensitive); throws for an unknown player', () => {
    const db = seeded();
    setPlayerColor(db, 'paul', '#9b6bd0');
    expect((db.prepare('SELECT color FROM players WHERE display_name=?').get('Paul') as any).color).toBe('#9b6bd0');
    expect(() => setPlayerColor(db, 'Nobody', '#ffffff')).toThrow();
  });
});
