// Pure helpers for the run-review popup. Kept out of the .svelte component so the
// tricky bits (negative coins, 0-is-valid, time_ms derivation) are unit-testable.
// Mirrors the server's M:SS.mmm rule (pi/src/db/ingest.ts:timeToMs).

const TIME_RE = /^(\d+):(\d{2})\.(\d{3})$/;

export const isValidTime = (t) => TIME_RE.test((t ?? "").toString().trim());

export function parseTimeMs(t) {
  const m = TIME_RE.exec((t ?? "").toString().trim());
  return m ? Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3]) : null;
}

// Coins are a signed delta - any integer (incl. negative, incl. 0) is valid.
export const isValidInt = (s) => /^-?\d+$/.test((s ?? "").toString().trim());

// Mushrooms used per lap - a non-negative integer.
export const isValidCount = (s) => /^\d+$/.test((s ?? "").toString().trim());

// A lap row is complete when every field validates.
export const lapComplete = (l) =>
  isValidTime(l.time) && isValidInt(l.coins) && isValidCount(l.shrooms);

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
