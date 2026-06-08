import { BODY_SOURCE_COLUMNS } from './body';

const OPS = new Set(['<', '<=', '>', '>=', '=']);

export interface BodyCondition { column: string; op: string; value: number; }

/** Parse "bmi<22" / "body_fat>=20.5" into a structured condition (column = normalized id). */
export function parseBodyCondition(raw: string): BodyCondition {
  const m = /^([a-z_]+)\s*(<=|>=|<|>|=)\s*(-?\d+(\.\d+)?)$/.exec(raw.trim());
  if (!m) throw new Error(`invalid body_condition: ${raw}`);
  const [, column, op, num] = m;
  if (!(column in BODY_SOURCE_COLUMNS)) throw new Error(`unknown body column: ${column}`);
  if (!OPS.has(op)) throw new Error(`invalid op: ${op}`);
  return { column, op, value: Number(num) };
}

/** WHERE fragment keeping runs whose player's latest weigh-in (<= ended_at) matches the
 *  condition. Assumes porker is ATTACHed AS porker; `tables` are the present porker tables
 *  (porker schema). Identity bridges run.player_id -> players.display_name -> porker table. */
export function bodyConditionSql(cond: BodyCondition, tables: { table: string; player: string }[]):
    { join: string; where: string; params: unknown[] } {
  if (tables.length === 0) return { join: '', where: '1=0', params: [] };
  const col = BODY_SOURCE_COLUMNS[cond.column];
  const unions = tables.map((m) =>
    `SELECT '${m.player}' AS player, "${col}" AS v, "Timestamp" AS ts FROM porker."${m.table}"`).join(' UNION ALL ');
  const where = `(
    SELECT b.v FROM ( ${unions} ) b
    JOIN players pp ON pp.display_name = b.player COLLATE NOCASE
    WHERE pp.id = r.player_id AND b.ts <= CAST(strftime('%s', datetime(r.ended_at)) AS INTEGER)
    ORDER BY b.ts DESC LIMIT 1
  ) ${cond.op} ?`;
  return { join: '', where, params: [cond.value] };
}
