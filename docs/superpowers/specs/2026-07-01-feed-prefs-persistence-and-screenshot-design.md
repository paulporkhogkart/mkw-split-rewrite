# Feed preferences persistence + screenshot button

**Date:** 2026-07-01
**Status:** Approved (design)
**Scope:** Tauri frontend (`src/`) + one Rust command (`src-tauri/`). No Python/engine changes.

## Problem

Two unrelated pbenguin monitor tweaks:

1. **Persist feed display preferences across launches.** Volume/mute, the preview-feed
   show/hide toggle, and a *new* ROI-boxes show/hide toggle currently either reset every
   launch or don't exist. They should be remembered. Additionally, hiding the preview feed
   ("display off") must hide **all** overlay content — the ROI boxes *and* the minimap
   replay dots — not just the video.
2. **A screenshot button** that saves a high-quality capture of the game feed.

## Current state (as found)

- `feedVolume` (0–1, default 0.5), `feedMuted` (bool), `feedVideoHidden` (bool) are plain
  in-memory `let`s in `src/App.svelte` (~lines 431–433). They reset to defaults every launch.
- Volume/mute drive a Web Audio `GainNode` (`_setupAudio` / the `$: _gainNode.gain.value`
  reactive block). The `<video>` element itself stays muted to avoid double-audio.
- `feedVideoHidden` is passed to `FeedOverlay` as `hidden`, but `hidden` only applies
  `display:none` to the `<video>`. **The overlay `<canvas>` (ROI boxes + minimap dots) draws
  regardless**, so today "Hide feed" leaves the overlay floating over the placeholder.
- The overlay is drawn by the pure `drawOverlay(ctx, opts)` in `src/lib/overlay.js`; the
  minimap portion is gated by `mmActive = currentScreen === "RACING" && raceFinishTime == null`.
- Frontend↔Rust uses Tauri `invoke()` commands (see `src/lib/ipc.js`, `src-tauri/src/lib.rs`).
  The engine is spawned with `--no-display`, so all display is in the Svelte frontend.
- Established persistence pattern for frontend-only display prefs: a `writable` store backed
  by `localStorage` with a `safeStorage()` probe (`src/lib/syncSettings.js`,
  `trailSettings.js`, `cardSettings.js`). localStorage in the Tauri webview persists to disk.

## Approach

Persist the four values with the existing **localStorage-store pattern**, not the SQLite
`config` table — they are pure frontend display prefs the Python engine never reads. The
screenshot save is a new **Rust `invoke` command** that resolves the Pictures directory
(Tauri's `PathResolver::picture_dir()`, no new dependency) and writes the PNG.

Decisions locked during brainstorming:

- Screenshot captures the **clean game feed only** (native 1920×1080, no overlay composited).
- Screenshot **auto-saves** to `Pictures\pbenguin\mkw-<stamp>.png` with a brief on-screen
  confirmation (no Save dialog).
- ROI toggle **off** (with display on) hides the ROI boxes but **leaves the minimap replay
  dots visible**. Only **display off** hides the minimap dots.
- The screenshot button stays **enabled while the feed is hidden** (captures the live frame).

## Components

### 1. `src/lib/feedSettings.js` (new)

Four localStorage-backed `writable` stores, each auto-persisting on change via `.subscribe`,
using the same `safeStorage()` probe as the sibling settings modules. `mkw.` key prefix
(matching the newest module, `trailSettings.js`).

| Export | Type | localStorage key | Default | Parse-on-load |
|---|---|---|---|---|
| `feedVolume` | number 0–1 | `mkw.feedVolume` | `0.5` | `parseFloat`, clamp `[0,1]`, fallback default |
| `feedMuted` | bool | `mkw.feedMuted` | `false` | `=== "true"` |
| `feedHidden` | bool | `mkw.feedHidden` | `false` | `=== "true"` |
| `roiHidden` | bool | `mkw.roiHidden` | `false` | `=== "true"` |

`feedHidden` is the renamed successor of the old in-component `feedVideoHidden`.

### 2. `src/App.svelte` (edit)

- Remove the three in-memory `let feedVolume / feedMuted / feedVideoHidden`.
- `import { feedVolume, feedMuted, feedHidden, roiHidden } from "./lib/feedSettings.js";`
- Replace every reference with the `$`-prefixed store form. Sites (from grep):
  - Audio: `_setupAudio` init (`$feedMuted ? 0 : $feedVolume`) and the reactive
    `$: if (_gainNode) _gainNode.gain.value = $feedMuted ? 0 : $feedVolume;`.
  - Monitor `FeedOverlay` props: `muted={$feedMuted}`, `volume={$feedVolume}`,
    `hidden={!cameraOk || $feedHidden}`, **new** `roiHidden={$roiHidden}`.
  - Placeholder conditions: `{#if !cameraOk || $feedHidden}` / `{#if $feedHidden && cameraOk}`.
  - Monitor controls: mute `$feedMuted = !$feedMuted`; slider `bind:value={$feedVolume}` +
    `on:input={() => { if ($feedVolume > 0) $feedMuted = false; }}`; `{Math.round($feedVolume*100)}%`;
    hide `$feedHidden = !$feedHidden`.
  - Setup-camera audio monitor: same `$feedMuted`/`$feedVolume` substitutions (persisting the
    audio-monitor volume is desirable).
- `bind:this={feedOverlayComp}` on the monitor `<FeedOverlay>` (for `capturePng()`).
- New feed-controls buttons (see §4) + screenshot handler + confirmation state (see §5).

Svelte 4 supports `bind:value={$store}` (range inputs coerce to number) and `$store = …`
assignment (calls `.set`), so the persistence is transparent to the existing markup.

### 3. Overlay hiding — `src/lib/overlay.js` + `src/components/FeedOverlay.svelte`

New pure, unit-testable helper in `overlay.js`:

```js
export function overlayVisibility({ hidden, roiHidden }) {
  return { showRois: !hidden && !roiHidden, showMinimap: !hidden };
}
```

`FeedOverlay.svelte`:
- New prop `export let roiHidden = false;`
- `$: vis = overlayVisibility({ hidden, roiHidden });`
- Fold `!hidden` into the minimap gate:
  `$: mmActive = currentScreen === "RACING" && raceFinishTime == null && vis.showMinimap;`
  → when hidden, `mmActive` is false, the rAF loop stops, and minimap/trails/sample are not drawn.
- In `redraw()`, pass `rois: vis.showRois ? activeRois : []` to `drawOverlay`.
- **Add `vis` (or `hidden`/`roiHidden`) to the static-redraw reactive block's dependency
  list** (the `$: { void activeRois; … redraw(); }` block). Otherwise toggling ROI/hide while
  no race is active (`mmActive` already false) would not repaint until an unrelated dependency
  changed, leaving stale boxes on screen.
- Result:
  - **display off** → `showMinimap=false` + `showRois=false` → `drawOverlay` clears and draws
    nothing (video already `display:none`). Whole overlay blank.
  - **display on, ROI off** → `showMinimap=true`, `showRois=false` → minimap dots draw, ROI
    boxes suppressed.
  - **display on, ROI on** → everything draws (unchanged behaviour).

`overlay.js`'s `drawOverlay` needs no change (it clears then draws whatever inputs it's given).

