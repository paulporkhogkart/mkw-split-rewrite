# Territory popup end-frame + PB-finish fire — design

**Status:** Built (2026-06-23); pending user live visual smoke. Change A uses the **union
bbox** (see below) — switched from the end-frame bbox during the build so the user can
compare the framing live.

Two independent, small changes that align the territory-map popup GIFs and the
player-card fire effect with the picked-figure pipeline:

- **A — Popup GIFs stop on the picked pose.** The territory hover popup's leader
  GIF should animate its lead-in and then freeze on the *same* frame the player
  card uses, framed (cropped/centred) the same way.
- **B — Fire survives a PB finish.** A card that earns a PB should keep (or be
  forced into) its "on fire" state — figure + flames — through the post-race
  window, instead of extinguishing the instant the race ends. Applies to both the
  Tauri monitor and the static website live page.

## Background

- The card's on-pace / online portraits are picked frames: `assets/player_figures.json`
  pins `(gif, frame)` per player per state (e.g. paul online = `paulPosted.gif` #66,
  onpace = `paulSitDown.gif` #63). `scripts/gen_player_figures.py:extract()` seeks
  that frame, **crops to the alpha bbox**, caps height ≤260px → `src/assets/players/<name>__{on,off,onpace}.png`.
  The card renders it `background-size: auto 100%; background-position: bottom center`
  in a 56px strip — the bbox crop is what makes the pose sit tight and centred.
- The popup GIFs are built by `scripts/bundle_web_player_gifs.py`: for each player it
  takes the **online** source gif → `web/public/players/<name>.gif` and the **onpace**
  source gif → `<name>__fire.gif`, and its only transform is a byte-level strip of the
  NETSCAPE loop block so the gif plays once. A play-once gif freezes on its **last
  frame (#98)** — not the picked frame — and keeps the **full uncropped 360×360 frame**,
  so the figure floats with transparent padding instead of being framed like the card.
- The popup component `web/src/CoursePopup.svelte` already height-locks + bottom-centres
  the gif (`.fig { height:100%; bottom:0; left:50%; transform:translateX(-50%) }`) in a
  64px strip (3px spine + 56px figure column), and renders shared `src/components/Fire.svelte`
  flames when the course is on-fire. `web/src/lib/courseData.js` chooses `<name>.gif`
  vs `<name>__fire.gif` per the stateless `fireModel.js`; the map spins a fresh object
  URL per open so the gif replays.
- The website live page (`web/src/CardWall.svelte`) imports the **same**
  `src/components/PlayerCard.svelte`, feeding it entries from the shared `/v1/presence`
  store with the same `now` clock as `PlayerPanel.svelte`. Fire already renders on the
  site during racing (the `{#if isRacing}<Fire/>` path is shared code), and finished
  cards already render there (final time, `fin` badge, PB delta). So a fix in
  `PlayerCard.svelte` reaches both surfaces from one edit.

## Goals / non-goals

- **Goal A:** popup gif animates the lead-in, then rests on the manifest's picked
  frame, framed like the card.
- **Goal B:** fire (on-pace figure + flames) is kept/forced on a card from the moment
  it finishes a PB run until the next race starts or it drops to a real menu — on the
  Tauri monitor *and* the website.
- **Non-goals:** no change to which frame is picked (the manifest is authoritative),
  the on-fire *course* model (`fireModel.js`), the fire *visual* (`Fire.svelte`), the
  racing-pace fire trigger (`fireState.js`), or the popup layout/leaderboard.

## Change A — popup gif trim + crop

Rework `scripts/bundle_web_player_gifs.py` from "strip loop, keep all frames" to a
Pillow re-encode that, per player, for each of `(online → <name>.gif)` and
`(onpace → <name>__fire.gif)`:

1. Read the picked frame index for that state from `assets/player_figures.json`
   (reuse the same manifest `gen_player_figures.py` reads; the onpace state falls back
   to online when unset, matching the current bundler).
2. Compute the **union of every retained frame's alpha bbox** (frames `0..picked`),
   skipping fully-transparent frames.
3. Keep frames `0..picked` (the lead-in), crop **every** retained frame to that one union
   window, preserving per-frame durations.
4. Write a **play-once** gif (no NETSCAPE loop block) so the browser animates once and
   rests on the picked pose.

Result: the popup figure animates in and freezes on the same well-framed pose as the
card. `CoursePopup.svelte` needs no change — its existing height-lock + bottom-centre
now frames a tight crop. Trimming also shrinks the committed gifs.

Re-run the script to regenerate the 10 committed `web/public/players/*.gif`. Source
gifs live in `assets/player_gifs/` (Git LFS, present locally).

**Trade-offs / risks:**
- Cropping forces a re-encode, so palettes are re-quantized vs. today's lossless byte
  strip. GIFs are 256-colour anyway; preserve quality by adopting the source frame's
  own palette per frame (`im.convert("RGBA")` → crop → quantize), and eyeball the
  result. Acceptable, but a real difference from "frames untouched".
- Crop window choice: the **end-frame bbox** frames the final pose exactly like the card,
  but clips any lead-in frame where the figure is larger/higher than the end pose. The
  **union bbox** (chosen) never clips, at the cost of the end pose sitting smaller and
  possibly floating above the strip bottom where the figure travels through the lead-in.
  Pillow also coalesces consecutive identical frames on save (harmless: durations are
  summed, the end pose is preserved). Final framing judged visually by the user; trivially
  reversible to the end-frame bbox in `trim_and_crop` if the union reads worse.

## Change B — fire through a PB finish

In `src/components/PlayerCard.svelte`, the on-fire visuals currently revert the instant
`isRacing` goes false at the finish: the figure falls back to the online pose
(`fig = onFire ? onpace : online`, `onFire` false), `fireState.js` deletes the latch
when `racing` is false, and the flames unmount (`{#if isRacing}<Fire/>`).

`viewModel` already exposes the exact "this run was a PB" signal: `vm.finPb`
(`src/lib/playerCard.js`) is true when a run **finished and beat (or first-ever-set)
the pre-race PB**, and it is persisted in the per-player hold so it survives the
post-race pause/reset loaders (`HOLD_SCREENS`) and clears when the next race starts or
a real menu drops the hold. That window is exactly "finished, not racing yet, not back
in menus".

Add a derived latch and feed it into the figure + flame render:

```js
$: forceFire = vm.state === "finished" && vm.finPb;
$: fig = (onFire || forceFire) ? (onpaceFigure(vm.name) || figureFor(vm.name, true))
                               : figureFor(vm.name, vm.online);
```
```svelte
{#if isRacing || forceFire}<Fire color={vm.color} active={onFire || forceFire} />{/if}
```

During the forced window `onFire` is false (racing latch cleared), so `active` is
`forceFire` and the flames burn steadily on their own rAF (independent of the card's
`now`). Non-PB finishes and DNFs (`finPb` false) are untouched — they extinguish as
today. Because `CardWall.svelte` renders the same component with the same presence
entry shape, the website live page inherits this with no web-side change.

## Files

- `scripts/bundle_web_player_gifs.py` — trim-to-picked-frame + crop-to-bbox re-encode.
- `web/public/players/*.gif` — 10 regenerated committed assets.
- `src/components/PlayerCard.svelte` — `forceFire` latch on `finished && finPb`; figure
  + Fire render.

## Verification

- **A:** `tests/test_bundle_web_player_gifs.py` (6 cases: union-bbox trim, clamp, play-once
  encode, transparency, frame coalescing, committed-asset invariant). Confirmed the output's
  final frame matches the source's picked frame (mean RGB diff 1.6–2.0/255, quantization
  only). Live: load the site and hover courses for all five players, default + on-fire
  leaders, and confirm each gif animates then rests on the framed pose (in a real browser).
- **B:** `vitest` + `svelte-check` green (logic is in the already-tested view model;
  add a `forceFire`-shaped assertion if a card-level test fits). Live smoke: a real PB
  run holds fire through the finish + pause on the monitor; the same holds on the
  website live page. A non-PB finish still extinguishes.
