/** "+1.234s" / "-0.842s" / "±0.000s" — ports legacy TimeUtils.format_time_difference. */
export function formatTimeDifference(ms: number): string {
  if (ms === 0) return '±0.000s';
  const sign = ms > 0 ? '+' : '';
  return `${sign}${(ms / 1000).toFixed(3)}s`;
}

/** "3 DAY" / "2 HOUR" ... — ports legacy DiscordBot._format_duration (singular labels). */
export function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s} SECOND`;
  if (s < 3600) return `${Math.floor(s / 60)} MINUTE`;
  if (s < 86400) return `${Math.floor(s / 3600)} HOUR`;
  if (s < 2592000) return `${Math.floor(s / 86400)} DAY`;
  if (s < 31536000) return `${Math.floor(s / 2592000)} MONTH`;
  return `${Math.floor(s / 31536000)} YEAR`;
}

import type { OvertakenEntry, Positions } from './types';

function parseDiff(diff: string): { sign_and_whole: string; decimal: string } {
  if (diff.endsWith('s')) {
    const t = diff.slice(0, -1);
    if (t.includes('.')) { const [b, a] = t.split('.'); return { sign_and_whole: b, decimal: a }; }
    return { sign_and_whole: t, decimal: '000' };
  }
  return { sign_and_whole: diff, decimal: '' };
}

/** Monospace, name-padded, decimal-aligned overtaken list — ports legacy _format_overtaken. */
export function formatOvertaken(list: OvertakenEntry[]): string {
  if (list.length === 0) return '`No-one`';
  const names = list.map((p) => p.name);
  const maxName = Math.max(...names.map((n) => n.length));
  const parts = list.map((p) => parseDiff(p.diff_str));
  const maxBefore = Math.max(...parts.map((pt) => pt.sign_and_whole.length));
  return list.map((p, i) => {
    const name = names[i];
    const pt = parts[i];
    const padded = name + ' '.repeat(Math.max(2, maxName - name.length + 2));
    const before = pt.sign_and_whole.padStart(maxBefore);
    const aligned = pt.decimal ? `${before}.${pt.decimal}s` : `${before}s`;
    return `\`${padded}(${aligned})\``;
  }).join('\n');
}

/** Track/total position transitions — ports legacy _format_positions. */
export function formatPositions(pos: Positions): string {
  const t = pos.track;
  const o = pos.total;
  const lines: string[] = [];
  if (t.old && t.new) lines.push(`\`Track: ${t.old} → ${t.new}\``);
  else if (t.new) lines.push(`\`Track: New → ${t.new}\``);
  if (o.old && o.new) {
    if (o.old === o.new) return lines.length ? lines.join('\n') : '`New record`';
    lines.push(`\`Total: ${o.old} → ${o.new}\``);
  } else if (o.new) {
    lines.push(`\`Total: New → ${o.new}\``);
  }
  return lines.length ? lines.join('\n') : '`New record`';
}
