/** Parse a lap time `SS.mmm` or `M:SS.mmm` (long tracks) into ms. `-`/empty → null. */
export function lapTimeToMs(raw: string): number | null {
  const t = raw.trim();
  if (!t || t === '-') return null;
  let m = /^(\d+):(\d{1,2})\.(\d{1,3})$/.exec(t);
  if (m) return Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3].padEnd(3, '0'));
  m = /^(\d{1,2})\.(\d{1,3})$/.exec(t);
  if (m) return Number(m[1]) * 1000 + Number(m[2].padEnd(3, '0'));
  return null;
}

/** Parse a per-lap field like `8-12-0-0` into `[8,12,0,0]`. `-`/empty → null. */
export function parsePerLap(raw: string): number[] | null {
  const t = raw.trim();
  if (!t || t === '-') return null;
  const nums = t.split('-').map((p) => Number(p));
  return nums.some((n) => !Number.isFinite(n)) ? null : nums;
}
