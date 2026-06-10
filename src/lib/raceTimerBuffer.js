// Per-player rolling sample buffer for the live friend-card timer + bar.
// The card renders these two live indicators DELAYED by DELAY_MS so the finish
// lines up: the real finish is only confirmed STILL_SECONDS (2.5 s) after the
// timer freezes, so the engine keeps emitting a climbing time for ~2.5 s; running
// the display 2.5 s behind makes it reach the total exactly as the finished
// result arrives. Engine/server stay un-lagged; the delay is purely display-side.
export const DELAY_MS = 2500;          // == FinishStillDetector.STILL_SECONDS
const MAX_AGE_MS = DELAY_MS + 1000;

const buffers = new Map();             // player_id -> [{ t, elapsed_ms, completion }] ascending t

export function pushSample(playerId, sample) {
  let buf = buffers.get(playerId);
  if (!buf) { buf = []; buffers.set(playerId, buf); }
  buf.push(sample);
  const cutoff = sample.t - MAX_AGE_MS;
  while (buf.length > 1 && buf[0].t < cutoff) buf.shift();
}

export function clearBuffer(playerId) { buffers.delete(playerId); }

function lerp(a, b, f) {
  if (a == null || b == null) return b == null ? a : b;
  return a + (b - a) * f;
}

/** Linear-interpolate { elapsed_ms, completion } at `target` within `samples`
 *  (ascending t). Past the newest -> hold newest. Before the oldest or empty ->
 *  null. Pure (exported for tests). */
export function interpolateAt(samples, target) {
  if (!samples || samples.length === 0) return null;
  const newest = samples[samples.length - 1];
  if (target >= newest.t) return { elapsed_ms: newest.elapsed_ms, completion: newest.completion };
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

export function sampleAt(playerId, target) {
  return interpolateAt(buffers.get(playerId), target);
}
