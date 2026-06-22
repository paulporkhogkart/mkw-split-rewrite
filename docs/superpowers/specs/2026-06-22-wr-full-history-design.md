# WR Full-History Capture — Design

**Date:** 2026-06-22
**Status:** Approved (data layer); territory/heat consumption is a separate follow-up.

## 1. Goal & context

Today the WR scraper (`pi/src/wr/`) loads only the `mkworld/` **main page** every 15–30 min and
makes `world_records` mirror the *current* WR per `(course_id, cc)`. We want the **full WR
history** for every course — accurately, with all per-lap splits, coins, mushrooms,
character/costume/kart, nation, date, and video link — captured from each course's
`display.php` history page, plus an ongoing low-volume verification pass.

This data powers (in a later spec) the territory/heat history playback (the actual WR holder
per historical date) and "which character/kart wins which course" analysis. **This spec is the
data layer only**: scrape → normalize → store → flag mismatches → verify. Consumption is out of
scope (§10). mkwrs is the **single source of truth** — nothing in `world_records` predates its
per-course history, so each per-track page is authoritative for that course's entire WR history
(this is what makes the §7 mirror semantics safe).

User decisions locked in brainstorming:
- **Scope:** data layer now; territory consumption is a follow-up spec.
- **Name review:** auto-resolve via slug + a user-editable alias map; unresolved names go to a
  flags table surfaced by a CLI report.
- **Storage:** extend `world_records` in place (no sibling table).

## 2. Source structure (verified against all 30 live pages)

Each `display.php?track=…` page has two `table.wr` tables: the first is the single current WR,
the **last** is the full history (84–140 rows each; ~3,200 rows total). Both share a per-track
layout. Enumerating all 30 pages gives exactly **4 structural variants**, distinguished purely
by the header row:

| Variant | Tracks | Header (`<th>`) | Row shape |
|---|---|---|---|
| **Flat, 3 laps** | 27 | `Date Time Player Nation Days Lap1 Lap2 Lap3 Coins Shrooms Character Kart` | 1 `<tr>` / record |
| **Stacked, 4 laps** | Rainbow Road | `… Lap1 Lap2 Lap3 Lap4 │ Coins & Shrooms │ Combination(colspan=2)` | 2 `<tr>` / record |
| **Stacked, 5 laps** | Koopa Troopa Beach | `… Lap1…Lap5 │ Coins & Shrooms │ Combination` | 2 `<tr>` / record |
| **Stacked, 6 laps** | DK Spaceport | `… Lap1…Lap6 │ Coins & Shrooms │ Combination` | 2 `<tr>` / record |

**Stacked layout** (≥4 laps): the common columns + laps are `rowspan=2`; **coins + character**
are in row 1's trailing cells, **shrooms + kart** in row 2 — under the merged `Coins & Shrooms`
and `Combination` (colspan=2) headers. Example (Rainbow Road, 4 laps):

```html
<tr>
  <td rowspan=2>2026-05-21</td> … <td rowspan=2>1:13.164</td>  <!-- date…lap4 -->
  <td>8-12-0-0</td> <td>Wiggler</td>                            <!-- coins, character -->
</tr>
<tr> <td>0-1-1-1</td> <td>Big Horn</td> </tr>                   <!-- shrooms, kart -->
```

**Interleaved patch/info rows** (every track, periodically): mkwrs inserts non-record rows into
the history table — `<tr><td>DATE</td><td colspan=13>Version 1.2.0 … <a href="patchinfo.php">More
Info</a></td></tr>`. These MUST be skipped. The tell is a body `<td>` with a `colspan` attribute.
(In stacked mode a 2-cell patch row otherwise collides with the "continuation row = 2 cells"
heuristic — so rows are classified by **attributes**, not cell count.)

### Per-cell formats (all observed)

- **Date:** `YYYY-MM-DD` (UTC day), or `<span title="Time set before 2025-06-05 00:00 UTC">Pre-release</span>`.
- **Time:** `1'47"414` either as plain text (no video) or wrapped in `<a href=…>` (video:
  `youtube.com/watch`, `youtube.com/live`, `youtu.be`, possibly with `?si=`/`&t=` params).
- **Player:** display text may be non-ASCII (`あつき`, `キリム`); `<a href="profile.php?player=…">`
  carries the URL-encoded stable key.
- **Nation:** `<img alt="Japan" src="../country-flags/JP.png">` → country code from `src` basename
  (`JP`). May be absent for some players → null.
