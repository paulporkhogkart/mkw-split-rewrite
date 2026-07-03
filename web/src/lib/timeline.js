// Unset-track penalty for the turf tie-break: a player with no time on a track is scored as
// 9:59.999 (the DNF cap) when summing their per-track total.
export const DNF_MS = 9 * 60000 + 59 * 1000 + 999; // 599999

// Sum of every roster player's running-best time across ALL courses as of the current `best`
// map: known tracks contribute their best ms, unset tracks contribute DNF_MS. Used only as the
// turf tie-break between players on equal course counts (lower total ranks higher).
function playerTotals(best, colors, totalCourses) {
  const sum = {}, cnt = {};
  for (const slug in best) {
    const bm = best[slug];
    for (const p in bm) { sum[p] = (sum[p] || 0) + bm[p]; cnt[p] = (cnt[p] || 0) + 1; }
  }
  const out = {};
  for (const p in (colors || {})) out[p] = (sum[p] || 0) + (totalCourses - (cnt[p] || 0)) * DNF_MS;
  return out;
}

// Replay the finished-run stream into the sequence of DISTINCT ownership snapshots.
// Owner of a course = the player with the running-minimum time up to that moment; a snapshot
// is emitted only when some course's leader actually changes (consecutive identical maps are
// deduped). Each snapshot carries `gainColor` = the colour of the last course to flip at that
// `t` (for colouring the scrubber tick) and `totals` = per-player summed track time as of that
// frame (turf tie-break, DNF-filled for unset tracks). Pure: no DOM, no map knowledge.
export function buildSnapshots(events, colors, totalCourses = 30) {
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
    snaps.push({ t, date: new Date(t).toISOString().slice(0, 10), owners, gainColor,
      totals: playerTotals(best, colors, totalCourses) });
  }
  return snaps;
}

// Slugs whose owning player differs between two ownership snapshots (capture, first
// claim, or loss). snapA may be null (everything in snapB counts as flipped). Pure.
export function flippedCourses(snapA, snapB) {
  const a = snapA ? snapA.owners : {};
  const b = snapB.owners;
  const slugs = new Set([...Object.keys(a), ...Object.keys(b)]);
  const out = [];
  for (const slug of slugs) {
    if ((a[slug]?.player ?? null) !== (b[slug]?.player ?? null)) out.push(slug);
  }
  return out.sort();
}

// Per-course leaderboard AS OF time `t`: each player's running-minimum ms among that course's
// events with `event.t <= t`, sorted ascending. Drives the historical hover popup off the same
// event stream that builds the ownership snapshots, so the board matches the map. Pure.
export function leaderboardAt(events, slug, t) {
  const best = {}; // player -> min ms up to t
  for (const e of events) {
    if (e.slug !== slug || e.t > t) continue;
    if (best[e.player] == null || e.ms < best[e.player]) best[e.player] = e.ms;
  }
  return Object.entries(best)
    .map(([player, ms]) => ({ player, ms }))
    .sort((a, b) => a.ms - b.ms);
}

// The WR in effect for `slug` at time `t`: the minimum record_ms among that course's history
// entries achieved by `t` (achievedMs <= t). null when none exist yet. Entries arrive pre-sorted
// ascending by achievedMs, but we scan all and take the running min (no early break) so a stray
// out-of-order/legacy row can never report a slower record than one already achieved. At
// t = Infinity this is the best-ever = the current WR, so the LIVE frame is unchanged. Pure.
export function wrAsOf(wrHistory, slug, t) {
  const entries = wrHistory[slug];
  if (!entries) return null;
  let best = null;
  for (const [achievedMs, recordMs] of entries) {
    if (achievedMs > t) continue;              // not yet achieved at this frame
    if (best == null || recordMs < best) best = recordMs;
  }
  return best;
}
