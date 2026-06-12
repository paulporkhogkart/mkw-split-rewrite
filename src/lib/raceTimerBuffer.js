// Per-player rolling sample buffer for the live friend-card timer + bar.
// Presence samples arrive at ~4Hz, but the timer must tick like a clock: past
// the newest sample, elapsed_ms is EXTRAPOLATED at 1ms/ms (a race clock
// advances in exact real time), so samples only re-anchor the tick. The card
// renders DELAY_MS in the past so the finish lands cleanly: the engine
// confirms the finish ~150ms after the timer freezes (FinishValueLatch: 3
// identical digit reads at 50ms cadence) and keeps emitting a climbing time
// until then; 200ms of display delay absorbs that window plus transport lag,
// so the extrapolated tick never overshoots the latched final_time. (Under the
// old hold-at-newest rendering the displayed sample itself averaged ~225ms
// old, so the display is still as fresh as before.) Engine/server stay
// un-lagged; the delay is display-only.
export const DELAY_MS = 200;            // > FinishValueLatch latency + transport jitter
export const EXTRAPOLATE_CAP_MS = 4000; // stalled feed: freeze instead of ticking forever
const RESET_BACKWARD_MS = 1500;         // elapsed dropping further than this = new race
const MAX_AGE_MS = DELAY_MS + 1000;

const buffers = new Map();             // player_id -> [{ t, elapsed_ms, completion }] ascending t
const floors = new Map();              // player_id -> last shown elapsed_ms (monotonic per race)

export function pushSample(playerId, sample) {
  let buf = buffers.get(playerId);
  if (!buf) { buf = []; buffers.set(playerId, buf); }
  buf.push(sample);
  const cutoff = sample.t - MAX_AGE_MS;
  while (buf.length > 1 && buf[0].t < cutoff) buf.shift();
}

export function clearBuffer(playerId) { buffers.delete(playerId); floors.delete(playerId); }

function lerp(a, b, f) {
  if (a == null || b == null) return b == null ? a : b;
  return a + (b - a) * f;
}

/** Linear-interpolate { elapsed_ms, completion } at `target` within `samples`
 *  (ascending t). Past the newest -> extrapolate elapsed_ms at 1ms/ms (capped
 *  at EXTRAPOLATE_CAP_MS), hold completion. Before the oldest or empty ->
 *  null. Pure (exported for tests). */
export function interpolateAt(samples, target) {
  if (!samples || samples.length === 0) return null;
  const newest = samples[samples.length - 1];
  if (target >= newest.t) {
    const ahead = Math.min(target - newest.t, EXTRAPOLATE_CAP_MS);
    return { elapsed_ms: newest.elapsed_ms == null ? null : newest.elapsed_ms + ahead,
             completion: newest.completion };
  }
  if (target <= samples[0].t) return null;
  let lo = samples[0];
  for (let i = 1; i < samples.length; i++) {
    const hi = samples[i];
    if (hi.t >= target) {
      const f = (target - lo.t) / (hi.t - lo.t || 1);
      return { elapsed_ms: lerp(lo.elapsed_ms, hi.elapsed_ms, f),
               completion: lerp(lo.completion, hi.completion, f) };
    }
    lo = hi;
  }
  return { elapsed_ms: newest.elapsed_ms, completion: newest.completion };
}

/** interpolateAt over the player's buffer, with a monotonic display floor on
 *  elapsed_ms: a fresh anchor landing slightly behind the extrapolated value
 *  (network jitter) stalls the timer for a beat instead of ticking it
 *  backward. A drop beyond RESET_BACKWARD_MS is a new race - accept it. */
export function sampleAt(playerId, target) {
  const est = interpolateAt(buffers.get(playerId), target);
  if (!est || est.elapsed_ms == null) return est;
  const floor = floors.get(playerId);
  let e = est.elapsed_ms;
  if (floor != null && e < floor && floor - e <= RESET_BACKWARD_MS) e = floor;
  floors.set(playerId, e);
  return e === est.elapsed_ms ? est : { elapsed_ms: e, completion: est.completion };
}
