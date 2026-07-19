# pbenguin chip cache + full-pack download + new live cards

**Date:** 2026-07-19 · **Status:** approved (brainstorm with Paul)
**Builds on:** `2026-07-18-chip-site-pack-design.md` (fixed contracts: site manifest at
`/chips/anim/manifest.json`, tagged immutable sheet URLs, `chips-vN` GitHub release pinned by
`web/chips.lock`, binding canvas Playback rules) and the locked live-card design
(`docs/design/site-redesign/live-card.html` + `fire-live-card.html`, currently on the
`site-redesign-p1` worktree branch).
**Concurrent work:** Velopack updater migration shares the repo (possibly the same branch).
Overlap is limited to `src-tauri/src/lib.rs` (command registration), `SettingsModal.svelte`
(this adds a tab; Velopack adds a delete-data button), and `App.svelte` edges — all chips code
lives in new files/modules so merges stay trivial in either order.

## Goal

pbenguin renders the locked "KART-OFF print" live cards — including the sprite-sheet chip
animations — with chips arriving through a persistent local cache: on-demand fetch by default
(instant for anything seen before), plus an opt-in "Download full pack (6.3 GB)" for
fully-offline instant chips. The pbenguin engine stays a pure detector: all networking and
caching is Rust (`src-tauri/`); the UI is Svelte.

## Shape: two parts, one seam

- **Part A — chip store (Rust):** `src-tauri/src/chips/` module — disk cache, `chips://`
  protocol handler (cache-or-fetch), resumable full-pack downloader, settings "Chips" tab.
- **Part B — new card (shared Svelte):** the locked live-card design built ONCE as a
  self-contained component in root `src/` (`LiveCard.svelte` + `src/lib/liveCard.js`).
  pbenguin's `PlayerPanel` switches to it now; **`PlayerCard.svelte` is left untouched**
  because the site's CardWall still imports it — site adoption (and PlayerCard retirement)
  happens in site-redesign P1b. Zero site risk from this work.
- **The seam:** the card fetches `manifest.json` and builds sheet URLs from the manifest's
  `base` field. Site: `base = "/chips/anim/<tag>/"`. pbenguin: the protocol handler serves the
  manifest with `base` rewritten to `chips://local/<tag>/`. Same component code, zero
  branching per surface.

## Part A — chip store

### Cache layout

