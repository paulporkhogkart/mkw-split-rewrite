import type { DatabaseSync } from 'node:sqlite';
import { randomBytes, createHash } from 'node:crypto';

export function hashToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

/** Generate a token for an existing player, store its hash, return the plaintext (shown once). */
export function mintToken(db: DatabaseSync, displayName: string): string {
  const player = db.prepare('SELECT id FROM players WHERE display_name = ? COLLATE NOCASE').get(displayName) as { id: number } | undefined;
  if (!player) throw new Error(`unknown player: ${displayName}`);
  const token = randomBytes(32).toString('hex');
  db.prepare('UPDATE players SET auth_token_hash=? WHERE id=?').run(hashToken(token), player.id);
  return token;
}

export function playerByToken(db: DatabaseSync, token: string): { id: number; display_name: string } | null {
  const row = db.prepare('SELECT id, display_name FROM players WHERE auth_token_hash=?').get(hashToken(token)) as any;
  return row ?? null;
}

/** Set a player's curated trail colour (a CSS hex like "#9b6bd0"). Throws for unknown player. */
export function setPlayerColor(db: DatabaseSync, displayName: string, color: string): void {
  const info = db.prepare('UPDATE players SET color=? WHERE display_name=? COLLATE NOCASE').run(color, displayName);
  if (info.changes === 0) throw new Error(`unknown player: ${displayName}`);
}
