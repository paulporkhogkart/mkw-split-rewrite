// Pure helpers for LiveCard.svelte — the locked print language's math. No DOM, no Svelte.
// Sources: docs/design/site-redesign/live-card.html (LOCKED header = the decision log).

export const JANK = [
  "rotate(-3deg)", "rotate(4deg) translateY(-5px)", "rotate(-2.5deg) translateY(3px)",
  "rotate(3deg) translateY(-4px)", "rotate(-4deg) translateY(2px)",
];

/** Settled timers render as janked digit spans; the PB wave staggers 0.07s per char. */
export function digitSpans(text) {
  return [...(text || "")].map((ch, i) => ({ ch, tj: JANK[i % 5], wd: +(i * 0.07).toFixed(2) }));
}

/** Deterministic per-index y-jitter for zigzag peaks (mockup's hand-authored vibe). */
const YS = [12, 4, 12, 5, 10, 11, 3, 13, 5, 11, 10, 4, 12, 5, 10];

/** One 5-point zigzag segment starting at x, width w, points at YS offsets. */
function seg(x, w, yi) {
  const px = [0, 0.25, 0.5, 0.75, 0.92].map((f) => +(x + f * w).toFixed(1));
  const py = px.map((_, i) => YS[(yi + i) % YS.length]);
  return `M${px[0]},${py[0]} ` + px.slice(1).map((v, i) => `L${v},${py[i + 1]}`).join(" ");
}

/** Segmented zigzag mini-track: laps as gapped segments; `fill` = race completion 0..1.
 *  Done laps ink solid, the current lap fills via dashoffset, future laps ghost. */
export function zigzag(laps, fill, w = 128) {
  const n = Number.isInteger(laps) && laps >= 1 ? laps : 1;
  const usable = w - 4, gap = n > 1 ? 8 : 0;
  const segW = (usable - gap * (n - 1)) / n;
  const f = Math.min(1, Math.max(0, fill || 0));
  const cur = f >= 1 ? n : Math.floor(f * n);        // index of the in-progress lap
  const lapFrac = f >= 1 ? 1 : f * n - cur;
  const out = { done: [], current: null, future: [] };
  for (let i = 0; i < n; i++) {
    const d = seg(2 + i * (segW + gap), segW, i * 3);
    if (i < cur) out.done.push(d);
    else if (i === cur && f < 1) out.current = { d, offset: Math.min(100, Math.max(0, 100 - lapFrac * 100)) };
    else out.future.push(d);
  }
  return out;
}

/** ATT + RACING micro-tags from viewModel().activity. */
export function sessTags(activity, now) {
  if (!activity) return { att: null, racing: null };
  let racing = null;
  if (activity.sinceMs != null) {
    const s = Math.max(0, Math.floor((now - activity.sinceMs) / 1000));
    racing = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  return { att: activity.count ?? null, racing };
}
