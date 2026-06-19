// Replay the finished-run stream into the sequence of DISTINCT ownership snapshots.
// Owner of a course = the player with the running-minimum time up to that moment; a snapshot
// is emitted only when some course's leader actually changes (consecutive identical maps are
// deduped). Each snapshot carries `gainColor` = the colour of the last course to flip at that
// `t` (for colouring the scrubber tick). Pure: no DOM, no map knowledge.
export function buildSnapshots(events, colors) {
  const best = {}; // slug -> { player -> ms }
  const owner = {}; // slug -> current leader player
  const snaps = [];
  let i = 0;
  while (i < events.length) {
    const t = events[i].t;
    let changed = false,
      gainColor = null;
    while (i < events.length && events[i].t === t) {
      const e = events[i++];
      const bm = (best[e.slug] = best[e.slug] || {});
      if (bm[e.player] == null || e.ms < bm[e.player]) bm[e.player] = e.ms;
      let lead = null,
        lo = Infinity;
      for (const p in bm)
        if (bm[p] < lo) {
          lo = bm[p];
          lead = p;
        }
      if (lead !== owner[e.slug]) {
        owner[e.slug] = lead;
        changed = true;
        gainColor = colors[lead] || null;
      }
    }
    if (!changed) continue;
    const owners = {};
    for (const slug in owner)
      if (owner[slug]) owners[slug] = { player: owner[slug], color: colors[owner[slug]] || null };
    snaps.push({ t, date: new Date(t).toISOString().slice(0, 10), owners, gainColor });
  }
  return snaps;
}
