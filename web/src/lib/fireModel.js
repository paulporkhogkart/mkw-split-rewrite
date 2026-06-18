// Stateless "on fire" model for the territory map. A course burns while the leader's
// margin over #2 clears an exponential bar that rises the further the PB sits off the WR.
// lead% and off% are percent-of-WR; bar% = E0 * e^(off%/K). Same metric SP2 will reuse.
export const E0 = 0.2;   // floor: min lead (% of WR) at WR pace
export const K = 4;      // steepness: bar climbs by factor e every K% off the WR

/** The fire bar (min lead, % of WR) at a given % off the WR. */
export function fireBarPct(offPct) {
  return E0 * Math.exp(offPct / K);
}

/** Leader's required lead in ms to stay lit on this course (NaN if no WR). */
export function snuffLeadMs({ t1, wr }) {
  if (!wr || t1 == null) return NaN;
  const offPct = ((t1 - wr) / wr) * 100;
  return (fireBarPct(offPct) / 100) * wr;
}

/** True when the course's leader is "on fire". Needs a real #2 and a current WR. */
export function isOnFire({ t1, t2, wr }) {
  if (!wr || t1 == null || t2 == null || t2 < t1) return false;
  const leadPct = ((t2 - t1) / wr) * 100;
  const offPct = ((t1 - wr) / wr) * 100;
  return leadPct >= fireBarPct(offPct);
}
