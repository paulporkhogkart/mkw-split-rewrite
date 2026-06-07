import type { OvertakenEntry, Positions } from './types';

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
  const aligned = alignDiffColumn(list.map((p) => p.diff_str));
  return list.map((p, i) => {
    const padded = names[i] + ' '.repeat(Math.max(2, maxName - names[i].length + 2));
    return `\`${padded}(${aligned[i]})\``;
  }).join('\n');
}

/** "1:23.456" / "23.456" — ports legacy TimeUtils.milliseconds_to_display. */
export function msToDisplay(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const msPart = ms % 1000;
  if (totalSeconds >= 60) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}.${String(msPart).padStart(3, '0')}`;
  }
  return `${totalSeconds}.${String(msPart).padStart(3, '0')}`;
}

/** Decimal-align a column of "+1.234s" diff strings: the sign+whole part is right-justified to
 *  the column's max width, with the sign fixed at the left (space inserted between sign and digits).
 *  '' / null entries stay ''. Shared by the leaderboard + nemesis + overtaken formatters (factors
 *  the legacy duplicated alignment). Returns the inner string (no parens). */
export function alignDiffColumn(diffs: (string | null | undefined)[]): string[] {
  const parts = diffs.map((d) => (d ? parseDiff(d) : null));
  const widths = parts.filter((p): p is { sign_and_whole: string; decimal: string } => p !== null)
                      .map((p) => p.sign_and_whole.length);
  const maxBefore = widths.length ? Math.max(...widths) : 0;
  return parts.map((p) => {
    if (!p) return '';
    // Legacy alignment (rjust on the whole sign+whole part): the sign moves with the number,
    // so decimals align but signs may not - matches _format_overtaken / leaderboard formatters.
    const before = p.sign_and_whole.padStart(maxBefore);
    return p.decimal ? `${before}.${p.decimal}s` : `${before}s`;
  });
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
