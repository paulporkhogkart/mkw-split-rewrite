# WR trail job status page — design

**Date:** 2026-07-21 · **Status:** APPROVED (read-only scope confirmed by Paul)

A hidden website subpage, `/wr-jobs` (same hiddenness model as `/version`), showing the status of
every WR trail-recording job: done, in progress, queued, in retry cooldown, parked, or
unprocessable. Read-only — no actions; interventions stay in the Pi CLIs (`npm run wr-flags`,
`reviveStaleEngineJobs`, reconcile backfill).

## Why

The WR service is autonomous (Pi leases jobs, a tray worker on Paul's PC processes them) and its
only visibility today is Discord stuck-job embeds and the `wr-flags` CLI on the Pi. Paul wants a
glanceable list of all WRs and where each one's trail stands.

## Approach

New token-free read endpoint on the Pi + new URL-only Svelte page on the site. Rejected
alternatives: piggybacking job fields onto `/v1/world-records` (pollutes a payload the desktop app
and site already consume) and client-side stitching (job state is not exposed by any existing
public read, so a new endpoint is needed regardless).

## Pi endpoint — `GET /v1/wr-jobs`

- **Data:** `wrJobsStatus(db)` in `pi/src/db/wrJobs.ts`. One query over
  `world_records × courses` LEFT JOINed to `wr_jobs` and `wr_trails`, returning every **current,
  non-removed** WR plus any **non-current** WR that has a job row or a trail (superseded-but-
  processed history stays visible). Removed WRs (`removed_at IS NOT NULL`) are excluded entirely,
  consistent with claiming and the scraper.
- **Route:** added to the existing `pi/src/api/wrJobs.ts` route group; path added to
  `PUBLIC_READS` in `pi/src/api/app.ts` (exact-path match, permissive CORS — same treatment as
  `/v1/version`).
- **Response:** `{ jobs: [...] }`, one row per WR:
  `wr_id, course, course_slug, cc, holder_name, record_str, is_current, status, attempts,
  last_error, updated_at, lease_owner, next_eligible_at, trail_points, trail_recorded_at`
  (non-applicable fields null).

### Status derivation (server-side, one place)

Derived with the **same predicates `claimJob` and `stuckJobs` use** — cooldown windows
1h (attempts ≥ `FREE_ATTEMPTS`=5) / 6h (≥ 8) / 24h (≥ 12) off `updated_at`, `time_mismatch%`
terminal — so the page can never disagree with the queue. Evaluated in order:

| Status | Predicate |
|---|---|
| `done` | `wr_trails` row exists (report point count + `recorded_at`) |
| `in_progress` | live lease: `lease_until >= now` (report `lease_owner`, attempt #) |
| `parked` | `last_error LIKE 'time_mismatch%'` — terminal until reconcile sees a new video link |
| `unprocessable` | `video_url IS NULL OR character_slug IS NULL` — can never be claimed (job row or not) |
| `cooldown` | `attempts >= 5` and inside the retry window (report `next_eligible_at` = `updated_at` + window, and `last_error`) |
| `queued` | job row claimable right now (attempts < 5, or cooldown elapsed); a row with a `last_error` is still `queued` — the error and attempt count are shown alongside |
| `not_queued` | no job row (reachable only for non-current WRs; v1 never enqueued them — `seedWrJobs` seeds every current videoed WR on boot) |

`next_eligible_at` is computed in SQL from the same CASE as `claimJob` so the two cannot drift.
`last_error` passes through as stored (already capped at 500 chars by `failJob`).

## Web page — `web/src/WrJobsPage.svelte` at `/wr-jobs`

- **Routing:** `"wr-jobs"` added to `lib/view.js` (URL-only — no navbar link, falls back to
  `live` for unknown paths as today) + the view case in `App.svelte`. URL builder `wrJobsUrl()`
  in `lib/api.js`.
- **Look:** `VersionPage`'s exact dark-table styling (Inter, 13 px tables, uppercase column
  headers, mono numerics). This is an ops page — the KART-OFF print language does not apply,
  matching `/version`.
- **Content:**
  - Summary line: `N done / M queued / K stuck` plus trail coverage over current WRs
    (`X/Y current WRs trailed`, mirroring what `wr-flags` prints).
  - Main table: current WRs, problem states (`parked`, `cooldown`, `unprocessable`) sorted to the
    top, then `in_progress`, `queued`, `done`; within a status, by course name. Columns: course,
    cc, holder, record time, status (colour-coded dot + label), attempts, detail (truncated
    `last_error` / worker id / next-retry time / trail point count), last updated.
  - Second table (only if non-empty): superseded WRs with a job row or trail, same columns.
  - Status colours: green `done`, blue `in_progress`, grey `queued`/`not_queued`, amber
    `cooldown`, red `parked`, dim red `unprocessable`.
- **Behaviour:** fetch on mount, re-poll every 30 s (`setInterval`, cleared on destroy) so a job
  can be watched moving claim → done. Timestamps rendered as relative ("3 m ago") with the
  absolute time on hover.

## Error handling

- Page: fetch failure or non-OK shows the `/version` "Couldn't load" message pattern; a failed
  poll keeps the last good table and retries on the next tick.
- Endpoint: single query — no partial-data path; an unexpected throw is a plain 500.

## Testing

- **Pi (vitest beside `wrJobs.ts` / `wrJobs.test.ts`):** drive one fixture WR through each status —
  done, in-progress (live lease), queued fresh, queued-after-cooldown-elapsed, cooldown at each
  tier boundary (attempts 5/8/12), parked `time_mismatch`, unprocessable (no video; no character
  slug), superseded with job, superseded without (`not_queued`), removed WR excluded.
  `next_eligible_at` asserted against the claim CASE. Route test: 200 token-less (PUBLIC_READS),
  payload shape.
- **Web (vitest):** `view.js` maps `/wr-jobs` → `"wr-jobs"`; the status-grouping/sort helper and
  the summary-count helper (pure functions, extracted for testability like `version.js`).

## Out of scope

- Any write/action (retry-now, re-enqueue) — decided read-only.
- Navbar link or KART-OFF styling — hidden ops page.
- Live WS updates — 30 s polling is enough for a debug page.
- Back-catalogue (non-current) enqueueing — unchanged v1 policy.
