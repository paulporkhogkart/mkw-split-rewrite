// Pure logic for the hidden /wr-jobs status page (WR trail recording jobs). Mirrors the
// version.js pattern: everything testable lives here, WrJobsPage.svelte stays thin.
// Statuses come from the Pi's wrJobsStatus() — see pi/src/db/wrJobs.ts.

// rank: sort order within a table — problem states first, then working, waiting, done.
export const STATUS_META = {
  parked:        { label: "parked",        color: "#f87171", rank: 0 },
  cooldown:      { label: "cooldown",      color: "#fbbf24", rank: 0 },
  unprocessable: { label: "unprocessable", color: "#b91c1c", rank: 0 },
  in_progress:   { label: "in progress",   color: "#60a5fa", rank: 1 },
  queued:        { label: "queued",        color: "#9aa1ab", rank: 2 },
  not_queued:    { label: "not queued",    color: "#6f7782", rank: 3 },
  done:          { label: "done",          color: "#4ade80", rank: 4 },
};

const rankOf = (j) => STATUS_META[j.status]?.rank ?? 5;

/** Current-WR rows (problem states first; server course order kept within a band — the sort is
 *  stable) and superseded-WR rows (server order as-is). */
export function splitRows(jobs) {
  const current = jobs.filter((j) => j.is_current);
  const superseded = jobs.filter((j) => !j.is_current);
  current.sort((a, b) => rankOf(a) - rankOf(b));
  return { current, superseded };
}

/** Header-line counts. "stuck" = needs eyes (cooldown, parked, unprocessable) — a superset of
 *  the Pi's stuckJobs() in that it also counts unprocessable WRs, which can never be claimed.
 *  coverage = trailed current WRs / all current WRs (what `wr-flags` prints). */
export function summary(jobs) {
  const n = (pred) => jobs.filter(pred).length;
  const cur = jobs.filter((j) => j.is_current);
  return {
    done: n((j) => j.status === "done"),
    queued: n((j) => j.status === "queued" || j.status === "in_progress" || j.status === "not_queued"),
    stuck: n((j) => j.status === "cooldown" || j.status === "parked" || j.status === "unprocessable"),
    coverage: `${cur.filter((j) => j.status === "done").length}/${cur.length}`,
  };
}

/** SQLite `datetime('now')` strings are "YYYY-MM-DD HH:MM:SS" in UTC with no zone marker. */
export function parseUtc(s) {
  return s ? new Date(s.replace(" ", "T") + "Z") : null;
}

export function relTime(s, now = Date.now()) {
  const d = parseUtc(s);
  if (!d) return "—";
  const ms = d.getTime() - now;
  const abs = Math.abs(ms);
  const [v, u] = abs >= 3600e3 ? [Math.round(abs / 3600e3), "h"]
               : abs >= 60e3   ? [Math.round(abs / 60e3), "m"]
               :                 [Math.round(abs / 1e3), "s"];
  return ms >= 0 ? `in ${v} ${u}` : `${v} ${u} ago`;
}

const trunc = (s, n = 80) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** The per-row detail cell: whatever is most useful for the row's status. */
export function detailOf(j, now = Date.now()) {
  if (j.status === "done") return j.trail_points != null ? `${j.trail_points} pts` : "";
  if (j.status === "in_progress") return `${j.lease_owner ?? "?"} · attempt ${j.attempts}`;
  if (j.status === "cooldown")
    return `retry ${relTime(j.next_eligible_at, now)}${j.last_error ? ` — ${trunc(j.last_error)}` : ""}`;
  if (j.status === "unprocessable") return "no video or unresolved character";
  return j.last_error ? trunc(j.last_error) : "";
}