In the app data dir (⇒ covered by the Velopack plan's "Delete all app data…" button):

```
<appdata>/chips/
  current              # text file: current tag, e.g. chips-v1
  chips-v1/
    .complete          # present only when the full pack is verified-installed
    .pack-state.json   # full-pack download state (lock snapshot + per-shard status)
    chips/manifest.json + *.webp (+ sils, which arrive inside pack shards)
```

On-demand fetches fill the tag dir file-by-file; the full pack fills it completely. One
unified store — the protocol handler cannot tell (and doesn't care) which path wrote a file.

### Protocol handler (`chips://local/…`)

Registered async URI scheme (on Windows WebView2 it surfaces as `http://chips.localhost/…`,
fetchable and `createImageBitmap`-compatible).

- `chips://local/manifest.json` → cached copy, refreshed from
  `https://thekartoff.com/chips/anim/manifest.json` at most every 5 minutes (matches the
  site's max-age). `base` rewritten to `chips://local/<tag>/`. Offline → last cached copy;
  never-fetched → 404 (cards render chipless — same fallback as the site).
- `chips://local/<tag>/<file>` → serve from cache; on miss, download that one file from the
  site's tagged URL, write atomically (temp + rename — a killed app never leaves a truncated
  sheet), serve. Path traversal guarded (no `..`, tag must match `chips-v[0-9]+`).
- Write failure (disk full etc.) degrades to serving the fetched bytes without caching.

### Eviction (no storage double-up — Paul, 2026-07-19)

A tag's release assets are immutable; updates ship as a whole new tag. Once the manifest
flips, old-tag URLs are never requested again — so on adopting a new manifest tag, **all
old-tag dirs are deleted immediately, partial caches and complete packs alike**. Disk ceiling
is ~one tag's data + one in-flight shard. A previously-installed full pack does NOT
auto-redownload: the Chips tab shows "pack update available" (no silent 6 GB pull); until the
user clicks, the on-demand cache covers the gap.

### Full-pack downloader

- **Lock source:** the Pi serves its checked-out `web/chips.lock` at `GET /chips/anim/lock`
  (new small `web/serve.mjs` route + test). This pins the pack download to the exact tag the
  site is currently serving — no version skew with the on-demand path. (Bundling the lock in
  the app was rejected: goes stale between app releases; GitHub-raw can run ahead of what the
  Pi actually deployed.)
- **Per shard:** download → sha256-verify → untar into the tag dir → delete tar. Peak disk ≈
  pack + largest shard (~6.7 GB), checked up front. (Deliberate improvement over
  `deploy/fetch_chips.sh`'s download-all-then-unpack; verify semantics are identical — the
  lock's per-file sha256s.)
- **Resume is byte-level and survives anything:** `.pack-state.json` records the lock
  snapshot (tag + per-shard shas) and per-shard status (`pending`/`downloaded`/`done`).
  A partial shard resumes via `Range: bytes=<size>-` (GitHub release assets support Range);
  sha-verify runs only when byte-complete, and a failed verify re-downloads just that shard.
  The tar is deleted only after untar succeeds and the shard is marked `done`, so a kill
  mid-untar re-untars that shard (overwrite-idempotent). Pause is a flag checked between
  chunks (stops within ~a second, state already on disk). Button-resume and boot-resume are
  the same code path.
- **Start/resume always re-fetches the lock first.** New tag → discard stale staging, start
  on the new tag. Same tag but changed shas (contract violation, e.g. force re-upload) →
  incremental for free: shards whose sha still matches stay `done`, only changed shards redo.
  A tag flip during an active download isn't polled mid-flight; the completed pack's tag
  mismatch is caught by the next manifest refresh → "pack update available".
- **Standing intent:** `chips_pack_wanted` flag in the existing `wr::state` settings DB; set
  on start, cleared on cancel. If set and the pack is incomplete at boot, the download resumes
  quietly.
- Runs on the tauri async runtime; progress events `chips-progress` (shard n/51, bytes,
  state: downloading/verifying/unpacking/paused/done/error).

### Commands + settings UI

Commands: `chips_get_status`, `chips_start_pack`, `chips_pause_pack`, `chips_cancel_pack`
(clears staging + intent), `chips_delete_cache`.

**Settings → new "Chips" tab:** cache status (combos cached / size on disk / current tag);
"Download full pack (6.3 GB)" with progress bar + pause/cancel (label switches to
"pack update available" on tag mismatch, "installed" when complete); "Delete chip cache".
No toasts mid-race — pack state surfaces only in this tab.

**Rehearsal:** `PBENGUIN_CHIPS_URL` env override (pattern follows the Velopack plan's
`PBENGUIN_UPDATE_PATH`) points manifest + lock + downloads at a local scratch server with a
tiny fake lock and 2–3 mini shards.

## Part B — new live card

- `LiveCard.svelte` (root `src/components/`) + pure helpers `src/lib/liveCard.js`; styles
  fully self-contained (no site-kit dependency — the locked HTML is self-contained; faithful
  translation). `PlayerPanel` renders it in place of `PlayerCard`.
- **First build task copies the locked design files** (`live-card.html`,
  `fire-live-card.html`, hand-drawn fire frames, with their decision-log headers) from the
  `site-redesign-p1` worktree branch into this branch under `docs/design/site-redesign/` —
  the decision logs are the truth for the translation.
- All locked rules apply: 2× supersampled text rendering; jank marks on SETTLED facts only
  (ticking timers run straight); timers m:ss.mmm; zigzag lap-segment progress; stacked-tag
  selection; PB wave in its own colour; photo on every card, border always figure-side;
  two-ply same-shape scrapbook tearout behind the chip; hand-drawn 3-frame tearout fire
  (multi-ply, centered on body mass, torn base). All current card states carry over: offline
  career stats, stale (server-down), idle online, racing, finished/held.
- **Chip playback per the site-pack spec's binding Playback rules:** `createChipPlayer`
  (`src/lib/chipSheet.js`), canvas `drawImage` with `frameRect`, all of a combo's sheets
  pre-decoded to `ImageBitmap`s, integral-device-pixel backing + device-px snap, ink ring
  baked in the draw, skip-draw-hold-last when a bitmap isn't ready — which is also the
  offline story: no chip bytes yet → the tearout renders empty until they arrive; no broken
  cards ever.
- **Choreography** (site brief → desktop entry data): selection change on a select screen →
  `select()` (spawn, interruptible); screen entering the race → `confirm()` (flourish → idle
  at `idle_resume` for karts, hard cut for chars); `final_time` set → flourish once.
- **`src/lib/chipKey.js`:** display names → pack combo slugs
  (`"Baby Daisy" + "Base" + "B Dasher"` → `baby_daisy__base__b_dasher`; char-only combo
  `<char>__<costume>` while no kart is picked), tested against real manifest keys.

## Error handling

Every network path degrades to "no chip / stale manifest", never a broken card. Protocol-
handler misses while offline are silent. Full-pack failures leave resumable state and one
status line in the Chips tab. Disk-space pre-check before pack start; cache-write failures
fall back to pass-through serving.

## Testing

- **Rust:** unit tests (temp-dir style like `wr::state`): lock parse, cache path resolution
  incl. traversal guard, sha verify, resume state machine (pause/kill/lock-change cases),
  eviction rules, manifest base rewrite.
- **JS:** `chipKey` slug tests vs real manifest sample; `liveCard` view-model tests;
  choreography transition tests (pure logic, `chipSheet.test.js` style). Existing
  `chipSheet.js` tests already cover playback math.
- **Pi:** serve.mjs `/chips/anim/lock` route test.
- **Rehearsal:** full-pack flow against the local scratch server (`PBENGUIN_CHIPS_URL`) —
  download, pause, kill-and-resume, tag-flip eviction. Paul's in-app eyeball of the cards vs
  the locked HTML is the visual gate.

## Decomposition

One spec, two implementation plans: **Plan A** (chip store + settings tab + Pi lock route),
**Plan B** (LiveCard port + PlayerPanel swap). They meet only at the manifest contract; A can
be verified with the scratch server before B exists.

## Non-goals / deferred

- Site CardWall adoption of `LiveCard` + `PlayerCard.svelte` retirement (site-redesign P1b).
- Auto-redownload of an updated pack (explicit button only).
- Bandwidth shaping / auto-pause of the pack download while a race is tracked.
- Safari-class fallbacks (site concern; N/A in WebView2).