- **Lap N:** `37.000` (`SS.mmm`) **or** `1:13.164` (`M:SS.mmm` on long tracks) **or** `-` (unknown).
- **Coins / Shrooms:** per-lap `8-0-0` → `[8,0,0]`; multi-digit ok (`8-12-0-0` → `[8,12,0,0]`);
  `-` → null. Part count equals lap count.
- **Character:** `Toadette (Conductor)` = `Character (Costume)`, or bare `Baby Daisy` (base costume).
- **Kart:** plain text (`Mach Rocket`, `R.O.B. H.O.G.`).

## 3. Module layout (all under `pi/src/wr/`)

The existing fast main-page scraper is **unchanged**. New files:

| File | Responsibility |
|---|---|
| `history_parse.ts` | `parseHistory(html) → ScrapedHistoryRow[]` (layout detect + flat/stacked + patch skip) |
| `lap.ts` | `lapTimeToMs` (`SS.mmm` & `M:SS.mmm`), `parsePerLap` (`"8-12-0"`→`[8,12,0]`, `-`→null) |
| `roster.ts` | canonical char/kart/costume slug sets + editable alias maps + `resolveItem()` |
| `flags.ts` | `upsertFlag` / `resolveFlag` / `reportFlags` over `wr_name_flags` |
| `history_reconcile.ts` | mirror one course's scraped history into `world_records` |
| `history_scrape.ts` | `scrapeTrackHistory(track)` + `scrapeAllHistory()` orchestration + polite fetch |
| `history_scheduler.ts` | slow round-robin drip verifier |
| `scripts/scrapeWrHistory.ts` | CLI: `scrape-wr-history [--all\|--track=NAME]`, `wr-flags` |

Reuses existing `time.ts` (`mkwrsTimeToMs`), `courses.ts` (`resolveCourseId`), `db/slug.ts`.

## 4. Schema changes — extend `world_records` (additive, nullable)

Migration follows the existing `db/connect.ts:applySchema` idempotent `ALTER TABLE … ADD COLUMN`
pattern (one try/catch per column, like the `is_current` migration). New columns:

