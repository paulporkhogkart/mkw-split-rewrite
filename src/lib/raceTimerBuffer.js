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

/** Linear-interpolate { elapsed_ms, completion, pb_delta_ms } at `target`
 *  within `samples` (ascending t). Past the newest -> extrapolate elapsed_ms
 *  at 1ms/ms (capped at EXTRAPOLATE_CAP_MS) and HOLD completion + pb_delta_ms
 *  (a pace delta drifts at the unknown pace difference, not 1ms/ms - holding
 *  is the on-PB-pace assumption). Before the oldest -> CLAMP to the oldest
 *  sample, never backward-extrapolate or go null: after a quiet stretch
 *  (presence idles at the 5s heartbeat while paused, the values frozen) the
 *  buffer restarts from one fresh sample, and the render target trails it by
 *  DELAY_MS - nulling there flashed 0:00.000 + an empty bar on every resume.
 *  Empty -> null. The in-window lerp is what makes the delta readout sweep
 *  smoothly between 4Hz server values instead of stepping. Pure (exported
 *  for tests). */
export function interpolateAt(samples, target) {
  if (!samples || samples.length === 0) return null;
  const newest = samples[samples.length - 1];
  if (target >= newest.t) {
    const ahead = Math.min(target - newest.t, EXTRAPOLATE_CAP_MS);
    return { elapsed_ms: newest.elapsed_ms == null ? null : newest.elapsed_ms + ahead,
             completion: newest.completion, pb_delta_ms: newest.pb_delta_ms };
  }
  if (target <= samples[0].t)
    return { elapsed_ms: samples[0].elapsed_ms, completion: samples[0].completion,
             pb_delta_ms: samples[0].pb_delta_ms };
  let lo = samples[0];
  for (let i = 1; i < samples.length; i++) {
    const hi = samples[i];
    if (hi.t >= target) {
      const f = (target - lo.t) / (hi.t - lo.t || 1);
      return { elapsed_ms: lerp(lo.elapsed_ms, hi.elapsed_ms, f),
               completion: lerp(lo.completion, hi.completion, f),
               pb_delta_ms: lerp(lo.pb_delta_ms, hi.pb_delta_ms, f) };
    }
    lo = hi;
  }
  return { elapsed_ms: newest.elapsed_ms, completion: newest.completion, pb_delta_ms: newest.pb_delta_ms };
}

/** Direction the pace delta is moving for a player: "gain" (delta falling -
 *  catching the PB) | "loss" (rising) | null (steady within the deadband or not
 *  enough buffered history). Compares the buffer ~TREND_WINDOW_MS apart at the
 *  same delayed clock the card renders, so the colour matches the shown value. */
const TREND_WINDOW_MS = 800;
const TREND_DEADBAND_MS = 15;
export function deltaTrendAt(playerId, target, windowMs = TREND_WINDOW_MS) {
  const buf = buffers.get(playerId);
  const cur = interpolateAt(buf, target), past = interpolateAt(buf, target - windowMs);
  if (!cur || !past || cur.pb_delta_ms == null || past.pb_delta_ms == null) return null;
  const d = cur.pb_delta_ms - past.pb_delta_ms;
  if (Math.abs(d) < TREND_DEADBAND_MS) return null;
  return d < 0 ? "gain" : "loss";
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
  return e === est.elapsed_ms ? est : { ...est, elapsed_ms: e };
}
