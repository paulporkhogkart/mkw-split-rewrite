// Decides when a player card should be "on fire" — i.e. visibly on PB pace
// during a run. Two trigger flavours, matching the card's delta mode:
//
//   pace  — the live (fluid) pace delta is ahead of PB *continuously* for the
//           on-window (the user's "consistently under PB"). A separate off-window
//           keeps it from flickering as the live delta hovers around zero.
//   laps  — the last completed lap split is under PB; a settled per-lap signal,
//           so it lights at once (on-window 0) and still uses the off-window.
//
// State is a small per-player latch (the sample buffer only holds ~1.2s, far
// less than the on-window, so the latch must own its own timing). Pure given the
// injected `now`; `updateFire` is called once per render tick from PlayerCard.
const states = new Map(); // player_id -> { lit, aheadSince, behindSince }

export const FIRE_ON_MS_PACE = 2000;  // pace: ahead this long, unbroken, lights it
export const FIRE_OFF_MS = 400;       // either mode: behind this long drops it (anti-flicker)

/** Drop one player's latch, or every latch when playerId is omitted. */
export function clearFire(playerId) {
  if (playerId == null) states.clear();
  else states.delete(playerId);
}

/**
 * @param {number|string} playerId
 * @param {{ ahead:boolean, racing:boolean, now:number, mode:"pace"|"laps" }} o
 * @returns {boolean} whether this player's card should render fire this tick.
 */
export function updateFire(playerId, { ahead, racing, now, mode }) {
  if (!racing) { states.delete(playerId); return false; }
  let st = states.get(playerId);
  if (!st) { st = { lit: false, aheadSince: null, behindSince: null }; states.set(playerId, st); }

  const onMs = mode === "laps" ? 0 : FIRE_ON_MS_PACE;
  if (ahead) {
    st.behindSince = null;
    if (st.aheadSince == null) st.aheadSince = now;
    if (!st.lit && now - st.aheadSince >= onMs) st.lit = true;
  } else {
    st.aheadSince = null;
    if (st.behindSince == null) st.behindSince = now;
    if (st.lit && now - st.behindSince >= FIRE_OFF_MS) st.lit = false;
  }
  return st.lit;
}