| Column | Type | Meaning |
|---|---|---|
| `nation` | TEXT | country code from flag `src`, e.g. `JP` |
| `character_slug` | TEXT | resolved canonical slug; NULL = unresolved (flagged) |
| `kart_slug` | TEXT | resolved canonical slug; NULL = unresolved |
| `costume_slug` | TEXT | resolved costume slug; NULL = base **or** unresolved (see note) |
| `lap_splits_ms` | TEXT | JSON int array, e.g. `[37000,35263,35151]` (variable length) |
| `coins` | TEXT | JSON int array, e.g. `[8,12,0,0]` |
| `mushrooms` | TEXT | JSON int array, e.g. `[1,1,1]` |
| `date_precision` | TEXT | `day` (default) or `pre_release` |
| `removed_at` | TEXT | set when a row vanishes from the page (DQ'd); NULL = live |
| `source_raw` | TEXT | JSON of verbatim scraped cells (audit / re-reconcile without re-fetch) |

Existing `character`/`vehicle` keep their raw display values (untouched, backward-compatible).
`source_raw` stores `{date, time, player_key, nation, laps[], coins, shrooms, character, kart}`
verbatim so we can re-resolve names after adding an alias **without re-fetching**.

> **Costume NULL ambiguity:** a bare character (no parens) is *base* costume → `costume_slug`
> stays NULL and is **not** flagged. Only a *present-but-unresolved* costume string is flagged.
> Distinguish via `source_raw.character` containing `(…)`.

Per-lap data is JSON columns (not a child table): small, always read with the row, variable
length. The "as-of-date" timeline (future) reads one row → all fields.

**New table** `wr_name_flags`:

```sql
CREATE TABLE IF NOT EXISTS wr_name_flags (
  id INTEGER PRIMARY KEY,
  category TEXT NOT NULL,          -- 'character'|'kart'|'costume'|'course'
  raw_value TEXT NOT NULL,         -- verbatim mkwrs string, e.g. 'R.O.B. H.O.G.'
  slug_guess TEXT,                 -- slugify() result that missed
  example_course_id INTEGER,
  example_wr_id INTEGER,
  occurrences INTEGER NOT NULL DEFAULT 1,
  resolved_at TEXT,                -- auto-set once an alias/roster entry makes it resolve
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(category, raw_value)
);
```

**New table** `wr_meta` (or reuse an existing key/value store) — one row holding the drip's
round-robin cursor (`history_cursor` = index into the 30-track list).

## 5. Parser (`history_parse.ts`)

```
parseHistory(html):
  tables = querySelectorAll('table.wr');  hist = the one with the most <tr> (the history table)
  ths    = hist header <th> texts
  N      = count of /^Lap \d+$/ headers
  stacked = ths include 'Coins & Shrooms' or 'Combination'
  rows   = hist <tr> after the header
  for each row:
    tds = row <td>
    if any td has a `colspan` attribute → patch/info row → skip
    if stacked:
      if td[0] has rowspan → PRIMARY: parse common[0..4], laps[5..4+N], coins=td[5+N], character=td[6+N]
                             continuation = next non-patch <tr> (2 cells): shrooms=td[0], kart=td[1]
      else → already consumed as a continuation → skip
    else (flat):
      parse common[0..4], laps[5..4+N], coins=td[5+N], shrooms=td[6+N], character=td[7+N], kart=td[8+N]
```

Cell helpers: `parseDate` (ISO or pre-release sentinel + precision), `parseTimeCell`
(`{ms, videoUrl}` via `<a>` href or text), `parsePlayer` (`{name, key}`), `parseNation`
(country code from flag `src`), `lapTimeToMs`, `parsePerLap`, `splitCharacter`
(`"Char (Costume)"` → `{character, costume|null}` splitting on the **last** `(...)`).

`ScrapedHistoryRow = { recordMs, recordStr, dateIso|null, datePrecision, holderName, holderKey,
nation|null, lapSplitsMs:(number|null)[], coins:number[]|null, mushrooms:number[]|null,
characterRaw, costumeRaw|null, kartRaw, videoUrl|null }`.

## 6. Name reconciliation (`roster.ts` + `flags.ts`)

- Canonical slug sets for characters/karts/costumes are **baked into `roster.ts`** (committed; a
  small generator script reads `captures/<lang>/<category>/*.png` basenames so the Pi needs no
  capture files at runtime). Courses reuse `courses.ts`.
- `resolveItem(category, raw, ctx)`: `slug = slugify(raw)`; if `slug ∈ canonical` → slug; else if
  `slug ∈ ALIASES[category]` → mapped slug; else → `null` + `upsertFlag(category, raw, slug, ctx)`.
- `wr-flags` CLI prints unresolved flags (`resolved_at IS NULL`) grouped by category with
  occurrences + an example course/row. The user adds an alias (or extends the canonical roster),
  re-runs the scrape (or a `--reresolve` pass over `source_raw`), and the flag auto-resolves.

**Verified flag volume (audit over all 30 pages):** characters 50/50 and costumes 32/32 resolve
cleanly; all karts resolve once **three aliases** are seeded. mkwrs uses MK8-era display names for
a few returning karts, whereas our roster uses the in-game MKWorld names:

```
KART_ALIASES = {                  // keyed by slugify(raw)
  'r_o_b_h_o_g': 'rob_hog',       // 'R.O.B. H.O.G.'
  'biddybuggy':  'buggybud',      // 'Biddybuggy'  -> MKWorld name Buggybud
  'tiny_titan':  'rally_romper',  // 'Tiny Titan'  -> MKWorld name Rally Romper
}
```

With these seeded, the first real run is **flag-clean** (0 unresolved across all categories). The
`wr_name_flags` table + `wr-flags` CLI are then reserved for genuinely new/unseen names that
appear in future scrapes (e.g. another returning kart/costume with an MK8-era mkwrs name).

## 7. Reconcile / mirror semantics (`history_reconcile.ts`)

The history page is ground truth for a course. For each scraped row (oldest→newest):

- **Natural key** = `(course_id, cc, record_ms, holder_name)`. (Each WR is strictly faster than
  the previous; ties — see mkwrs `tiedwrs.php` — are distinguished by holder.)
- **Exists** → *enrich*: fill `nation`, `*_slug`, `lap_splits_ms`, `coins`, `mushrooms`,
  `video_url`, `date_precision`, `source_raw`, and raw `character`/`vehicle` where null or
  changed; never clobber a non-null canonical value unless the page differs.
- **New** → insert with `provenance='scraped_history'`, `is_current=0`.
- **is_current:** set on the row matching the page's current WR (top of the first table / newest
  history row), 0 on others for that course — stays consistent with the main-page scraper (same
  source, so they agree).
- **Removal / DQ:** mkwrs is the **single source of truth** and nothing in `world_records`
  predates its per-course history, so the page is authoritative for the course's *entire* history
  across **all provenances**. Any existing WR row for the scraped course **not** present in the
  fresh scrape → set `removed_at = now` (soft delete: kept for audit, excluded from canonical
  reads), regardless of provenance. Existing `legacy_import`/`scraped` rows that DO match a page
  row are matched by natural key and enriched in place (never duplicated).

