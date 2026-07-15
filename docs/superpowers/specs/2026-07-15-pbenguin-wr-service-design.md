# pbenguin WR Service — design

**Date:** 2026-07-15
**Status:** approved (design); implementation plan pending

Automatically turn each new mkwrs world record into a minimap trail, without Paul touching a
game menu. The Pi hands out leased jobs; a background tray service on one or more PCs downloads
the WR's YouTube video, replays it through pbenguin's existing detection engine with the scraped
loadout injected, verifies the result against the scraped time, and uploads the trail. pbenguin
clients then draw WR dots on the minimap alongside player trails.

---

## 1. Feasibility: proven, not assumed

A spike on 2026-07-15 ran the real pipeline against the then-current Mario Circuit WR
(JaK, 1'02"934, `youtube.com/watch?v=blldIdJg7zo`), downloaded at 1080p60 H.264 and fed to the
engine with the scraped loadout injected over IPC.

| Measure | Result |
|---|---|
| Detected total | `1:02.934` — exact match to mkwrs `1'02"934` |
| Detected splits | `18.213 / 20.335 / 24.386` — exact match to mkwrs Section 1/2/3 |
| Minimap lock | 844 `tracking` / 11 `ring_only` (98.7% confident) |
| Badge NCC score | median 0.797, max 0.993, min 0.656 |
| Auto-calibration | 0.715 from median 0.810 (default 0.65 was already sufficient) |
| Trail | 1732 points, t = 14…63129 ms, traces the circuit cleanly |

Load-bearing findings:

- **The minimap is character-agnostic.** `MinimapTracker.seed()` builds the badge template *from
  the seed frame at runtime* (`tracker.py:174`), not from a stored per-character asset. A
  stranger's Toadette (Explorer) locks at 0.797 median against an uncalibrated 0.65 default.
- **YouTube capture is geometrically identical to Paul's OBS feed.** The stored Mario Circuit seed
  `(1636, 876)` landed pixel-exact on JaK's badge. No per-video alignment is needed.
- **The engine needs no changes for v1.** Two existing IPC commands do the whole job.
- **The only obstacle is `UNKNOWN_RACE_ACTIVE`** (see §2).

### 1.1 The `UNKNOWN_RACE_ACTIVE` obstacle and its bypass

WR uploads cut straight from a menu into the race countdown, skipping the loading screen. The
detector correctly classifies "race HUD appeared from nowhere" as `UNKNOWN_RACE_ACTIVE`, which is
a deliberate guardrail: `TRANSITIONS[UNKNOWN_RACE_ACTIVE]` (`screen.py:198`) does **not** include
`RACING`, and `_RACE_TYPE_RESOLUTION` (`screen.py:621`) only resolves it *retroactively on exit*
via `RACE_MENU` / `REPLAY_MENU` / `POST_TIME_TRIAL`. A WR video exits to `RESET`, which isn't in
that map, so it never resolves and **every tracker stays gated off for the whole race**. The
first spike run produced 0 minimap updates for exactly this reason.

The guardrail exists because a mid-join race has "missing early laps + minimap" and "can never be
a valid PB" (`race.py:178`). For a WR video that reasoning inverts: the loadout is known from the
scrape, and the countdown at `0:00.000` proves the video contains the whole race.

**Do not force `RACING` directly.** `force_screen(RACING)` from `UNKNOWN_RACE_ACTIVE` hits
`race.py:182`, which calls `_start_race()` then immediately `_invalidate()` — and `_invalidate()`
calls `_mm_rec.stop()`, which *clears the recorded points*. The run completes with `points: 0`.

The working sequence, and the one the service uses:

```
on screen_change -> UNKNOWN_RACE_ACTIVE:
    force_screen RESET     # RESET ∈ _RACE_START_SCREENS (race.py:30)
    force_screen RACING    # old=RESET -> genuine fresh start, no invalidation
```

This is **self-correcting**: forcing happens *only* on `UNKNOWN_RACE_ACTIVE`. A video that does
include a real loading screen starts a valid race by itself and is never forced.

### 1.2 Other engine facts the service must respect

- **Processing is wall-clock bound.** The trackers are rate-limited on real time (10 Hz scans,
  timestamp bursts, EMA), so a WR video takes ~its own duration to process (~100 s). `--video-fps 0`
  would break sampling density, not speed things up. Batch speed-ups are not available.
- **`--video-once` stops playback but does not exit the process.** The service must reap the engine
  itself (watch for `run_finalized`, then kill; plus a hard timeout).
- **Engine diagnostics `print()` to stdout**, interleaved with the JSON IPC stream. The service must
  tolerate non-JSON lines (and can usefully surface `[MinimapTracker]` lines into its log).
- **`set_selection` takes top-level keys**, not `{field, value}`:
  `{"type":"set_selection","course":"Mario Circuit","character":"Toadette","costume":"Explorer","kart":"Baby Blooper"}`
  (`main.py:179`). Values are **engine display names**, derived from template filenames via
  `_`→space + `.title()` — e.g. slug `baby_blooper` → `"Baby Blooper"`.
- **Course names are EU/UK.** `minimap_seeds` keys on `Warios Galleon`, not mkwrs's US
  `Wario Shipyard`. Slug resolution is what bridges this (`MKWRS_ALIASES`, `courses.ts:5`).

---

## 2. Scope

**In scope (v1):** WRs observed becoming current from now on, plus a one-time seed of the 30
currently-current WRs. Trails accumulate as WRs fall, so history populates organically.

**Out of scope (v1):** walking the pre-existing back-catalogue of historic WRs (several hundred to
~1500 videos ≈ 8–40 h, plus dead links and pre-1080p60 uploads). `wr_trails` keys on
`world_records.id`, so backfill later is purely a matter of inserting older `wr_id`s into
`wr_jobs` — **no schema change required**.

**Also out of scope:** any engine change; 200cc or non-150cc (mkwrs MKWorld is 150cc-only, `cc` is
a hardcoded constant at `scrape.ts:18`); the NSIS component picker (see §8).

---

## 3. Pi — schema

```sql
CREATE TABLE IF NOT EXISTS wr_trails (
  wr_id    INTEGER PRIMARY KEY REFERENCES world_records(id) ON DELETE CASCADE,
  codec    INTEGER NOT NULL,          -- reuse CODEC_BROTLI_V1 + trailCodec.ts verbatim
  n        INTEGER NOT NULL,
  max_t_ms INTEGER NOT NULL,
  data     BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS wr_jobs (
  wr_id       INTEGER PRIMARY KEY REFERENCES world_records(id) ON DELETE CASCADE,
  lease_owner TEXT,                   -- stable per-machine worker id
  lease_until TEXT,                   -- ISO; NULL or past == claimable
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_wr_jobs_claim ON wr_jobs(lease_until);
```

`wr_trails` deliberately mirrors `run_trails` (`schema.sql:66`) so `trailCodec.ts` is reused with
no changes: same `codec` / `n` / `max_t_ms` / `data` shape, same brotli-v1 packing, same
bit-exactness guarantees.

**There is no `status` column, by design** — a `wr_trails` row *is* "done". `wr_jobs` carries only
lease and failure bookkeeping.

**Strangers never enter `players` / `runs` / `seasons`.** Keying on `world_records.id` means a WR
holder can never leak into leaderboards, turf, activity, PBs, the roster, or Discord — and no
future query has to remember to filter them out.

### 3.1 Enqueue and claim

The reconciler inserts a `wr_jobs` row whenever a WR becomes current (`reconcile.ts` Case 2 —
both the `inserted` and `reflagged` branches). A boot migration seeds rows for existing
`is_current=1` WRs that have a `video_url` and no trail.

Claimable predicate:

```sql
SELECT j.wr_id, w.* FROM wr_jobs j JOIN world_records w ON w.id = j.wr_id
 WHERE w.removed_at IS NULL
   AND w.video_url IS NOT NULL
   AND w.character_slug IS NOT NULL          -- unslugged == unprocessable (§5)
   AND NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = j.wr_id)
   AND (j.lease_until IS NULL OR j.lease_until < datetime('now'))
   AND j.attempts < 5
 ORDER BY w.is_current DESC, w.achieved_at DESC, j.enqueued_at ASC
 LIMIT 1;
```

**Note the absence of an `is_current=1` filter.** Supersession changes *priority*, not
eligibility: a WR that falls before it's processed still gets a trail (it's valid data for that
`wr_id`, and we've already paid to enqueue it), it just yields to newer work. This is what makes
history accumulate for free.

Claim must be atomic (`BEGIN IMMEDIATE`, select + stamp `lease_owner`/`lease_until` in one
transaction) so two machines can't take the same job.

**Only `character_slug` is required.** A NULL `costume_slug` is *legitimate*, not a failure —
`splitCharacter()` returns `costume: null` for a bare name, meaning the base costume, which is by
far the common case. Gating on it would reject most WRs. `kart_slug` is not load-bearing either:
the minimap threshold keys on course + character + costume only
(`get_minimap_threshold(course, character, costume or "")`, `race.py:500`), and the kart is
injected for completeness alone. Requiring either would be a bug.

---

## 4. Pi — endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/wr-jobs/claim` | Bearer (worker) | Atomically lease one job; returns job or 204 |
| `POST` | `/v1/wr-jobs/:wr_id/heartbeat` | Bearer (worker) | Extend `lease_until` |
| `POST` | `/v1/wr-jobs/:wr_id/result` | Bearer (worker) | Trail, or a failure reason |
| `POST` | `/v1/wr-jobs/:wr_id/release` | Bearer (worker) | Voluntary release (pause/discard) |
| `GET` | `/v1/wr-trails?course=&cc=` | none (`PUBLIC_READS`) | Client read |

Claim response:

```json
{ "wr_id": 412, "course_slug": "mario_circuit", "course_name": "Mario Circuit",
  "cc": 150, "video_url": "https://www.youtube.com/watch?v=...",
  "record_ms": 62934, "lap_splits_ms": [18213, 20335, 24386],
  "character_slug": "toadette", "costume_slug": "explorer", "kart_slug": "baby_blooper",
  "lease_until": "2026-07-15T00:31:00.000Z" }
```

Writes take a **Bearer header only**, never `?token=` — matching the existing `POST /v1/runs`
rule (`pi/CLAUDE.md`). Workers get a dedicated token via the existing `mint-token` CLI, distinct
from Paul's player token, so it can be revoked independently.

`GET /v1/wr-trails` joins the `PUBLIC_READS` allowlist (`api/app.ts:38`) for token-free
cross-origin reads, consistent with `/v1/world-records`.

**Lease TTL:** 10 minutes, extended by heartbeat every 30 s while working. Download is ~10 s and
processing ~100 s, so the margin is wide. A dead machine simply stops heartbeating, its lease
expires, and the next worker claims the job — **that is the whole reassignment mechanism**; the Pi
needs no liveness tracking, no worker registry, and no reaper.

---

## 5. Pi — scraper fixes (prerequisite)

The current-WR path stores `character` / `vehicle` **raw only**: the three `*_slug` columns aren't
in its INSERT column list (`reconcile.ts:79`) and `backfill()` never writes them. Only the history
reconciler resolves slugs (`history_reconcile.ts:44-46`), and it drips one track per 2–6 h — a
full sweep is 2.5–7.5 days. A fresh WR would therefore have no slugs for days.

This is **load-bearing, not cosmetic**: without slugs the service cannot build `set_selection`, so
an unslugged WR is unprocessable. Hence `character_slug IS NOT NULL` in the claim predicate.

1. **Resolve on the current path.** Run the existing `splitCharacter()` (`history_parse.ts:76`) and
   `resolveItem()` (`roster.ts:50`) in `reconcileOne`; add `character_slug` / `costume_slug` /
   `kart_slug` to the INSERT and to `backfill()`.
2. **Re-resolve when raw changes.** `backfill()` already diffs raw values; when a raw value moves,
   re-run `resolveItem` and rewrite the slug beside it.
3. **Alert on unresolvable names.** `upsertFlag` already records these; wire the current path to it
   too, and publish a `wr_name_flag` event on the EventHub so the bot (already a `/v1/events`
   consumer) posts to Discord. **Alert only on first sighting** — `upsertFlag` increments
   `occurrences` on conflict, so gate on `occurrences === 1` (via `RETURNING occurrences`) or the
   15-minute scraper will re-alert forever.
4. **Flag unmapped courses.** Nothing writes `category:'course'` today and `resolveFlags` skips it
   (`flags.ts:27`); unmapped course names only reach `WrReport.unmapped` and get console-logged.
   If mkwrs renames a track, reconciliation silently stops for it and nothing says so. Wire
   unmapped courses through the same flag + alert path, and teach `resolveFlags` to re-check the
   `course` category against the `courses` table.

Existing gotcha to preserve: `reconcile.ts:91` gates event emission on `if (cur)`, so a
first-ever WR for a course emits no `wr_update`. This does **not** affect the service — claiming
is a query, not an event subscription. The WS is a latency nudge only; polling alone is correct.

---

## 6. The WR service

A **second Tauri binary inside the existing `src-tauri` project**, sharing the bundled engine exe,
`sync.rs` upload patterns, the icons, and the palette. Requires enabling the `tray-icon` and
`image-png` features on the `tauri` dep (currently `features = []`, `Cargo.toml:14`), plus
`tauri-plugin-autostart` and `tauri-plugin-single-instance`.

### 6.1 Tray UX

- **Badge:** pbenguin's icon with the blue re-shaded — **grey when inactive, red when active**.
  Needs a 16×16 asset; `src-tauri/icons/` has no 16×16 today (smallest is `32x32.png`), so either
  add one or downscale at runtime via the existing `image` crate.
- **Hover tooltip:** current activity — `Downloading Mario Circuit 62%` / `Processing Mario
  Circuit 41%` / `Idle — 3 queued` / `Paused`.
- **Left click:** live log feed window (webview; reuses Svelte components + design tokens).
- **Right click:** Pause / Resume / Exit, context-sensitive per §6.3.

### 6.2 Work loop

```
claim -> download -> process -> verify -> upload -> cleanup -> repeat
```

1. **Claim** `POST /v1/wr-jobs/claim`. 204 → idle, poll with backoff (and/or wake on the
   `wr_update` WS event as a latency nudge).
2. **Download** with yt-dlp:
   `-f "bestvideo[height=1080][fps=60][vcodec^=avc1]/bestvideo[height=1080][fps=60]/bestvideo[height=1080]"`
   — a **format selector, never a hardcoded id**: format ids are per-video and `299` does not
   exist on every upload. Video only; audio is never fetched. 1080p is non-negotiable (all ROIs
   are 1080p pixel coords; `_norm()` would rescale anything else and blur the templates), so no
   1080p60 stream → fail the job with a reason rather than process it badly. avc1 is preferred,
   not required — that mattered only for a Pi's hardware decoder.
3. **Process**: spawn the engine `--video <path> --video-once --no-display`, then
   - on `ready` → `set_selection` with display names mapped from the job's slugs
   - on `screen_change` → `UNKNOWN_RACE_ACTIVE` → `force_screen RESET` then `force_screen RACING`
   - collect `run_finalized`, then **kill the engine** (it will not exit on its own)
   - hard timeout at ~3× video duration
4. **Verify** (§6.4).
5. **Upload** `POST /v1/wr-jobs/:id/result` with the points array.
6. **Cleanup**: delete the video on *every* terminal outcome.

### 6.3 Pause semantics

| State when paused | Behaviour |
|---|---|
| Mid-download | Suspend the download; keep the lease alive by heartbeat; resume continues it |
| Mid-processing | **Discard** — kill the engine, bin the partial, `release` the lease so another machine can take it. Tracking must happen in one unbroken pass |
| Idle | Stay idle; simply stop claiming |

Note for the record: pbenguin's own ghost recorder does **not** discard on pause — `_pause()`
(`race.py:261`) doesn't touch the recorder; the race clock freezes and the recorder's monotonic
guard drops the paused frames. What actually discards is *invalidation* (`_INVALIDATE_SCREENS`),
which is terminal and sticky. Discard-on-pause here is a deliberate choice, not an inherited rule
— and it's cheap, because "discard" only means re-download (~6 s) and re-run later.

### 6.4 Verification — the free correctness gate

**The engine reads the time off the video independently of mkwrs.** The spike got `1:02.934`
against a scraped `1'02"934`. So:

- `detected total_ms == job.record_ms` → upload
- mismatch → **reject**, report failure with the detected vs expected values

This catches mkwrs typos, wrong links, re-uploads of the wrong run, and truncated videos — using a
number already in hand. Splits provide a secondary check against `lap_splits_ms` where present.

Additional failure reasons: `no_1080p60`, `download_failed`, `video_unavailable`,
`no_trail` (0 points — i.e. the minimap never locked), `time_mismatch`, `timeout`.

### 6.5 Local state

No local queue — **the Pi is the queue**, which is exactly what makes it survive app and system
restarts. Local SQLite holds only: a stable worker id, the single in-flight job (for crash
recovery and orphan cleanup), and a log ring buffer. On startup: sweep orphaned video files,
release any stale in-flight job.

Only one job is ever in flight — processing is real-time bound, so there is nothing to gain from
concurrency on one machine.

---

## 7. pbenguin client

Fetch `GET /v1/wr-trails?course=` per course; cache beside the existing `course_cache` table
(`sync.rs`) so reads survive a flaky network. Draw a **grey** WR dot; the current WR **pulses** —
`drawOverlay` already has a sinusoidal breathe for `is_pb` (`overlay.js:250`) to reuse.

`trailSettings.js` already persists per-source display config in `localStorage`; WR becomes one
more source. **Default: show only the current WR**, on. A "show all historic WR trails" mode is
config-only and becomes useful as superseded trails accumulate (§3.1).

Colours stay locked and non-user-configurable, consistent with the existing rule that "a player's
colour is fixed + the same on every client" (`trailSettings.js:5`).

---

## 8. Risks and decisions taken

- **Installer component picker deferred.** Tauri v2's NSIS bundler doesn't expose component
  selection as a config knob; a real components page needs a custom `nsis.template`. **v1 ships the
  binary with autostart OFF by default, opt-in from the UI** — ~90% of the "don't bloat non-users"
  goal for ~5% of the effort. Installer work comes later.
- **The `force_screen` bypass is implicit.** It works and needs no engine change, but it depends on
  the service noticing `UNKNOWN_RACE_ACTIVE` and reacting. A later explicit engine ingest mode
  would be cleaner; not needed for v1.
- **yt-dlp rots.** YouTube changes break it regularly; it needs a pinned-but-updatable dependency
  and `video_unavailable` must be a normal, retryable outcome rather than an exception.
- **Downloading YouTube videos** is against YouTube's ToS. Paul's call; noted, not litigated.
- **`OVER_LIMIT_MS = 11 min`** (`ingest.ts:10`) drops trails on the run path. WR videos are ~2 min,
  so it never bites — but don't blindly copy that guard's *value* if reusing ingest code.
- **The wire format drops `lap`.** Both trail serializers emit 4-tuples (`db/reads.ts:104`), while
  storage keeps 5. WR trails should match whatever the client expects; no reason to diverge.

---

## 9. Implementation phasing

This spec spans three loosely-coupled chunks and should become **three plans**, not one. They are
ordered by dependency, and each is independently verifiable:

1. **Pi foundation** — §5 scraper fixes (slugging + flags + alerts), then §3 schema and §4
   endpoints. Testable on its own via vitest and the ops CLIs, with no client involved.
2. **The WR service** — §6. Depends on plan 1's `claim` endpoint existing. The download → engine →
   verify core can be built and proven headless *before* any tray UI, using the §9 fixture.
3. **Client display** — §7. Depends on plan 1's `/v1/wr-trails` and on plan 2 having produced at
   least one real trail to look at.

Plan 1 is the only one that touches existing production code paths; plans 2 and 3 are additive.

## 10. Testing

- **Pi (vitest, colocated):** claim atomicity under concurrent claims; lease expiry →
  reclaimable; supersession keeps a job eligible but lowers priority; `attempts` cap; trail
  round-trips bit-exact through `trailCodec`; `wr-trails` reads; slug resolution on the current
  path; first-sighting-only alert gating.
- **Service:** verification gate accepts an exact time and rejects a mismatch; pause mid-download
  vs mid-processing; orphan cleanup; engine reaping on `--video-once`; format-selector fallback
  when no 1080p60 exists.
- **End-to-end fixture:** `temp/wr_mario_circuit.mp4` (JaK, Mario Circuit, 1'02"934) is a known-good
  video with a known-exact expected output — 1732 points, `1:02.934`, splits `18.213/20.335/24.386`.
  `temp/` and `*.mp4` are gitignored, so it is a local fixture, not a committed one.
