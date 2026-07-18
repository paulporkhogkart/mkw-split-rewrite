# WR Service Plan 4 — Client WR dots: design

**Date:** 2026-07-18
**Status:** approved (design); implementation plan pending
**Parent:** `2026-07-15-pbenguin-wr-service-design.md` §7 (client display). Plans 1–3 shipped:
the Pi serves `GET /v1/wr-trails` (built Plan 1, `pi/src/api/reads.ts:57`, `PUBLIC_READS`),
and the background service produces trails. This plan makes pbenguin draw them.

Decisions locked (Paul, 2026-07-18):

| Decision | Choice |
|---|---|
| Mental model | **The WR is just another player**, drawn grey. Its "PB" is the current WR; historic WRs are its ordinary ghost runs |
| Z-order (revised 2026-07-18, supersedes the earlier "above player PBs" call) | **WR always yields to players within its rank**: the current WR paints directly UNDER the player-PB band; historic WRs paint UNDER all player past runs. Full hierarchy in §3 |
| Abandoned tier (added 2026-07-18) | The whole hierarchy is **duplicated into two tiers**: alive runs (dots) above, abandoned runs (the ones that end as an X) below — every alive run outranks every abandoned one, so even a hypothetical dead current WR sits under an alive player past run. Tier comes from the run's `abandoned` flag (static), not from the frame it visually becomes an X |
| Opacity | **No special dimming.** Full opacity like every player run; historic WRs obey the existing global "Fade older runs by rank" toggle exactly as a player's non-PB runs do |
| Default | **Current WR only, on** (`mode: "current"`); "all history" and "off" are options |
| Colour | Locked grey `#a7adb5`, a constant beside `TRAIL_PRESETS` — never user-configurable (the `trailSettings.js:5` rule) |
| Storage | Already satisfied by Plan 1, pinned here per Paul: `wr_trails` stores trails **identically to `run_trails`** — same `codec`/`n`/`max_t_ms`/`data` columns, same `trailCodec.ts` brotli-v1 blobs (parent §3; `pi/src/db/wrTrails.ts` insertWrTrail). Points are decoded only on the wire, exactly like run trails. Plan 4 adds no storage |
| Copy | No em dashes in any user-facing string (standing rule, 2026-07-18) |

---

## 1. Data path — one more read inside `sync_course_reads`

`fetch_course_reads` (`src-tauri/src/sync.rs`) gains a fourth GET:
`{base}/v1/wr-trails?course=<course>&cc=150` — public, sent without auth requirement,
same 8s timeout and `get_json` shape as the other three. The combined payload gains a
`"wr_trails"` key carrying the Pi rows verbatim:

```json
{ "wr_id": 412, "holder_name": "JaK", "record_ms": 62934, "record_str": "1'02\"934",
  "achieved_at": "...", "is_current": 1, "video_url": "...",
  "points": [[t_ms, cx, cy, score], ...] }
```

Rows arrive fastest-first (`courseWrTrails`, `pi/src/db/wrTrails.ts:30`). `points` 4-tuples
feed `interpolateXY` unchanged (it reads elements 0..2 and ignores the rest).

- `sync_course_reads` gains a `wr_mode: Option<String>` parameter (JS sends `wrMode` —
  Tauri v2 camelCase mapping, same as `serverUrl`). Missing or unrecognized values are
  treated as `"current"` (the product default), one validation point.
- **The wr-trails read degrades, never fails**: on any error it contributes `[]` instead
  of failing the whole payload — a Pi that predates the endpoint (or a transient error)
  must not take PB splits and player trails down with it. The other three reads keep
  their existing all-or-cached semantics.
- When the resolved mode is `"off"`, the GET is skipped and `wr_trails` is `[]` — a user
  who never enables WRs costs the Pi nothing.
- `EMPTY_COURSE_READS` gains `"wr_trails":[]`. A stale cached payload from before this
  feature simply lacks the key; the builder tolerates it (`?? []`).
- The fetch does NOT filter to the current WR server-side: the endpoint has no such query
  param and today a course has at most a handful of trailed WRs. Accepted cost; if history
  accumulates enough to matter, add a `?current=1` param on the Pi later (out of scope).
- Cache note: with mode `"off"`, the cached payload holds `[]` — turning WRs on shows dots
  from the next successful fetch (next RACING entry, online). Matches how every other
  trail-settings change already behaves.

Approaches rejected: a separate command + cache table (duplicates the cache/invalidation
machinery for nothing) and a direct webview fetch of the public endpoint (would be the only
place the webview talks to the Pi, and loses the offline course_cache).

## 2. Settings model — one key in `trailSettings`

`trailSettings` (localStorage) gains `wr: { mode: "off" | "current" | "all" }` with
`DEFAULTS.wr = { mode: "current" }`. The existing `{ ...DEFAULTS, ...stored }` spread
handles old stored blobs (missing key → default). New pure helper `wrCfg(settings)`
mirrors `playerCfg`. `resetTrailSettings` resets it with everything else. No count field:
"current" is the WR player's PBs-only mode, "all" is its everything mode.

