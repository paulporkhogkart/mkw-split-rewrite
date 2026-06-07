# WR Scraper — Design

Date: 2026-06-07
Sub-project: B/C boundary (the server's WR ingestion job; consumed later by C)
Status: approved, ready for implementation plan

## Goal

A server-side job on the pi that periodically reads the global Mario Kart World
world-record table from mkwrs.com, detects genuinely new records, appends them to
the canonical `world_records` table (append-only history), enriches existing rows
when mkwrs adds metadata later (videos, character, vehicle), and emits a live event
so sub-project C (broadcast/website) can react.

This is a fresh TypeScript implementation co-located with the pi server that owns the
DB. The legacy `legacy/mkwpb2/kart-off/services/mkwrs_monitor.py` is a reference for
the page structure and reconcile semantics, not a port target.

## Context (what already exists)

- Source: `https://mkwrs.com/mkworld/`. A `table.wr` lists one current-WR row per
  track. Columns (verified live, matches legacy indices): `[0]` track name (linked),
  `[1]` time + video link, `[2]` player (linked), `[3]` nation flag, `[4]` date
  (`YYYY-MM-DD`), `[5]` duration, `[6]` character, `[7]` vehicle, `[8]` splits icon.
  Time format is `M'SS"mmm` (e.g. `1'47"414`); ms can be 1–3 digits. The page also
  lists glitch categories ("Mario Bros. Circuit (Glitch)", "Crown City (Glitch)")
  that are NOT canonical courses and must be excluded. No cc selector exists; the
  page is the standard (150cc) records.
- Course names on mkwrs match the canonical display names except **"Wario Shipyard"**
  (mkwrs) vs **"Wario's Galleon"** / `warios_galleon` (canonical) — the same single
  alias the Python importer carries in `server/courses.py:LEGACY_ALIASES`.
- The pi server (`pi/`): TypeScript + Hono + `node:sqlite`, shares `server/schema.sql`
  as the single schema source. Node 22 (global `fetch`, `node:sqlite`). Minimal-dep
  ethos (no axios; `node:sqlite` not better-sqlite3).
- `server/schema.sql` already defines `world_records(id, course_id, cc, holder_name,
  record_ms, record_str, achieved_at, video_url, character, vehicle, provenance
  DEFAULT 'legacy_import', created_at)` with `idx_wr_course(course_id, cc, achieved_at)`.
  `world_records.provenance` has **no** CHECK constraint, so `'scraped'` is a valid value.
- `pi/mkw.db` currently holds 30 courses + 473 WRs, all `provenance='legacy_import'`.
  WRs are GLOBAL and not season-scoped.
- `pi/src/db/reads.ts:currentWr(db, courseId, cc)` reads the current WR; consumed by
  `runs.ts` (gap-to-WR / `wr_beaten`), the `GET /v1/world-records` route, and one test.
- `pi/src/db/slug.ts:slugify` already does lowercase + apostrophe-drop + non-alnum→`_`.
- Standalone-script pattern: `pi/src/scripts/*.ts` run via
  `node --no-warnings --import tsx ...`, registered in `package.json` scripts.
- Event plumbing: `pi/src/api/events.ts:EventHub` (`publish`/`subscribe`); the
  `/v1/events` WS forwards every published `ServerEvent` (`pi/src/db/types.ts`).

## Decisions (from brainstorming)

- **Language/runtime:** TypeScript in `pi/` (the prompt scoped it to "the pi
  serverside"; co-located with the DB-owning server; one typed stack).
- **Run model:** in-process scheduler started by `server.ts` (so a new WR can
  broadcast live), with the same pure logic also exposed as a one-shot
  `npm run scrape-wr` CLI for tests and manual/cron runs.
- **HTML parser:** `node-html-parser` (tiny, zero transitive deps, pure JS; robust
  enough for this regular table; fits the repo's minimal-dep ethos).

## Module layout (new `pi/src/wr/`)

Mechanism (pure, network-free, unit-tested) is separated from policy (when to run):

| File | Responsibility |
|------|----------------|
| `time.ts` | `mkwrsTimeToMs("1'47\"414"): number` and `msToTimeStr(107414): "1:47.414"`. Normalizes 1–3 digit ms to 3 digits. Throws on unparseable input. Pure. |
| `courses.ts` | `MKWRS_ALIASES = { "Wario Shipyard": "warios_galleon" }`; `mkwrsNameToSlug(name): string` (alias → else `slugify`). `resolveCourseId(db, name): number \| null` (null for `(Glitch)` and any name with no canonical course). Reuses `db/slug.ts`. |
| `parse.ts` | `parseWrTable(html: string): ScrapedWr[]` using `node-html-parser`. Selects `table.wr` rows, extracts fields by column, drops the header and any row with `< 9` cells, drops `(Glitch)` rows. Pure. |
| `reconcile.ts` | `reconcile(db, hub, scraped: ScrapedWr[]): WrReport`. Per row: resolve course, compare to current WR, INSERT / BACKFILL / skip (see below). |
| `scrape.ts` | `scrapeOnce(db, hub, opts): Promise<WrReport>` orchestrator: fetch → parse → reconcile. `opts.fetchHtml?: () => Promise<string>` is injectable (default = global `fetch(url, { headers: { 'User-Agent': ... }, signal: AbortSignal.timeout(30_000) })`); `opts.url` (default `https://mkwrs.com/mkworld/`); `opts.cc` (default 150). |
| `scheduler.ts` | `startWrScraper(db, hub, { url, intervalSec }): () => void`. Runs one scrape immediately (async, non-blocking), then every `intervalSec`. Each tick wrapped in try/catch (a failure logs and is swallowed — never throws, never takes down the server). A re-entrancy guard skips a tick if the previous one is still running. Returns a stop function (clears the interval). |

Plus:
- `pi/src/scripts/scrapeWr.ts`: one-shot CLI. Opens the DB (`MKW_DB`), `applySchema`,
  runs `scrapeOnce` with a no-op hub, prints the `WrReport`, exits non-zero on a
  hard failure (network/parse). Registered as `"scrape-wr"` in `package.json`.
- `pi/src/server.ts`: calls `startWrScraper(db, hub, { url: process.env.MKWRS_URL,
  intervalSec: Number(process.env.MKWRS_INTERVAL_SEC ?? 300) })` after the WS is
  injected. `MKWRS_INTERVAL_SEC=0` disables the in-process scheduler entirely.
- `pi/src/wr/__fixtures__/mkworld.html`: a real captured snapshot of the live page,
  checked in as the parser test fixture.
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
  inserted: number;     // new records appended
  backfilled: number;   // existing current-WR rows enriched
  skipped: number;      // already-current, nothing to change
  unmapped: string[];   // scraped course names with no canonical course (logged)
};
```

## Reconcile / dedup logic (the core)

For each `ScrapedWr`, resolve `course_id` via `resolveCourseId`. If null, push the
name to `unmapped` (warn) and continue. Otherwise fetch the current WR:

```sql
SELECT id, holder_name, record_ms, record_str, video_url, character, vehicle
FROM world_records WHERE course_id = ? AND cc = ?
ORDER BY record_ms ASC LIMIT 1
```

(The fastest row *is* the current WR in an append-only log; ordering by `record_ms`
is immune to the date-format difference between imported datetimes and scraped dates.)

Then:

1. **No current, or `scraped.recordMs < current.recordMs`** (strictly faster) →
   **INSERT** a new row: `provenance='scraped'`, `cc` = opts.cc,
   `achieved_at` = `${date}T00:00:00.000Z` (or `new Date().toISOString()` if the date
   is missing/unparseable), all metadata from the scrape. Then emit a `wr_update`
   event (with `prev_holder`/`prev_time`/`improvement_ms` from `current`, or nulls
   when there was no prior WR). `inserted++`.

2. **`scraped.recordMs === current.recordMs`** (same record — the normal steady state,
   since the page lists one current-WR row per track) → **BACKFILL** the current row,
   guarded by `scraped.holder === current.holder_name || current.holder_name == null`
   (so metadata from a same-time *tie by a different holder* never bleeds onto our row):
   - `holder_name`: set only if currently NULL (never overwrite a record's identity).
   - `video_url`, `character`, `vehicle`: `UPDATE` whenever the scraped value is
     non-empty **and differs** from what is stored (this is the "video / metadata
     added or corrected later" case — a regular occurrence on mkwrs).
   - `provenance` is left unchanged (it records origin, not last-touched; an enriched
     `legacy_import` row stays `legacy_import`).
   - No event emitted (a late-added video is not a broadcast moment; matches legacy).
   - If at least one field changed, `backfilled++`; else `skipped++`.

3. **`scraped.recordMs > current.recordMs`** (page shows a slower time than we hold —
   only on a DQ/record removal, out of scope) → skip entirely, `skipped++`. Do NOT
   backfill (it is a different record than ours).

**Idempotency:** insert only happens when strictly faster than what we hold, and
backfill only writes when a field actually differs. Re-running on an unchanged page
therefore performs no writes.

Each INSERT and each BACKFILL is wrapped in its own transaction so one bad row cannot
abort the batch; per-row errors are caught, logged, and counted as skips.

## New event type

Add to the `ServerEvent` union in `pi/src/db/types.ts`, distinct from the existing
player-side `wr_beaten`:

```ts
| { type: 'wr_update'; course: string; cc: number; holder: string | null;
    total_time: string; prev_holder: string | null; prev_time: string | null;
    improvement_ms: number | null; character: string | null;
    vehicle: string | null; video_url: string | null }
```

`course` is the verbatim mkwrs name (consistent with other events carrying the
display name). `total_time` is the canonical `record_str`. `improvement_ms` is
`current.record_ms - scraped.recordMs` (positive) when there was a prior WR, else null.

## One existing-code change

`pi/src/db/reads.ts:currentWr` changes its ordering from `achieved_at DESC, id DESC`
to `record_ms ASC LIMIT 1`. Reason: scraped rows carry date-only `achieved_at` that
can mis-sort against imported datetime values; the fastest row is unambiguously the
current WR in an append-only log. The existing `reads.test.ts` "currentWr returns the
latest WR" assertion (single-row seed, `record_ms === 100000`) stays green unchanged;
add a multi-row case that proves the fastest row wins regardless of `achieved_at`
order. `runs.ts` and `/v1/world-records` are behaviourally unaffected for clean
monotonic data.

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `MKWRS_URL` | `https://mkwrs.com/mkworld/` | Page to scrape. |
| `MKWRS_INTERVAL_SEC` | `300` | In-process scrape interval; `0` disables the in-process scheduler. |
| `MKW_DB` | `mkw.db` | (existing) DB path, used by the CLI and server alike. |

cc is fixed at 150 (MKW is 150-only); a `--cc` CLI flag may default it but is not
surfaced as config.

## Testing (TDD, vitest, all offline)

- `time.test.ts`: `1'47"414`→107414, `59"123`→59123, 1- and 2-digit ms normalization,
  round-trip `msToTimeStr`, throws on garbage.
- `parse.test.ts`: against `__fixtures__/mkworld.html` — expected row count, correct
  field extraction for a known track, video URL pulled from the time link, `(Glitch)`
  rows excluded, header/short rows excluded, time parsed to ms.
- `courses.test.ts`: all 30 canonical courses resolve from their mkwrs names
  (completeness test — catches a future mkwrs rename), "Wario Shipyard" → `warios_galleon`,
  `(Glitch)` and unknown names → null.
- `reconcile.test.ts` (in-memory `node:sqlite`, schema applied, seeded course + WR):
  insert when strictly faster (+ `wr_update` emitted with correct prev/improvement);
  insert when no prior WR (prev nulls); backfill adds a missing video; backfill updates
  a *changed* video; backfill fills character/vehicle; backfill does NOT overwrite a
  non-null holder; no backfill when times differ; backfill does not cross holders on a
  tie; running the same batch twice performs no second write (idempotency); unmapped
  course name recorded, not thrown.
- `scrape.test.ts`: `scrapeOnce` with an injected `fetchHtml` returning the fixture
  drives parse→reconcile end to end and returns the expected `WrReport`.

The network `fetch` default is only exercised manually (CLI against the live site);
no test hits the network.

## Out of scope (deferred)

WR reign/duration tracking; WR removal/DQ rollback handling; multi-cc (150-only);
any website/overlay rendering (sub-project C); historical per-WR sub-page scraping
(only the current-WR table is read).

## Acceptance criteria

- `npm test` in `pi/` green, including the new WR test files and the updated
  `currentWr` test.
- `npm run scrape-wr` against `pi/mkw.db` runs, prints a `WrReport`, and is idempotent
  (a second immediate run reports `inserted: 0` and only the day's real metadata
  backfills, if any).
- Starting the server with `MKWRS_INTERVAL_SEC` unset performs an initial scrape and
  schedules subsequent ones; a scrape failure logs without crashing the server.
- A genuinely faster WR appearing on mkwrs results in exactly one new
  `provenance='scraped'` row and one `wr_update` event; a later-added video updates the
  existing row in place with no new row and no event.
