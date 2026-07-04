import { fireBarPct } from "./fireModel.js";

/** The PB time that seizes #1 AND lights the track: lead the current leader by the fire bar,
 *  evaluated at your own % off WR. Returns { ms, reason }; ms is null when impossible. */
export function fireTargetMs({ leaderMs, wr }) {
  if (wr == null || leaderMs == null) return { ms: null, reason: "no-wr" };
  const lit = (T) => ((leaderMs - T) / wr) * 100 >= fireBarPct(((T - wr) / wr) * 100);
  if (!lit(wr)) return { ms: null, reason: "wr-tight" }; // leader too close to WR to ever be out-lit
  // Largest T in [wr, leaderMs] that is still lit — bisect the crossing.
  let lo = wr, hi = leaderMs;
  for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (lit(mid)) lo = mid; else hi = mid; }
  return { ms: lo, reason: "ok" };
}
