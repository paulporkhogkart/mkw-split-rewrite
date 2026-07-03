// Pure turf-leaderboard helpers: standings from an ownership snapshot + the
// deterministic "jank" (shapes/rotations/offsets/digit transforms) the column
// draws with. No DOM, no randomness (stable across animation frames).

/** Courses owned per player in a snapshot ({slug:{player}} -> {player:count}). */
export function courseCounts(snapshot) {
  const out = {};
  const owners = snapshot?.owners || {};
  for (const slug in owners) {
    const p = owners[slug]?.player;
    if (p) out[p] = (out[p] || 0) + 1;
  }
  return out;
}

/** Compare two players' summed track times (turf tie-break): lower time ranks higher.
 *  A missing total (no `snapshot.totals`) sorts last; two missing totals are equal. */
export function cmpTotalMs(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a - b;
}

/** Standings for a snapshot: every roster player (keys of `colors`), sorted by
 *  courses desc, then by summed track time asc (equal pct → lower total time ranks
 *  higher), then player name asc. pct = round(courses/total*100). */
export function turfStandings(snapshot, colors, totalCourses = 30) {
  const counts = courseCounts(snapshot);
  const totals = snapshot?.totals || {};
  return Object.keys(colors || {})
    .map((player) => ({
      player,
      color: colors[player],
      courses: counts[player] || 0,
      pct: Math.round(((counts[player] || 0) / totalCourses) * 100),
      totalMs: totals[player],
    }))
    .sort((a, b) =>
      b.courses - a.courses ||
      cmpTotalMs(a.totalMs, b.totalMs) ||
      (a.player < b.player ? -1 : a.player > b.player ? 1 : 0))
    .map((r, i) => ({ ...r, rank: i + 1 }));
}

// --- deterministic jank (stable per player / index; NEVER Math.random) ---
const ROT = [-1.6, 1.4, -1.9, 1.5, -1.2];   // card tilt by roster index
const OY = [5, 4, 4, 3, 3];                  // colour-border vertical offset
const FX = { aliias: 12 };                   // per-figure edge push (px) — wide angle-of-photo figure

/** Fixed card styling for a player (playerKey) at stable roster index i. */
export function cardConfig(key, i) {
  return { shape: (i % 5) + 1, rot: ROT[i % 5], ox: 5, oy: OY[i % 5], fx: FX[key] || 0 };
}

const DIG_ROT = [-4, 5, -3, 4, -5];
const DIG_TY = [0, -5, 3, -4, 2];
/** Per-digit ransom transform, stable by digit position. */
export function digitJank(i) {
  return { rot: DIG_ROT[i % 5], ty: DIG_TY[i % 5] };
}
