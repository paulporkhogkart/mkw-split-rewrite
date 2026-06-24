import type { LeaderRow } from '../db/reads';
import { isOnFire } from './fireModel';

export type TurfTransition =
  | { kind: 'claim'; leaderId: number; rivalId: number }
  | { kind: 'fire'; leaderId: number }
  | { kind: 'waver'; leaderId: number };

interface Standing { board: LeaderRow[]; wr: number | null }

export function turfTransitions(before: Standing, after: Standing): TurfTransition[] {
  const out: TurfTransition[] = [];
  const a0 = after.board[0];
  if (!a0) return out;
  const b0 = before.board[0] ?? null;
  const claimed = !!b0 && b0.player_id !== a0.player_id;
  if (claimed) out.push({ kind: 'claim', leaderId: a0.player_id, rivalId: b0!.player_id });

  const fireAfter = isOnFire(a0.total_time_ms, after.board[1]?.total_time_ms ?? null, after.wr);
  const fireBefore = b0 ? isOnFire(b0.total_time_ms, before.board[1]?.total_time_ms ?? null, before.wr) : false;

  if (fireAfter && (claimed || !fireBefore)) out.push({ kind: 'fire', leaderId: a0.player_id });
  else if (!fireAfter && !claimed && fireBefore) out.push({ kind: 'waver', leaderId: a0.player_id });
  return out;
}