`reconcileHistory` returns `{ course, inserted, enriched, unchanged, removed, flagged }`.

## 8. Scraping orchestration & cadence

Two triggers, both polite (browser-like UA, `Referer: https://mkwrs.com/mkworld/`, sequential —
never parallel, per-request `AbortController` + `clearTimeout` timeout per the Windows-safe
pattern, non-200 → back off):

- **Bulk backfill CLI** `npm run scrape-wr-history --all`: iterate all 30 tracks with randomized
  20–60 s gaps (≈15–25 min). Prints a per-track report (inserted / enriched / removed / flagged)
  and a totals line. `--track=NAME` does one track.
- **Slow drip verifier** (in-process, opt-in): every long random interval
  (`MKWRS_HISTORY_MIN/MAX_INTERVAL_SEC`, default ~2–6 h, re-rolled each cycle) scrape **one**
  track, round-robin via the persisted `wr_meta.history_cursor`. Over a few days it continuously
  re-verifies all 30 at near-zero request volume — catching DQs/backfills. `MKWRS_HISTORY_ENABLED=0`
  disables. Reuses the existing scheduler's jittered-`setTimeout` shape. Wired from `server.ts`
  alongside the existing `startWrScraper`.

The fast main-page scraper stays the authority for *current* WRs (frequent); the history layer
owns the rich fields (incl. the current row's, enriched within hours by the drip).

## 9. Testing

- **Fixtures** in `pi/src/wr/__fixtures__/history/` — the saved real pages, trimmed to the
  history-table region, covering every variant + edge case:
  - `mario_bros_circuit.html` — flat 3-lap; pre-release row, non-ASCII players, a `-` missing-data
    row, plain-text (no-video) time.
  - `mario_circuit.html` — flat 3-lap **with an interleaved patch row** (colspan skip).
  - `rainbow_road.html` — stacked 4-lap; `M:SS.mmm` lap, multi-digit coins.
  - `koopa_troopa_beach.html` — stacked 5-lap.
  - `dk_spaceport.html` — stacked 6-lap (max), patch row in stacked context.
- **Parser tests:** assert exact extraction for each fixture — record counts, the layout/lap
  detection, every cell parser, patch-row skip, pre-release precision, null handling.
- **lap/perLap tests:** `SS.mmm`, `M:SS.mmm`, `-`, multi-digit, mismatched part-count guard.
- **Resolution tests:** slug hit, alias hit (R.O.B. → rob_hog), unresolved → flag, flag
  auto-resolve after alias added, base-costume (bare name) not flagged.
- **Reconcile tests** (in-memory DB): insert / enrich-in-place / dedup by natural key / removal
  marks `removed_at` / `is_current` move / legacy_import untouched.
- **e2e dry-run** against a **copy** of the real `pi/mkw.db`: full `--all` parse+reconcile, assert
  no exceptions, sane totals, `wr_name_flags` has **0 unresolved** (3 kart aliases seeded), a
  **small `removed_at` count** (existing legacy/scraped rows match + enrich rather than mass
  remove + reinsert — guards against holder-name drift), and invariant `currents = 30`.

## 10. Out of scope (explicit follow-up)

Territory/heat **consumption** of historical WRs (rendering the actual WR per snapshot date;
character/kart-per-course analysis) is a separate spec. This design only *shapes* the data for it:
every history row carries `achieved_at` + `date_precision`, so a later "WR as-of date" query needs
no re-scrape. Capturing patch-info rows as data (a `wr_patches` table) is a possible future
enhancement; here they are simply skipped.

## 11. Risks & mitigations

- **mkwrs layout drift** → parsing keyed on header signature + cell attributes, not fixed indices;
  a page that yields 0 records or an unknown header logs + is skipped (never corrupts the DB).
- **Anti-bot blocking** → randomized polite spacing, browser UA + referer, sequential, back-off;
  drip is ~one page per several hours. Dev-box backfill uses a different IP than the Pi.
- **Name roster gaps** → flags table + CLI; nothing is silently mis-mapped (unresolved → NULL).
- **Over-aggressive removal** → safe because mkwrs is the single source of truth (nothing predates
  it); removal is **soft** (`removed_at`, excluded from reads, never hard-deleted) and scoped to
  the just-scraped course. The e2e dry-run asserts a small removed count so holder-name drift
  (which would cause spurious remove + reinsert) is caught.
