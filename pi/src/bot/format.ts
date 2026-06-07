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