### 4. Feed-controls UI (monitor `.feed-controls` bar only)

Two new `.fc-btn`s in the existing inline-SVG icon style, after the Hide button:

- **ROI toggle** — box glyph; tooltip `"Hide ROI boxes"` / `"Show ROI boxes"`;
  `on:click={() => $roiHidden = !$roiHidden}`. **Disabled/dimmed while `$feedHidden`** (display
  off already suppresses ROI), tooltip noting so.
- **Screenshot** — camera/aperture glyph; tooltip `"Save screenshot"`; `on:click={takeScreenshot}`.
  Enabled whenever `cameraOk` (captures the live feed even if hidden).

A small **ephemeral confirmation** element near the controls: `shotMsg` string set on
success (`"Saved → …\pbenguin\mkw-….png"`) or failure (`"Screenshot failed"`, error-coloured),
cleared by a ~3s `setTimeout`.

These additions are **monitor-only** — the setup-camera audio monitor bar keeps just the
audio controls.

### 5. Screenshot capture + save

`FeedOverlay.svelte` — `export async function capturePng()`:
1. Guard `videoEl && videoEl.videoWidth > 0` (else return `null`).
2. Offscreen `canvas` at `videoEl.videoWidth × videoEl.videoHeight` (native 1920×1080).
3. `ctx.drawImage(videoEl, 0, 0, w, h)` — the current live frame. A local getUserMedia stream
   is same-origin, so the canvas is **not tainted** and `toBlob` works.
4. `await new Promise(res => canvas.toBlob(res, "image/png"))` → `Uint8Array` of the blob.
   Clean feed only — no overlay composited.

`App.svelte` — `takeScreenshot()`:
1. `const bytes = await feedOverlayComp.capturePng();` — null → failure confirmation.
2. Build stamp `YYYYMMDD-HHmmss-SSS` from `new Date()` (real browser runtime; not a workflow script).
3. `const path = await invoke("save_screenshot", { bytes: Array.from(bytes), stamp });`
4. success → `shotMsg = "Saved → " + path`; `catch` → failure confirmation.

Marshalling: `Array.from(Uint8Array)` → Rust `Vec<u8>`. A screenshot is an infrequent manual
action, so the JSON-array cost is acceptable; can be optimised later if needed.

### 6. Rust `save_screenshot` — `src-tauri/src/lib.rs`

```rust
#[tauri::command]
fn save_screenshot(app: tauri::AppHandle, bytes: Vec<u8>, stamp: String) -> Result<String, String> {
    let dir = app.path().picture_dir().map_err(|e| e.to_string())?.join("pbenguin");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("mkw-{stamp}.png"));
    std::fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}
```

- `app.path()` needs the already-imported `tauri::Manager` trait.
- Register `save_screenshot` in the `invoke_handler![…]` list.
- The `-SSS` millisecond suffix in the stamp makes same-second filename collisions effectively
  impossible.
- **No new Cargo dependency; no `tauri.conf.json` capability change** (Rust-side FS needs no ACL).

## Testing

- `src/lib/feedSettings.test.js` — defaults when storage empty; set-store → localStorage
  updated (round-trip), with a localStorage stub, mirroring the other `lib` tests.
- `src/lib/overlay.test.js` — `overlayVisibility` truth table (all 4 `hidden`×`roiHidden` combos).
- **Manual:** screenshot lands in `Pictures\pbenguin` and opens as a clean 1080p PNG;
  volume/mute + both toggles survive an app relaunch; display-off blanks ROI *and* minimap
  dots; ROI-off (display on) hides only the boxes.
- Regression: `npm run test:js`, `npm run check`, `cargo build` (Rust command compiles).

## Non-goals

- No Python/engine changes.
- No SQLite `config` persistence for these prefs.
- No keyboard shortcuts.
- Screenshot is clean-feed only — no overlay-burned-in or whole-window variants.
