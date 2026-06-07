// Pure helpers for the run-review popup. Kept out of the .svelte component so the
// tricky bits (negative coins, 0-is-valid, time_ms derivation) are unit-testable.
// Mirrors the server's M:SS.mmm rule (pi/src/db/ingest.ts:timeToMs).

const TIME_RE = /^(\d+):(\d{2})\.(\d{3})$/;

export const isValidTime = (t) => TIME_RE.test((t ?? "").toString().trim());

export function parseTimeMs(t) {
  const m = TIME_RE.exec((t ?? "").toString().trim());
  return m ? Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3]) : null;
}

// True when totalMs would be a new PB versus the cached best. A null/undefined best
// means no PB is on record yet, so any finished time counts. Mirrors the Rust
// pb_cache check (sync.rs:is_new_pb): a tie is NOT a PB.
export function isPbTime(totalMs, best) {
  if (totalMs == null) return false;
  return best == null || totalMs < best;
}

// Coins are a signed delta - any integer (incl. negative, incl. 0) is valid.
export const isValidInt = (s) => /^-?\d+$/.test((s ?? "").toString().trim());

// Mushrooms used per lap - a non-negative integer.
export const isValidCount = (s) => /^\d+$/.test((s ?? "").toString().trim());

// A lap row is complete when every field validates.
export const lapComplete = (l) =>
  isValidTime(l.time) && isValidInt(l.coins) && isValidCount(l.shrooms);

// True only when the engine captured a FULL per-lap set: every lap 1..totalLaps with
// a time + non-null coins + non-null shrooms. Per-lap data is all-or-nothing - one
// untracked lap makes the coin deltas / mushroom counts for the rest meaningless, so
// a partial capture is treated as no per-lap data at all. Mirrors Rust laps_complete.
export function lapsComplete(runLaps, totalLaps) {
  if (!Array.isArray(runLaps) || !Number.isInteger(totalLaps) || totalLaps < 1) return false;
  for (let n = 1; n <= totalLaps; n++) {
    const l = runLaps.find((x) => x?.lap === n);
    if (!l || !l.time_str || l.coins == null || l.shrooms == null) return false;
  }
  return true;
}

// Turn the popup's working lap rows ({lap, time, coins, shrooms} as strings) into
// the upload shape. time_ms is derived from the edited string so the server (which
// stores lap.time_ms directly) always gets it.
export function buildLaps(laps) {
  return (laps ?? []).map((l) => ({
    lap: l.lap,
    time_str: l.time.trim(),
    time_ms: parseTimeMs(l.time),
    coins: parseInt(l.coins, 10),
    shrooms: parseInt(l.shrooms, 10),
  }));
}
