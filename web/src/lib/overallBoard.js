/** Overall standings from per-course boards: sum each player's per-track best time.
 *  boards: [{ standings: [{ player, ms }] }] -> [{ player, total_ms, tracks }] fastest total first. */
export function overallBoard(boards) {
  const sum = {}, cnt = {};
  for (const b of boards)
    for (const s of b.standings) { sum[s.player] = (sum[s.player] || 0) + s.ms; cnt[s.player] = (cnt[s.player] || 0) + 1; }
  return Object.keys(sum)
    .map((player) => ({ player, total_ms: sum[player], tracks: cnt[player] }))
    .sort((a, b) => a.total_ms - b.total_ms);
}
