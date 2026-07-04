/** Sum an array of lap times; null if it is empty or any entry is null. */
function sumLaps(arr) {
  if (!arr.length) return null;
  let s = 0;
  for (const v of arr) { if (v == null) return null; s += v; }
  return s;
}

/** Augment a courseSummary.splits with each player's theoretical best (sum of their best laps)
 *  and the field-ideal total (sum of the per-lap field ideal). */
export function withTheoretical(splits) {
  return {
    laps: splits.laps,
    perPlayer: splits.perPlayer.map((p) => ({ ...p, theoretical: sumLaps(p.best) })),
    fieldIdeal: splits.fieldIdeal,
    fieldIdealTotal: sumLaps(splits.fieldIdeal),
  };
}
