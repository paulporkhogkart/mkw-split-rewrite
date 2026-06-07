# WR Scraper — Design

Date: 2026-06-07
Sub-project: B/C boundary (the server's WR ingestion job; consumed later by C)
Status: approved, ready for implementation plan

## Goal

A server-side job on the pi that periodically reads the global Mario Kart World
world-record table from mkwrs.com and makes the canonical `world_records` store
**mirror that table's current WR per course**, while keeping an append-only history of
past records and enriching rows when mkwrs adds metadata later (videos, character,
vehicle). A live event is emitted whenever the current WR changes so sub-project C
(broadcast/website) can react.

**Correctness principle (the governing rule):** whatever the mkwrs table shows is the
truth for the current WR. Our served current WR must equal it at the last scrape —
even when a record is removed/DQ'd on mkwrs and the current WR *reverts to a slower,
older time*. We do not assume WRs only get faster.

This is a fresh TypeScript implementation co-located with the pi server that owns the
DB. The legacy `legacy/mkwpb2/kart-off/services/mkwrs_monitor.py` is a reference for
the page structure and field extraction, not a port target (its append-only,
strictly-faster logic does not handle reverts).

## Context (what already exists)

- Source: `https://mkwrs.com/mkworld/`. A `table.wr` lists one current-WR row per
  track. Columns (verified live, matches legacy indices): `[0]` track name (linked),
  `[1]` time + video link, `[2]` player (linked), `[3]` nation flag, `[4]` date
  (`YYYY-MM-DD`), `[5]` duration, `[6]` character, `[7]` vehicle, `[8]` splits icon.
  Time format is `M'SS"mmm` (e.g. `1'47"414`); ms can be 1-3 digits. The page also
  lists glitch categories ("Mario Bros. Circuit (Glitch)", "Crown City (Glitch)")
  that are NOT canonical courses and must be excluded. No cc selector exists; the
  page is the standard (150cc) records.
- Course names on mkwrs match the canonical display names except **"Wario Shipyard"**
  (mkwrs) vs **"Wario's Galleon"** / `warios_galleon` (canonical) - the same single
  alias the Python importer carries in `server/courses.py:LEGACY_ALIASES`.
- The pi server (`pi/`): TypeScript + Hono + `node:sqlite`, shares `server/schema.sql`
  as the single schema source (B has edited it before: `auth_token_hash`, `attempt_id`,
  `color`). Node 22 (global `fetch`, `node:sqlite`). Minimal-dep ethos.
- `server/schema.sql` defines `world_records(id, course_id, cc, holder_name, record_ms,
  record_str, achieved_at, video_url, character, vehicle, provenance DEFAULT
  'legacy_import', created_at)` with `idx_wr_course(course_id, cc, achieved_at)`.
  `world_records.provenance` has **no** CHECK constraint, so `'scraped'` is valid.
- `pi/mkw.db` holds 30 courses + 473 WRs, all `provenance='legacy_import'`. WRs are
  GLOBAL and not season-scoped.
- `pi/src/db/reads.ts:currentWr(db, courseId, cc)` reads the current WR; consumed by
  `runs.ts` (gap-to-WR / `wr_beaten`), the `GET /v1/world-records` route, and one test.
  `runs.ts` already tolerates a null WR.
- `pi/src/db/slug.ts:slugify` already does lowercase + apostrophe-drop + non-alnum->`_`.
- Standalone-script pattern: `pi/src/scripts/*.ts` run via
  `node --no-warnings --import tsx ...`, registered in `package.json` scripts.
- Event plumbing: `pi/src/api/events.ts:EventHub` (`publish`/`subscribe`); the
  `/v1/events` WS forwards every published `ServerEvent` (`pi/src/db/types.ts`).
- `pi/src/db/connect.ts:applySchema` execs `server/schema.sql` then applies additive
  `ALTER TABLE ... ADD COLUMN` migrations in try/catch for pre-existing DBs (the
  `color` column precedent).

## Decisions (from brainstorming)

- **Language/runtime:** TypeScript in `pi/` (co-located with the DB-owning server).
- **Run model:** in-process scheduler started by `server.ts` (so a current-WR change
  can broadcast live), with the same pure logic also exposed as a one-shot
  `npm run scrape-wr` CLI for tests and manual/cron runs.
- **HTML parser:** `node-html-parser` (tiny, zero transitive deps, pure JS).
- **Reconcile model:** mirror the mkwrs table. The current WR per `(course, cc)` is
  marked by a new `is_current` flag; each scrape makes our `is_current` row equal the
  page's row, moving it to a slower record on a revert. Append-only history is kept;
  a revert reuses the existing history row rather than duplicating it.

## Module layout (new `pi/src/wr/`)

Mechanism (pure, network-free, unit-tested) is separated from policy (when to run):

| File | Responsibility |
|------|----------------|
| `time.ts` | `mkwrsTimeToMs("1'47\"414"): number` and `msToTimeStr(107414): "1:47.414"`. Normalizes 1-3 digit ms to 3 digits. Throws on unparseable input. Pure. |
| `courses.ts` | `MKWRS_ALIASES = { "Wario Shipyard": "warios_galleon" }`; `mkwrsNameToSlug(name): string` (alias -> else `slugify`). `resolveCourseId(db, name): number \| null` (null for `(Glitch)` and any name with no canonical course). Reuses `db/slug.ts`. |
| `parse.ts` | `parseWrTable(html: string): ScrapedWr[]` using `node-html-parser`. Selects `table.wr` rows, extracts fields by column, drops the header and any row with `< 9` cells, drops `(Glitch)` rows. Pure. |
| `reconcile.ts` | `reconcile(db, hub, scraped: ScrapedWr[], cc): WrReport`. Per row: resolve course, mirror it to our current WR (match / insert / reflag-on-revert / backfill). |
| `scrape.ts` | `scrapeOnce(db, hub, opts): Promise<WrReport>` orchestrator: fetch -> parse -> reconcile. `opts.fetchHtml?: () => Promise<string>` injectable (default = global `fetch(url, { headers: { 'User-Agent': ... }, signal: AbortSignal.timeout(30_000) })`); `opts.url` (default `https://mkwrs.com/mkworld/`); `opts.cc` (default 150). |
| `scheduler.ts` | `startWrScraper(db, hub, { url, intervalSec }): () => void`. Runs one scrape immediately (async, non-blocking), then every `intervalSec`. Each tick wrapped in try/catch (a failure logs and is swallowed - never throws, never takes down the server). A re-entrancy guard skips a tick if the previous one is still running. Returns a stop function. |

Plus:
- `pi/src/scripts/scrapeWr.ts`: one-shot CLI -> `npm run scrape-wr`. Opens the DB
  (`MKW_DB`), `applySchema`, runs `scrapeOnce` with a no-op hub, prints the `WrReport`,
  exits non-zero on a hard failure (network/parse).
- `pi/src/server.ts`: calls `startWrScraper(db, hub, { url: process.env.MKWRS_URL,
  intervalSec: Number(process.env.MKWRS_INTERVAL_SEC ?? 300) })` after the WS is
  injected. `MKWRS_INTERVAL_SEC=0` disables the in-process scheduler entirely.
- `pi/src/wr/__fixtures__/mkworld.html`: a real captured snapshot, the parser fixture.
- `node-html-parser` added to `pi/package.json` dependencies.

## Data shapes

```ts
// pi/src/wr/parse.ts
export type ScrapedWr = {
  courseName: string;   // verbatim from mkwrs, e.g. "Wario Shipyard"
  recordMs: number;     // parsed from "1'47\"414"
  recordStr: string;    // canonical "1:47.414"
  holder: string | null;
  date: string | null;  // "YYYY-MM-DD" as shown
  character: string | null;
  vehicle: string | null;
  videoUrl: string | null;
};

// pi/src/wr/reconcile.ts
export type WrReport = {
  inserted: number;     // a new current row was appended (faster, or a record we had no history row for)
  reflagged: number;    // current moved onto an existing history row (a revert / DQ)
  backfilled: number;   // metadata enriched on the current row (video/character/vehicle)
  unchanged: number;    // scraped row already equals current; nothing written
  unmapped: string[];   // scraped course names with no canonical course (logged)
};
```

## Schema change

Add a current-WR marker to `world_records` (the only schema change):

- `server/schema.sql`: add `is_current INTEGER NOT NULL DEFAULT 0` to the
  `world_records` column list (fresh DBs get it). Do **not** add the partial index here
  (see below).
- `pi/src/db/connect.ts:applySchema`, after the existing `color` migration, in its own
  try/catch (runs once, when the column is first added to a pre-existing DB):
  ```sql
  ALTER TABLE world_records ADD COLUMN is_current INTEGER NOT NULL DEFAULT 0;
  -- one-time seed: flag the latest-achieved row per (course,cc) as current
  UPDATE world_records SET is_current = 1 WHERE id = (
    SELECT w2.id FROM world_records w2
    WHERE w2.course_id = world_records.course_id AND w2.cc = world_records.cc
    ORDER BY w2.achieved_at DESC, w2.id DESC LIMIT 1);
  ```
  This makes `currentWr` correct for the existing `pi/mkw.db` immediately, before any
  scrape (latest-achieved is the best pre-scrape heuristic; the first scrape corrects
  any drift).
- Then, unconditionally (idempotent, after the migration block so the column is
  guaranteed present for both fresh and migrated DBs):
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS idx_wr_current
    ON world_records(course_id, cc) WHERE is_current = 1;
  ```
  This partial unique index enforces **at most one current WR per (course, cc)**. It
  lives in `connect.ts` rather than `schema.sql` because `applySchema` execs the whole
  `schema.sql` blob first; on a pre-existing DB that runs before the `ALTER` adds the
  column, so a `WHERE is_current=1` index in `schema.sql` would reference a missing
  column and throw. (No Python importer change: a fresh import is seeded by the boot
  scrape, and the all-zero `is_current` state is valid under the partial index.)

## Reconcile / dedup logic (mirror the table)

For each `ScrapedWr`, resolve `course_id` via `resolveCourseId`. If null, push the name
to `unmapped` (warn) and continue. Otherwise read the current WR:

```sql
SELECT id, holder_name, record_ms, record_str, video_url, character, vehicle
FROM world_records WHERE course_id = ? AND cc = ? AND is_current = 1;
```

Let `cur` be that row (or none). Each case runs in its own transaction; the previous
current is always cleared before a new one is set, so the partial unique index never
sees two current rows. Per-row errors are caught, logged, and excluded from the batch
without aborting it.

1. **Same record** as `cur` (`scraped.recordMs === cur.recordMs` and
   `scraped.holder === cur.holder_name`) -> **BACKFILL** in place, no current move:
   - `holder_name`: set only if currently NULL (never overwrite a record's identity).
   - `video_url`, `character`, `vehicle`: `UPDATE` when the scraped value is non-empty
     **and differs** from what is stored (the "video / metadata added or corrected
     later" case - a regular occurrence on mkwrs).
   - `provenance` is left unchanged (it records origin, not last-touched).
   - If a field changed -> `backfilled++`; else `unchanged++`. No event.

2. **Different record** (different `record_ms`, or different holder - whether faster,
   slower, or a tie by a new holder) -> the current WR **changed**. Mirror it:
   - Clear `is_current` on `cur` (if any).
   - Look for an existing history row matching `(course_id, cc, record_ms, holder)`
     (highest `id` if several):
     - **found** (a revert / DQ back to a prior record) -> set that row
       `is_current = 1`, backfill its metadata from the scrape -> `reflagged++`.
     - **not found** -> INSERT a new row (`provenance='scraped'`, `is_current=1`,
       `achieved_at = ${date}T00:00:00.000Z` or `now()` if the date is missing) ->
       `inserted++`.
   - Emit a `wr_update` event **only when `cur` was non-null** (a prior current
     existed), with `prev_*` from `cur` and the new values; `improvement_ms =
     cur.record_ms - scraped.recordMs` (positive = faster, negative = reverted to a
     slower record). When `cur` is null the current is established silently - this is
     the steady state on the first scrape of a freshly imported DB (every course's
     current is unset until then), and a 30-event burst on boot/cutover is not wanted.
     A genuine new WR always has a prior current, so it still emits.

3. **Course absent** from the scrape (not in `scraped[]`) -> do nothing. We never clear
   a current WR for a course we simply did not see this run (guards against a flaky or
   partial page wiping a record). Only rows present on the page are acted on.

**Idempotency:** insert/reflag only happen when the scraped current differs from `cur`;
backfill only writes a field that actually differs. Re-running on an unchanged page
performs no writes.

## New event type

Add to the `ServerEvent` union in `pi/src/db/types.ts`, distinct from the existing
player-side `wr_beaten`:

```ts
| { type: 'wr_update'; course: string; cc: number; holder: string | null;
    total_time: string; prev_holder: string | null; prev_time: string | null;
    improvement_ms: number | null; character: string | null;
    vehicle: string | null; video_url: string | null }
```

`course` is the verbatim mkwrs name (consistent with other events carrying the display
name). `total_time` is the canonical `record_str`. `improvement_ms` is positive for a
faster record and negative for a revert, so C can render direction. Fires only when the
current WR changes *and a prior current existed* (case 1's backfill, "unchanged", and
the silent first-scrape establishment of current are all silent). `prev_*` are
therefore always non-null on an emitted event.

## `currentWr` read change

`pi/src/db/reads.ts:currentWr` changes from `ORDER BY achieved_at DESC, id DESC` to
`WHERE ... AND is_current = 1 LIMIT 1`. This is the explicit, unambiguous current
(immune to date-format drift and correct after a revert, where the current row is not
the fastest). The existing single-row `reads.test.ts` seed must set `is_current=1` on
its WR insert (otherwise the new `WHERE is_current=1` read returns nothing); the
`record_ms === 100000` assertion then stays green. Add a multi-row case where a
*slower* row is `is_current` and assert `currentWr` returns it (proving it is not
"fastest wins"). `runs.ts` and `GET /v1/world-records` are unaffected behaviourally.

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `MKWRS_URL` | `https://mkwrs.com/mkworld/` | Page to scrape. |
| `MKWRS_INTERVAL_SEC` | `300` | In-process scrape interval; `0` disables the in-process scheduler. |
| `MKW_DB` | `mkw.db` | (existing) DB path, used by the CLI and server alike. |

cc is fixed at 150 (MKW is 150-only); a `--cc` CLI flag may default it but is not
surfaced as config.

## Testing (TDD, vitest, all offline)

- `time.test.ts`: `1'47"414`->107414, `59"123`->59123, 1- and 2-digit ms normalization,
  round-trip `msToTimeStr`, throws on garbage.
- `parse.test.ts`: against `__fixtures__/mkworld.html` - expected row count, correct
  field extraction for a known track, video URL pulled from the time link, `(Glitch)`
  rows excluded, header/short rows excluded, time parsed to ms.
- `courses.test.ts`: all 30 canonical courses resolve from their mkwrs names
  (completeness test - catches a future mkwrs rename), "Wario Shipyard" ->
  `warios_galleon`, `(Glitch)` and unknown names -> null.
- `reconcile.test.ts` (in-memory `node:sqlite`, schema + `is_current` index applied,
  seeded course + current WR):
  - insert + current move + `wr_update` (positive improvement) when strictly faster;
  - no prior current (`cur` null): current is established (insert, or reflag a matching
    history row) with **no event** emitted;
  - **revert**: a *slower* scraped time matching an existing history row -> that row is
    reflagged current, **no new row**, `wr_update` with negative `improvement_ms`;
  - holder/time change to a record not in history -> new row inserted + current move;
  - backfill adds a missing video; backfill updates a *changed* video; fills
    character/vehicle; does NOT overwrite a non-null holder; no event on backfill;
  - unchanged: scraped equals current -> no writes (idempotency: batch run twice);
  - a course absent from the batch keeps its current WR (no clear);
  - unmapped course name recorded in `WrReport.unmapped`, not thrown;
  - invariant: exactly one `is_current` row per `(course, cc)` after reconcile (the
    partial unique index would reject a second).
- `connect.test.ts` (migration): build a pre-`is_current` DB (create `world_records`
  without the column - a fresh `applySchema` DB already has it - and insert several WRs
  per course), then run `applySchema`; assert it flags exactly one current per course
  (the latest-achieved) and that the partial unique index exists and rejects a second
  current.
- `reads.test.ts`: `currentWr` returns the `is_current` row, including the slower-is-
  current multi-row case.
- `scrape.test.ts`: `scrapeOnce` with an injected `fetchHtml` returning the fixture
  drives parse->reconcile end to end and returns the expected `WrReport`.

The network `fetch` default is only exercised manually (CLI against the live site);
no test hits the network.

## Out of scope (deferred)

WR reign-duration analytics (who held it how long - derived later in C); multi-cc
(150-only); any website/overlay rendering (C); historical per-WR sub-page scraping
(only the current-WR table is read). Reverts/DQs *are* handled (the current WR mirrors
the table), but reign bookkeeping around them is not.

## Acceptance criteria

- `npm test` in `pi/` green, including the new WR test files, the migration test, and
  the updated `currentWr` test.
- `npm run scrape-wr` against `pi/mkw.db` runs, prints a `WrReport`, and is idempotent
  (a second immediate run reports `inserted: 0, reflagged: 0` and only real
  same-day metadata backfills, if any).
- Starting the server with `MKWRS_INTERVAL_SEC` unset performs an initial scrape and
  schedules subsequent ones; a scrape failure logs without crashing the server.
- A genuinely faster WR on mkwrs -> exactly one new `provenance='scraped'`,
  `is_current=1` row (the prior current cleared) + one `wr_update` (positive). A
  removal/DQ that reverts the page to a slower prior record -> our current flips to
  that record (reusing its history row, no duplicate) + one `wr_update` (negative). A
  later-added video on the unchanged current WR -> in-place backfill, no new row, no
  event.
- Exactly one `is_current` row per `(course, cc)` at all times (partial unique index).