## 3. Rendering — WR rows become ordinary runs in `buildTrailRuns`

`buildTrailRuns(courseReads, settings, rosterList)` additionally maps
`courseReads.wr_trails ?? []` through the mode:

- `"off"` → nothing. `"current"` → rows with `is_current` truthy (normally one).
  `"all"` → every row.
- Each row becomes a run: `points`, `color: WR_COLOR` (`#a7adb5`), `abandoned: false`
  (a stored WR trail is by construction a verified finished run), `total_ms: record_ms`.
- **Current WR:** `is_pb: true`, opacity 1 — the existing overlay breathe pulses it with
  zero changes to `overlay.js`.
- **Historic WRs:** `is_pb: false`, opacity via the same `rankOpacity(i, count, fade)`
  the player runs use, over the WR rows in their fastest-first order (fade toggle off →
  1.0 like everyone else; on → fastest brightest, same as a player's Best-mode fade).

The paint-order sort gains an explicit band index (computed in the builder; `is_pb`
stays purely the pulse flag, `abandoned` stays purely the X flag) implementing Paul's
two-tier hierarchy — WR yields to players within its rank, and every alive run outranks
every abandoned run:

```
rank within a tier:  0 historic WR   1 player past run   2 current WR   3 player PB
band = (abandoned ? 0 : 4) + rank        // paint order = ascending band

bottom  band 0: abandoned historic WRs      (definitionally empty: WR trails are verified finished)
        band 1: abandoned player past runs  (the runs that end as an X)
        band 2: abandoned current WR        (definitionally empty, hierarchy defined anyway)
        band 3: abandoned player PBs        (definitionally empty: a PB is a finished run)
        band 4: alive historic WRs
        band 5: alive player past runs
        band 6: alive current WR
top     band 7: alive player PBs            (fastest on top, as today)
```

A run's tier is its `abandoned` flag — a static property — so the paint order never
reshuffles mid-race when an abandoned dot visually turns into its X. Within a band, the
existing tiebreaks stay: fainter lower, faster higher. The empty-by-construction bands
cost nothing (the band is a formula, not a data structure) and keep the pure function
total for any input.

**Legend:** `trailLegendRows` gains one grey `"WR"` row while `wr.mode != "off"` (no
holder name in v1; the legend is settings-derived and has no server data in scope).

## 4. Settings UI — one row in `TrailSettings.svelte`

A "World record" block above the player grid, reusing the row idiom: label
"World record", a three-option select (`Off` / `Current WR` / `All WRs`), the locked grey
chip, no count input. Hint line (em-dash-free):
"The current world record replays as a grey dot. It pulses so you can tell it apart from
player ghosts. All WRs adds the older records this track has had."
The existing fade-toggle copy needs no change; historic WRs simply obey it.

## 5. Refresh semantics — none added

WR dots ride the existing triggers: `loadCourseReads` on every RACING entry
(`App.svelte:898`), per-course cache offline, cleared-state rule on course change
(`trailRunsStore.set([])` already covers WR runs since they are ordinary runs). A
settings change applies at the next race, exactly like the player-trail settings today.
`App.svelte` passes `wrMode: wrCfg(settings).mode` beside the existing `config:` argument.

## 6. Testing

- **JS unit (`trailSettings.test.js`):** mode selection off/current/all; current WR gets
  `is_pb` + grey and sorts directly UNDER every player PB; historic WRs sort UNDER every
  alive player past run and obey `rankOpacity` parity with player runs; the two-tier split
  is table-tested on the band formula, including Paul's canonical case (an abandoned
  current WR sorts below an alive player past run) and an abandoned player ghost below an
  alive historic WR; missing `wr_trails` key (stale cache) yields no WR runs and no crash;
  legend row appears iff mode != off; `wrCfg` defaults.
- **Rust:** `EMPTY_COURSE_READS` contains the `wr_trails` key (shape-pinning test beside
  the existing const); mode resolution helper (`"off"`/`"current"`/`"all"`/garbage/None)
  is a pure function with a table test.
- **End-to-end (manual, scratch Pi):** seed a scratch Pi via the fix-wave smoke technique
  (fresh DB + seed_courses + real scrape + a temporary `#[ignore]` test driving
  `service::process_one` on the Mario Circuit fixture; pin the target job by setting
  `attempts=5` on the others, never row deletion — boot `seedWrJobs` re-enqueues). Then
  point the app at the scratch Pi and replay `temp/wr_mario_circuit.mp4` through the LIVE
  app: the grey pulsing dot must shadow the live tracked marker for the whole race, since
  the video IS the current WR run. That coincidence is the whole correctness check in one
  glance.

## 7. Out of scope

- Any Pi change (endpoint, filtering, schema) — Plan 4 is client-only.
- Holder names / record times in the legend or Race section; dot click-through to the
  video. All possible later; none needed to ship dots.
- WR dots on the website (the site has its own trail rendering surface).
- The Sky-High Sundae engine seed-row migration (separate standing decision).
