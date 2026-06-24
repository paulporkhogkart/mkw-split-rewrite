export const E0 = 0.2;
export const K = 4;

export function fireBarPct(offPct: number): number {
  return E0 * Math.exp(offPct / K);
}

export function isOnFire(t1: number | null, t2: number | null, wr: number | null): boolean {
  if (!wr || t1 == null || t2 == null || t2 < t1) return false;
  const leadPct = ((t2 - t1) / wr) * 100;
  const offPct = ((t1 - wr) / wr) * 100;
  return leadPct >= fireBarPct(offPct);
}
