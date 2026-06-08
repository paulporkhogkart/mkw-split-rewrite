import { readFileSync, writeFileSync } from 'node:fs';
import type { DatabaseSync } from 'node:sqlite';

/** Bot-owned announce watermark (NOT in the server DB). The bot keeps track of how far it
 *  has announced so it can catch up after downtime without the server knowing it exists. */
export interface BotState { lastPbRunId: number; }

/** Load the watermark from `path`. If it's missing/corrupt, seed it to the current max run
 *  id and persist - so the first launch announces only NEW PBs, never the whole history. */
export function loadState(path: string, db: DatabaseSync): BotState {
  try {
    const s = JSON.parse(readFileSync(path, 'utf8'));
    if (s && typeof s.lastPbRunId === 'number') return { lastPbRunId: s.lastPbRunId };
  } catch { /* missing or corrupt -> seed below */ }
  const max = (db.prepare('SELECT COALESCE(MAX(id),0) AS m FROM runs').get() as { m: number }).m;
  const seeded: BotState = { lastPbRunId: max };
  saveState(path, seeded);
  return seeded;
}

export function saveState(path: string, state: BotState): void {
  writeFileSync(path, JSON.stringify(state));
}
