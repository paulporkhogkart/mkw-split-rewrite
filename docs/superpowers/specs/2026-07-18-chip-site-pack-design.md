# Chip Site Pack — encode + delivery pipeline for matte'd chip animations

**Date:** 2026-07-18 · **Status:** approved (brainstorm with Paul; rev 2 — sprite sheets
replace animated WebP after Paul asked for the comparison and measurement favoured sheets)
**Sources:** `D:\kartoff\asset_chips\matte\` (pristine masters — READ-ONLY, never modified) +
`D:\kartoff\asset_chips\manifest.json` (segments, `idle_resume`, `flourish_fallback`).
**Consumers:** thekartoff.com live cards (locked design `docs/design/site-redesign/live-card.html`
+ `fire-live-card.html`, currently in the site-redesign-p1 worktree) and pbenguin (future usages).

## Goal

Turn ~1.3M pristine 1024×1080 RGBA PNG frames (~1.1TB measured; 6,273 char×costume×kart combos with
spawn/idle/flourish + 153 standalone char×costume combos with idle/flourish) into a small,
fast-loading, versioned asset pack: WebP sprite sheet per animation + tearout sil masks + a site
manifest — hosted as GitHub Release assets, auto-deployed to the Pi, lazy-loaded by the site,
fetched-and-cached by pbenguin with an opt-in full-pack download.

## Format decision: static WebP sprite sheets (grid) + JS stepper

One lossy **static WebP sprite sheet** per animation (`<combo>__idle.webp`, `__spawn.webp`,
`__flourish.webp`): frames tiled in a near-square grid, **max side ≤ 4096px** (single-row strips
up to 12,300px wide risk GPU texture limits), stepped by a small shared rAF JS stepper via
`background-position` (GPU-composited). Rationale:

- **Measured smaller than animated WebP** on identical frames/recipe (idle 218→182KB strip /
  192KB grid; flourish 0.93×, spawn 0.94×): the subject bobs every frame so temporal
  frame-diffing gains ~nothing, while the sheet gets spatial prediction across adjacent
  near-identical tiles.
- **Random access**: idle can start at exactly `idle_resume` (no encode hacks), spawn replays
  are interruptible mid-flight, handoffs are frame-exact, and the stepper knows its frame index
  (real end-of-animation events — no duration+timer guesswork).
- Decoded memory is a wash (browsers cache all decoded frames of looping animated images
  anyway); stepping a static texture avoids continuous animated-image decode.
- Cost: playback is JS-driven, not free `<img>` looping — acceptable; the live card already
  runs a JS ticker for the sil-mask cycle, and one shared rAF loop steps every visible chip.
- Codec still WebP (alpha + universal modern-browser/WebView2 support). WebM/VP9-alpha:
  `<video>` machinery, no frame-exact control, Safari needs HEVC-alpha parallel encode.
  Animated AVIF: slow encode at this scale, shakier support. **Deferred, not needed** — Paul:
  modern browsers first, Safari-class gaps handled later as exceptions.
- The card's ink-ring finish is realtime CSS (four 1px `drop-shadow`s) — nothing baked.

## Encode recipe (candidate — gated by the A/B eye test)

Measured on `baby_daisy__base__b_dasher` (Pillow, pure-Python pipeline, no external tools):

| Recipe (animated-webp numbers; sheets measure ~0.88–0.94× of these) | idle/spawn/flourish | per combo | full pack (×6,273) |
|---|---|---|---|
| 0.2× 60fps q75 (placeholder-equivalent) | 683/142/357 KB | ~1.16MB | ~7.4GB |
| 0.2× 30fps q60 | 311/67/161 KB | ~540KB | ~3.5GB |
| 0.2× 30fps q60 + 5-bit alpha (animated) | 218/47/113 KB | ~375KB | ~2.4GB |
| **0.2× 30fps q60 + 5-bit alpha, sprite-sheet grid** | 192/44/106 KB | **~340KB** | **~2.1GB** |
| 0.15× 30fps q60 + 5-bit alpha | — | ~230KB | ~1.5GB |

Pipeline per frame: decode PNG → **premultiply → Lanczos downscale → unpremultiply** (kills
edge fringe from stray RGB under alpha) → **alpha quantize to 5 bits** (snap <6→0, >249→255;
the lossless alpha plane is 38% of bytes at 8-bit; 5-bit cuts total ~31%, diminishing beyond)
→ paste into the grid sheet, saved as static WebP `quality=60, method=4`.

- `method=6`/`minimize_size`/`allow_mixed` measured ≈2% smaller for 50–80× encode time — **use
  method=4**. Quality above ~q60 barely moves size (alpha dominates).
- **Target scale 0.2× → 205×216** = ~2× device pixels at the biggest card render (112px CSS),
  right for hi-DPI. 0.15× is the fallback if it survives the eye test.
- **30fps amends the locked "60fps webp" card decision** — that is exactly what the A/B page
  exists to judge. Nothing batch-encodes until Paul's eye signs off on scale/fps/quality/alpha.
  (At 60fps the biggest idle grid is 11×11 = 2255×2376 — still under the 4096 cap.)
- At 30fps every source frame index (incl. `idle_resume`) halves; encoder drops every 2nd frame.

### Playback + handoffs (JS stepper; sheets make these frame-exact)

- One shared rAF stepper drives every visible chip via `background-position`; per-chip state =
  sheet + frame index + mode (loop / play-once-hold). Grid geometry (cols/rows/frame count/fps)
  comes from the manifest.
- **spawn → idle:** stepper plays spawn once, then switches to idle at frame 0 (spawn's last
  source frame precedes idle frame 0 — seamless by construction). Spawn replays (re-pick) just
  reset the frame index — interruptible by design.
- **flourish → idle (karts):** stepper plays flourish once, then enters idle **at
  `idle_resume`** directly — random access replaces the encode-side idle-tail hack an animated
  format would have needed. Chars keep the designed hard cut (enter idle at 0).
- End-of-animation is the stepper reaching the last frame — no duration/timer guesswork.
- **Rendering rules (measured on the A/B lab, binding for the card integration):** render
  chips on a **`<canvas>`** — native-resolution backing store (`fw`×`fh`), CSS-scaled to
  display size, `drawImage(sheet, sx, sy, fw, fh, 0, 0, fw, fh)` per frame with the integral
  source rect from `chipSheet.frameRect`. Do NOT step a CSS `background-position` (scaled
  directly OR at native px under `transform: scale()`): a moving background offset is
  pixel-snapped per paint and visibly jitters horizontally as columns cycle. Measured while
  debugging: the sheet pixels themselves are shift-free (decoded-tile dx spread 0.03px vs
  ground truth, statistically identical to animated webp) — the jitter is purely paint-time
  snapping, which canvas avoids because the destination rect never moves (the `<img>` path).
  Decode all of a combo's sheets up-front into **`ImageBitmap`s** (`img.decode()` →
  `createImageBitmap`) — a bare `Image`'s decoded pixels sit in the browser's evictable
  decode cache, so `drawImage` from many/large sheets triggers synchronous re-decodes
  (measured on the lab: global judder even at 60fps); an ImageBitmap decodes once into a
  pinned GPU-backed bitmap and draws are texture copies. If a bitmap isn't ready at switch
  time, skip the draw and hold the previous canvas frame — never blank. (URL/src swapping
  flashes 1–2 blank frames on async decode; an animated-webp `<img>`-swap approach would hit
  the same flash. Per-combo bitmap memory is trivial for the card — ~33MB decoded; the lab
  pins ~100 sheets ≈ 1GB, fine for a desktop eye-test page.) Size the backing store at
  integral DEVICE pixels, derive the CSS display size from it (`W/dpr`), set
  `imageSmoothingQuality:"high"`, and **snap the canvas to the device-pixel grid** after
  layout (sub-pixel `translate` correction): a canvas on a fractional device position is
  resampled and reads soft — on the lab this made identical encodes look crisp or blurry
  depending on accidental flex-layout phase, which was mistaken for encode quality.

## Outputs (one pack dir, all data in one place)

Built to `D:\kartoff\asset_chips\site_pack\` (outside the repo):

1. **`chips/<combo>__<anim>.webp`** — ~19k sprite-sheet animations (near-square grid, row-major, ≤4096px/side).
2. **`chips/<combo>__<anim>__sil_k{0..3}.png`** — tearout masks per animation, chip-resolution
   RGBA, generated from downsampled alpha of 4 frames sampled across the animation using the
   locked tearout language (12-point radial jagged cut, margins 18–34 srcpx, shared jag seeds
   across the 4 frames — variance = pose only). The original placeholder generator is lost
   (session scratch); recreate to visually match the placeholders in
   `docs/design/site-redesign/sil/`, eyeballed on the A/B page. Budget ~5KB each (~75k files
   ≈ +0.3GB pack on top of the webp numbers above).
3. **`chips/manifest.json`** — per combo: anims present, frame counts, grid cols/rows,
   frame w/h, encoded fps/scale, `idle_resume` (post-halving — the stepper enters idle here
   after a kart flourish), `kart` flag. Derived from the
   master manifest; the site and pbenguin read only this.
4. **Shards:** `chips-<char>.tar` per character (~50 shards, ~50MB each; well under the 2GB
   per-asset limit); `pack_shards.py` writes their sha256s inline into `chips.lock` (no
   separate checksums file).

## Delivery: GitHub Release assets + committed lock

- Shards + manifest uploaded to a dedicated release on this (public → unauthenticated curl)
  repo, tag `chips-v1` (`chips-vN` on re-encodes; release is NOT a deploy tag — the
  `v[0-9]*` glob in update.sh ignores it).
- **`web/chips.lock`** committed in git: release tag + shard list + sha256s. This is the
  repo-association: the repo pins the exact asset version; git stays lean (no LFS — banned for
  Pi-served media; no multi-GB binaries in history).
- **Pi deploy** (`deploy/update.sh`): new step after checkout — if `web/chips.lock` differs from
  `$DATA/chips/.version`, curl shards from the release into `$DATA/chips/`, verify sha256,
  unpack, write `.version`. Outbound-only, idempotent, fail-safe like the rest of the script
  (chips failure must not block the code deploy — served stale until next tick succeeds).
- **Pi serves** the pack at **`/chips/anim/`** (`web/serve.mjs`, `MKW_CHIPS_DIR`): the bare
  `/chips/` namespace was already taken by the activity-feed chips (`web/src/lib/chips.js`).
  `GET /chips/anim/manifest.json` (max-age=300) returns the current pack's manifest with an
  injected `"base": "/chips/anim/<tag>/"`; all sheet/sil URLs live under that tagged base and
  are served `immutable` (tag dir per `chips-vN` ⇒ cache-safe across re-encodes). The Pi keeps
  the current + one previous tag under `$DATA/chips/`. The fetcher writes `$DATA/chips/current`
  as a plain text file containing the current tag; the server's `currentChipsTag` accepts either
  a symlink or a text file there, so this needs no special Pi/Windows-dev handling.

## pbenguin

- **Default:** fetch `/chips/` URLs from thekartoff.com on demand, persistent local cache
  (app data dir) — instant for anything seen before.
- **Opt-in:** "Download full chip pack (~2.1GB)" in settings — pulls shards from the GitHub
  release (same lock), full offline instant. No gigabytes in the installer.
- Detailed pbenguin UI/cache design is its own later plan; this spec fixes the contract
  (URLs, manifest, shards, lock).

## First deliverable: A/B eye-test page

A lab page (pattern: `sharpness-lab.html`) rendering real card-size chips (92/112/76px CSS,
against card-dark background, ink-ring CSS applied) side-by-side across: 60 vs 30fps, q75/60/50,
8 vs 5 vs 4-bit alpha, 0.2× vs 0.15×, using a handful of representative combos (big char, small
char, busy kart, standalone char) encoded by the real pipeline code, played by the real JS
stepper (which doubles as a smoothness sanity-check vs one animated-webp reference chip).
**Paul's pick locks the recipe; only then does the ×6,273 batch run.**

## Batch run

Encoder tool lives in `tools/asset_matte/` (beside the pipeline that owns the masters; reads
master manifest, multiprocessing over combos, skip-if-done resume via pack-local book-keeping,
never writes into `matte/`). Estimated wall-clock ~1.5–2h on the 9800X3D (decode+resize
dominates; encode ~1.5s/combo at method=4). Console button can come later; CLI first.

## Non-goals / deferred

- Safari/HEVC-alpha fallback; animated AVIF.
- Baking card finishes (ink ring stays CSS).
- Client-side runtime sil generation (rejected: per-mount canvas decode vs four tiny PNGs).
- Re-encoding masters or touching `D:\kartoff\asset_chips\matte\` in any way.
