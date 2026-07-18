# Chip Site Pack — encode + delivery pipeline for matte'd chip animations

**Date:** 2026-07-18 · **Status:** approved (brainstorm with Paul)
**Sources:** `D:\kartoff\asset_chips\matte\` (pristine masters — READ-ONLY, never modified) +
`D:\kartoff\asset_chips\manifest.json` (segments, `idle_resume`, `flourish_fallback`).
**Consumers:** thekartoff.com live cards (locked design `docs/design/site-redesign/live-card.html`
+ `fire-live-card.html`, currently in the site-redesign-p1 worktree) and pbenguin (future usages).

## Goal

Turn ~1.3M pristine 1024×1080 RGBA PNG frames (~900GB; 6,273 char×costume×kart combos with
spawn/idle/flourish + 153 standalone char×costume combos with idle/flourish) into a small,
fast-loading, versioned asset pack: animated WebP per animation + tearout sil masks + a site
manifest — hosted as GitHub Release assets, auto-deployed to the Pi, lazy-loaded by the site,
fetched-and-cached by pbenguin with an opt-in full-pack download.

## Format decision: animated WebP

One lossy animated WebP per animation (`<combo>__idle.webp`, `__spawn.webp`, `__flourish.webp`),
rendered via plain `<img>` exactly as the locked live-card does today. Rationale:

- Alpha + animation + `<img>` simplicity + support in every modern browser and WebView2.
- WebM/VP9-alpha compresses better but needs `<video>` machinery, has no loop/hold-last-frame
  semantics, and Safari would need a parallel HEVC-alpha encode. Animated AVIF: slow to encode
  at this scale, shakier decode support. **Deferred, not needed** — Paul: modern browsers first,
  Safari-class gaps handled later as exceptions (and animated WebP works in Safari 14+ anyway).
- The card's ink-ring finish is realtime CSS (four 1px `drop-shadow`s) — nothing baked.

## Encode recipe (candidate — gated by the A/B eye test)

Measured on `baby_daisy__base__b_dasher` (Pillow, pure-Python pipeline, no external tools):

| Recipe | idle/spawn/flourish | per combo | full pack (×6,273) |
|---|---|---|---|
| 0.2× 60fps q75 (placeholder-equivalent) | 683/142/357 KB | ~1.16MB | ~7.4GB |
| 0.2× 30fps q60 | 311/67/161 KB | ~540KB | ~3.5GB |
| **0.2× 30fps q60 + 5-bit alpha** | ~218/~47/~113 KB | **~375KB** | **~2.4GB** |
| 0.15× 30fps q60 + 5-bit alpha | — | ~260KB | ~1.7GB |

Pipeline per frame: decode PNG → **premultiply → Lanczos downscale → unpremultiply** (kills
edge fringe from stray RGB under alpha) → **alpha quantize to 5 bits** (snap <6→0, >249→255;
the lossless alpha plane is 38% of bytes at 8-bit; 5-bit cuts total ~31%, diminishing beyond)
→ append to animated WebP, `quality=60, method=4`.

- `method=6`/`minimize_size`/`allow_mixed` measured ≈2% smaller for 50–80× encode time — **use
  method=4**. Quality above ~q60 barely moves size (alpha dominates).
- **Target scale 0.2× → 205×216** = ~2× device pixels at the biggest card render (112px CSS),
  right for hi-DPI. 0.15× is the fallback if it survives the eye test.
- **30fps amends the locked "60fps webp" card decision** — that is exactly what the A/B page
  exists to judge. Nothing batch-encodes until Paul's eye signs off on scale/fps/quality/alpha.
- Loop counts: idle `loop=0` (infinite); spawn + flourish `loop=1` (WebP holds final frame).
- At 30fps every source frame index (incl. `idle_resume`) halves; encoder drops every 2nd frame.

### Seamless handoffs (kart combos)

- **spawn → idle:** spawn's last source frame precedes idle frame 0, and idle is encoded at
  phase 0 → front-end swaps `src` to idle on spawn end; seamless by construction.
- **flourish → idle:** flourish settles into the idle bob at `idle_resume`, but an `<img>` WebP
  cannot start at an offset. Fix at encode time: kart flourish files get an **idle tail** —
  frames `idle[idle_resume..N)` appended (avg ~17 src frames, ~+15KB) so the flourish's final
  frame equals idle frame 0; front-end then swaps to idle.webp, seamless. Chars keep the
  designed hard cut (`idle_resume` unused).
- Front-end detects animation end via manifest `duration_ms` + timer (no `<img>` end event).

## Outputs (one pack dir, all data in one place)

Built to `D:\kartoff\asset_chips\site_pack\` (outside the repo):

1. **`chips/<combo>__<anim>.webp`** — ~19k animations.
2. **`chips/<combo>__<anim>__sil_k{0..3}.png`** — tearout masks per animation, chip-resolution
   RGBA, generated from downsampled alpha of 4 frames sampled across the animation using the
   locked tearout language (12-point radial jagged cut, margins 18–34 srcpx, shared jag seeds
   across the 4 frames — variance = pose only). The original placeholder generator is lost
   (session scratch); recreate to visually match the placeholders in
   `docs/design/site-redesign/sil/`, eyeballed on the A/B page. Budget ~5KB each (~75k files
   ≈ +0.3GB pack on top of the webp numbers above).
3. **`chips/manifest.json`** — per combo: anims present, frame counts, `duration_ms` per anim,
   encoded fps/scale, `idle_resume` (post-halving, informational), `kart` flag. Derived from the
   master manifest; the site and pbenguin read only this.
4. **Shards:** `chips-<char>.tar` per character (~50 shards, ~50MB each; well under the 2GB
   per-asset limit) + `sha256sums.txt`.

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
- **Pi serves** `$DATA/chips/` at `/chips/` (static route in the pi server). Site lazy-loads
  per combo on selection (~375KB for all three anims), browser-cached (immutable — content
  changes only with `chips-vN` bump, so long `max-age` + version in path or lock-driven cache
  bust).

## pbenguin

- **Default:** fetch `/chips/` URLs from thekartoff.com on demand, persistent local cache
  (app data dir) — instant for anything seen before.
- **Opt-in:** "Download full chip pack (~2.4GB)" in settings — pulls shards from the GitHub
  release (same lock), full offline instant. No gigabytes in the installer.
- Detailed pbenguin UI/cache design is its own later plan; this spec fixes the contract
  (URLs, manifest, shards, lock).

## First deliverable: A/B eye-test page

A lab page (pattern: `sharpness-lab.html`) rendering real card-size chips (92/112/76px CSS,
against card-dark background, ink-ring CSS applied) side-by-side across: 60 vs 30fps, q75/60/50,
8 vs 5 vs 4-bit alpha, 0.2× vs 0.15×, using a handful of representative combos (big char, small
char, busy kart, standalone char) encoded by the real pipeline code. **Paul's pick locks the
recipe; only then does the ×6,273 batch run.**

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
