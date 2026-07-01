# Feed Preferences Persistence + Screenshot Button — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the pbenguin monitor's volume/mute, preview-feed, and a new ROI-boxes toggle across launches; make "display off" blank the whole overlay (ROI boxes *and* minimap dots); and add a button that saves a clean 1080p screenshot to `Pictures\pbenguin`.

**Architecture:** Four frontend display prefs move from in-memory `let`s in `App.svelte` into localStorage-backed Svelte stores (`lib/feedSettings.js`), following the existing `syncSettings`/`trailSettings` pattern. A pure `overlayVisibility()` helper in `lib/overlay.js` derives what the overlay canvas draws from the two hide toggles; `FeedOverlay.svelte` applies it. The screenshot is captured in the frontend from the live `<video>` at native resolution and written to disk by a new Rust `save_screenshot` command (Tauri resolves the Pictures dir — no new dependency).

**Tech Stack:** Svelte 4, Vitest (Node env — no `localStorage`, tests inject a fake), Tauri 2 (Rust command via `invoke`), Web Audio (existing gain node).

## Global Constraints

- **No Python/engine changes.** The engine runs `--no-display`; all display is in the Svelte frontend.
- **Persistence = localStorage**, not the SQLite `config` table. Key prefix `mkw.` (matches `trailSettings.js`).
- **No new Cargo dependency and no `tauri.conf.json` capability change** — the Rust command uses only `std::fs` + Tauri's built-in `PathResolver`.
- **Screenshot = clean game feed only** (native 1920×1080, no overlay composited), **auto-saved** to `Pictures\pbenguin\mkw-<YYYYMMDD-HHmmss-SSS>.png` with a brief on-screen confirmation.
- **ROI toggle off (feed shown)** hides only the ROI boxes; the **minimap dots stay**. **Display off** hides both.
- **Frontend tests are pure-helper only** (Vitest under Node): inject a Map-backed `fakeStorage`, mirroring `presence.test.js`. Svelte components have no test harness in this repo — verify them with `npm run check` (svelte-check) + the regression suite + manual smoke.
- **Verification commands:** `npx vitest run <file>` (single file), `npm run test:js` (full frontend suite), `npm run check` (svelte-check), and — for the Rust task — `cargo check` run from `src-tauri/`.
- **Every commit message ends with the trailer** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (shown in each commit step).
- Branch: `feed-prefs-screenshot` (already created off `main`; the spec is committed there).

---

## Task 1: `feedSettings.js` — persisted feed-pref stores

Foundation module: four localStorage-backed stores + the pure parse/load helpers that the tests exercise (the stores themselves can't round-trip through localStorage under Node, so the testable logic is factored out — same approach as `presence.js`'s `readSnapshot(store)`).

**Files:**
- Create: `src/lib/feedSettings.js`
- Test: `src/lib/feedSettings.test.js`

**Interfaces:**
- Produces:
  - `feedVolume` — `writable<number>` in `[0,1]`, default `0.5`
  - `feedMuted`, `feedHidden`, `roiHidden` — `writable<boolean>`, default `false`
  - `parseVolume(raw: string|null, fallback?: number): number` — clamped to `[0,1]`
  - `parseBool(raw: string|null, fallback?: boolean): boolean`
  - `loadFeedPrefs(store: {getItem(k):string|null}): { feedVolume, feedMuted, feedHidden, roiHidden }`

- [ ] **Step 1: Write the failing test**

Create `src/lib/feedSettings.test.js`:

```js
import { describe, it, expect } from "vitest";
import { parseVolume, parseBool, loadFeedPrefs } from "./feedSettings.js";

// A Map-backed fake of the localStorage subset we use (Node has no localStorage).
function fakeStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

describe("parseVolume", () => {
  it("passes valid values and clamps to [0,1]", () => {
    expect(parseVolume("0.5")).toBe(0.5);
    expect(parseVolume("0")).toBe(0);
    expect(parseVolume("1.5")).toBe(1);
    expect(parseVolume("-0.2")).toBe(0);
  });
  it("falls back on null / non-numeric", () => {
    expect(parseVolume(null)).toBe(0.5);
    expect(parseVolume("abc")).toBe(0.5);
    expect(parseVolume(null, 0.8)).toBe(0.8);
  });
});

describe("parseBool", () => {
  it("maps the persisted string form", () => {
    expect(parseBool("true")).toBe(true);
    expect(parseBool("false")).toBe(false);
  });
  it("falls back on anything else", () => {
    expect(parseBool(null)).toBe(false);
    expect(parseBool("1")).toBe(false);
    expect(parseBool(null, true)).toBe(true);
  });
});

describe("loadFeedPrefs", () => {
  it("returns defaults for empty storage", () => {
    expect(loadFeedPrefs(fakeStorage())).toEqual({
      feedVolume: 0.5, feedMuted: false, feedHidden: false, roiHidden: false,
    });
  });
  it("reads persisted values", () => {
    const store = fakeStorage({
      "mkw.feedVolume": "0.25", "mkw.feedMuted": "true",
      "mkw.feedHidden": "true", "mkw.roiHidden": "true",
    });
    expect(loadFeedPrefs(store)).toEqual({
      feedVolume: 0.25, feedMuted: true, feedHidden: true, roiHidden: true,
    });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/feedSettings.test.js`
Expected: FAIL — `Failed to resolve import "./feedSettings.js"` (module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `src/lib/feedSettings.js`:

```js
// Feed display preferences, persisted in localStorage (client-side only — the Python
// engine never reads these). Same pattern as syncSettings.js / trailSettings.js.
import { writable } from "svelte/store";

const KEYS = {
  feedVolume: "mkw.feedVolume",
  feedMuted:  "mkw.feedMuted",
  feedHidden: "mkw.feedHidden",
  roiHidden:  "mkw.roiHidden",
};
const DEFAULTS = { feedVolume: 0.5, feedMuted: false, feedHidden: false, roiHidden: false };

// localStorage is absent under Node (tests). Probe it; fall back to a no-op so the
// module imports cleanly either way.
function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}

/** Parse a 0..1 volume from storage, clamping and falling back to the default. */
export function parseVolume(raw, fallback = DEFAULTS.feedVolume) {
  const n = parseFloat(raw);
  if (!isFinite(n)) return fallback;
  return Math.max(0, Math.min(1, n));
}

/** Parse a persisted boolean ("true"/"false") with a fallback. */
export function parseBool(raw, fallback = false) {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return fallback;
}

/** Load all four feed prefs from a storage object (defaults when absent/invalid). */
export function loadFeedPrefs(store) {
  return {
    feedVolume: parseVolume(store.getItem(KEYS.feedVolume)),
    feedMuted:  parseBool(store.getItem(KEYS.feedMuted),  DEFAULTS.feedMuted),
    feedHidden: parseBool(store.getItem(KEYS.feedHidden), DEFAULTS.feedHidden),
    roiHidden:  parseBool(store.getItem(KEYS.roiHidden),  DEFAULTS.roiHidden),
  };
}

const ls = safeStorage();
const initial = loadFeedPrefs(ls);

export const feedVolume = writable(initial.feedVolume);
export const feedMuted  = writable(initial.feedMuted);
export const feedHidden = writable(initial.feedHidden);
export const roiHidden  = writable(initial.roiHidden);

feedVolume.subscribe((v) => ls.setItem(KEYS.feedVolume, String(parseVolume(v))));
feedMuted.subscribe((v)  => ls.setItem(KEYS.feedMuted,  v ? "true" : "false"));
feedHidden.subscribe((v) => ls.setItem(KEYS.feedHidden, v ? "true" : "false"));
roiHidden.subscribe((v)  => ls.setItem(KEYS.roiHidden,  v ? "true" : "false"));
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/feedSettings.test.js`
Expected: PASS — 3 describe blocks, 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/lib/feedSettings.js src/lib/feedSettings.test.js
git commit -m "feat(feed): persisted feed-pref stores (volume/mute/hide/roi)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `overlayVisibility` helper

Pure helper that turns the two hide toggles into "what to draw". Lives with the overlay drawing code so they stay together.

**Files:**
- Modify: `src/lib/overlay.js` (add one exported function, just above `drawOverlay`)
- Test: `src/lib/overlay.test.js` (new)

**Interfaces:**
- Produces: `overlayVisibility({ hidden: boolean, roiHidden: boolean }): { showRois: boolean, showMinimap: boolean }`

- [ ] **Step 1: Write the failing test**

Create `src/lib/overlay.test.js`:

```js
import { describe, it, expect } from "vitest";
import { overlayVisibility } from "./overlay.js";

describe("overlayVisibility", () => {
  it("shows everything when nothing is hidden", () => {
    expect(overlayVisibility({ hidden: false, roiHidden: false }))
      .toEqual({ showRois: true, showMinimap: true });
  });
  it("ROI-off hides only the boxes; minimap dots stay", () => {
    expect(overlayVisibility({ hidden: false, roiHidden: true }))
      .toEqual({ showRois: false, showMinimap: true });
  });
  it("display-off hides both, regardless of the ROI toggle", () => {
    expect(overlayVisibility({ hidden: true, roiHidden: false }))
      .toEqual({ showRois: false, showMinimap: false });
    expect(overlayVisibility({ hidden: true, roiHidden: true }))
      .toEqual({ showRois: false, showMinimap: false });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/overlay.test.js`
Expected: FAIL — `overlayVisibility is not a function` (not exported yet).

- [ ] **Step 3: Write the implementation**

In `src/lib/overlay.js`, add this function immediately **before** `export function drawOverlay(ctx, opts) {` (around line 188):

```js
/**
 * Decide what the feed overlay should draw, given the two hide toggles.
 * - display off (`hidden`) blanks the whole overlay — ROI boxes AND minimap dots;
 * - the ROI toggle (`roiHidden`) hides only the ROI boxes while the feed is shown.
 * @param {{ hidden: boolean, roiHidden: boolean }} opts
 * @returns {{ showRois: boolean, showMinimap: boolean }}
 */
export function overlayVisibility({ hidden, roiHidden }) {
  return { showRois: !hidden && !roiHidden, showMinimap: !hidden };
}

```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/overlay.test.js`
Expected: PASS — 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/lib/overlay.js src/lib/overlay.test.js
git commit -m "feat(overlay): overlayVisibility helper for the hide toggles" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Persist volume / mute / preview via `App.svelte` store wiring

Replace the three in-memory `let`s with the Task 1 stores so volume, mute, and the preview-feed toggle survive relaunch. No behaviour change beyond persistence yet (overlay-blanking + ROI toggle come in Task 4). This is a pure Svelte-wiring task — no new unit test; verified by `svelte-check`, the regression suite, and manual relaunch.

**Files:**
- Modify: `src/App.svelte` (import; remove 3 `let`s; retarget references to the stores)

**Interfaces:**
- Consumes: `feedVolume`, `feedMuted`, `feedHidden` from `./lib/feedSettings.js` (Task 1).

**Ordering matters** — do the steps in this exact order. The `replace_all` steps run while no import exists yet, so they can't corrupt the import line; the import (with bare names in braces) is added last.

- [ ] **Step 1: Remove the three in-memory `let`s**

In `src/App.svelte`, replace this block (currently ~lines 431–433):

```svelte
  let feedVolume    = 0.5;    // 0–1
  let feedMuted     = false;
  let feedVideoHidden = false;
```

with:

```svelte
  // Persisted feed prefs (volume / mute / preview-hide / ROI-hide) live in lib/feedSettings.js.
```

- [ ] **Step 2: Retarget `feedVideoHidden` → `$feedHidden`**

Replace **all** occurrences of `feedVideoHidden` with `$feedHidden` in `src/App.svelte` (3 sites: the two placeholder conditions and the hide-button `title`/`on:click`/`{#if}`). The token is unique, so a replace-all is safe.

- [ ] **Step 3: Retarget `feedMuted` → `$feedMuted`**

Replace **all** occurrences of `feedMuted` with `$feedMuted` in `src/App.svelte` (audio gain init, the `$:` gain reactive block, the monitor mute button, and the setup-camera mute button).

- [ ] **Step 4: Retarget `feedVolume` → `$feedVolume`**

Replace **all** occurrences of `feedVolume` with `$feedVolume` in `src/App.svelte` (audio gain init, the `$:` gain reactive block, both volume sliders, both `on:input` guards, both `{Math.round(... * 100)}%` labels).

- [ ] **Step 5: Add the import**

In `src/App.svelte`, immediately after the `syncSettings` import (currently line 42):

```svelte
  import { serverUrl as serverUrlStore, authToken as authTokenStore } from "./lib/syncSettings.js";
```

add:

```svelte
  import { feedVolume, feedMuted, feedHidden } from "./lib/feedSettings.js";
```

(Bare names in the braces are correct — this is the import, not a usage.)

- [ ] **Step 6: Sanity-check the retargeted sites**

Confirm these now read as stores (spot-check by searching the file):
- `_setupAudio`: `_gainNode.gain.value = $feedMuted ? 0 : $feedVolume;`
- Reactive: `$: if (_gainNode) _gainNode.gain.value = $feedMuted ? 0 : $feedVolume;`
- Monitor `<FeedOverlay>`: `muted={$feedMuted}` / `volume={$feedVolume}` / `hidden={!cameraOk || $feedHidden}`
- Monitor controls: `bind:value={$feedVolume}`, `on:input={() => { if ($feedVolume > 0) $feedMuted = false; }}`, `{Math.round($feedVolume * 100)}%`, hide button `on:click={() => $feedHidden = !$feedHidden}`
- Setup-camera controls: the same `$feedMuted` / `$feedVolume` forms

There must be **no** remaining bare `feedVolume`/`feedMuted`/`feedVideoHidden` outside the import line.

- [ ] **Step 7: Type-check**

Run: `npm run check`
Expected: 0 errors (the pre-existing warning count is unchanged).

- [ ] **Step 8: Regression suite**

Run: `npm run test:js`
Expected: all frontend tests pass (Task 1 + Task 2 + the pre-existing suites).

- [ ] **Step 9: Commit**

```bash
git add src/App.svelte
git commit -m "feat(feed): persist volume/mute/preview across launches" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Overlay hiding + ROI-boxes toggle

Make display-off blank the whole overlay, add the persisted ROI-boxes toggle, and wire the ROI button into the feed controls. Verified by `svelte-check` + regression + manual smoke (consumes the Task 2 helper, which is unit-tested).

**Files:**
- Modify: `src/components/FeedOverlay.svelte`
- Modify: `src/App.svelte`

**Interfaces:**
- Consumes: `overlayVisibility` (Task 2); `roiHidden` store (Task 1).

- [ ] **Step 1: FeedOverlay — import the helper**

In `src/components/FeedOverlay.svelte`, change the overlay import (line 6):

```svelte
  import { drawOverlay } from "../lib/overlay.js";
```

to:

```svelte
  import { drawOverlay, overlayVisibility } from "../lib/overlay.js";
```

- [ ] **Step 2: FeedOverlay — add the `roiHidden` prop**

After `export let hidden = false;` (line 16), add:

```svelte
  /** @type {boolean} hide just the ROI boxes (minimap dots still draw while shown) */
  export let roiHidden = false;
```

- [ ] **Step 3: FeedOverlay — derive visibility and fold it into `mmActive`**

Replace this line (currently line 119):

```svelte
  $: mmActive = currentScreen === "RACING" && raceFinishTime == null;
```

with:

```svelte
  $: vis = overlayVisibility({ hidden, roiHidden });
  $: mmActive = currentScreen === "RACING" && raceFinishTime == null && vis.showMinimap;
```

- [ ] **Step 4: FeedOverlay — gate the ROI boxes in `redraw()`**

In `redraw()`, change the `rois` argument passed to `drawOverlay` (currently line 180):

```svelte
      rois:          activeRois,
```

to:

```svelte
      rois:          vis.showRois ? activeRois : [],
```

- [ ] **Step 5: FeedOverlay — repaint when the toggles change**

The static-redraw reactive block (currently lines 191–192) lists its dependencies explicitly; add `vis` so toggling ROI/hide repaints even when no race is active. Replace:

```svelte
  $: { void activeRois; void currentMinimap; void currentTrails; void currentLegend; void sampleImg;
       void mmActive; void canvasW; void canvasH; redraw(); }
```

with:

```svelte
  $: { void activeRois; void currentMinimap; void currentTrails; void currentLegend; void sampleImg;
       void mmActive; void vis; void canvasW; void canvasH; redraw(); }
```

- [ ] **Step 6: App — extend the feedSettings import to include `roiHidden`**

In `src/App.svelte`, change the import added in Task 3:

```svelte
  import { feedVolume, feedMuted, feedHidden } from "./lib/feedSettings.js";
```

to:

```svelte
  import { feedVolume, feedMuted, feedHidden, roiHidden } from "./lib/feedSettings.js";
```

- [ ] **Step 7: App — pass `roiHidden` to the monitor `<FeedOverlay>`**

In the monitor view, the `<FeedOverlay>` currently ends with `hidden={!cameraOk || $feedHidden}`. Change:

```svelte
          <FeedOverlay
            stream={setupComplete ? (videoStream ?? null) : null}
            muted={$feedMuted}
            volume={$feedVolume}
            hidden={!cameraOk || $feedHidden}
          />
```

to:

```svelte
          <FeedOverlay
            stream={setupComplete ? (videoStream ?? null) : null}
            muted={$feedMuted}
            volume={$feedVolume}
            hidden={!cameraOk || $feedHidden}
            roiHidden={$roiHidden}
          />
```

- [ ] **Step 8: App — add the ROI toggle button**

In the monitor `.feed-controls`, the hide button block ends with `</button>` (currently ~line 1647). Insert the ROI toggle **after** that closing `</button>` and before the `</div>` that closes `.feed-controls`:

```svelte
          <button class="fc-btn fc-vid-btn" title={$feedHidden ? "ROI hidden while feed is off" : ($roiHidden ? "Show ROI boxes" : "Hide ROI boxes")}
            disabled={$feedHidden}
            on:click={() => $roiHidden = !$roiHidden}>
            {#if $roiHidden || $feedHidden}
              <svg viewBox="0 0 16 16" class="fc-icon"><rect x="2.5" y="4.5" width="11" height="7" rx="1" stroke-dasharray="2.2 2"/><line x1="2" y1="2" x2="14" y2="14"/></svg>
            {:else}
              <svg viewBox="0 0 16 16" class="fc-icon"><rect x="2.5" y="4.5" width="11" height="7" rx="1"/></svg>
            {/if}
            <span class="fc-vid-label">ROI</span>
          </button>
```

- [ ] **Step 9: App — add a disabled-button style**

In the `<style>` block, just after the `.fc-btn:hover` rule (currently line 2055), add:

```svelte
  .fc-btn:disabled { opacity: .35; cursor: default; }
  .fc-btn:disabled:hover { color: var(--tx-dim); background: transparent; }
```

- [ ] **Step 10: Type-check**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 11: Regression suite**

Run: `npm run test:js`
Expected: all pass.

- [ ] **Step 12: Manual smoke**

Run the app (`npm run tauri dev`) with a live feed:
- During a race, toggle **ROI**: the ROI boxes vanish/return; the minimap tracking dot/marker stays visible.
- Toggle **Hide** (display off): the video, ROI boxes **and** minimap dots all disappear; the ROI button greys out (disabled).
- Toggle Hide back on: overlay returns, respecting the current ROI state.

- [ ] **Step 13: Commit**

```bash
git add src/components/FeedOverlay.svelte src/App.svelte
git commit -m "feat(feed): ROI-boxes toggle + display-off blanks the whole overlay" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Screenshot button + Rust `save_screenshot` command

Capture the live `<video>` at native resolution to a PNG and save it to `Pictures\pbenguin`. Verified by `cargo check` (command compiles), `svelte-check`, and manual capture.

**Files:**
- Modify: `src/components/FeedOverlay.svelte` (add `capturePng()`)
- Modify: `src/App.svelte` (bind the component; add the button, handler, confirmation)
- Modify: `src-tauri/src/lib.rs` (add + register `save_screenshot`)

**Interfaces:**
- Produces (FeedOverlay): `export async function capturePng(): Promise<Uint8Array|null>` — PNG bytes of the current frame, or `null` if no frame is available.
- Produces (Rust): `save_screenshot(app, bytes: Vec<u8>, stamp: String) -> Result<String, String>` — returns the full path written.
- Consumes: `invoke` from `@tauri-apps/api/core` (already imported in `App.svelte`).

- [ ] **Step 1: FeedOverlay — add `capturePng()`**

In `src/components/FeedOverlay.svelte`, inside `<script>`, add this exported function (e.g. immediately after the `redraw()` function, before the ResizeObserver `onMount`):

```svelte
  // ── Screenshot: draw the live <video> at native resolution → PNG bytes ─────────
  // Clean feed only (no overlay). A local getUserMedia stream is same-origin, so the
  // capture canvas is not tainted and toBlob works. Returns null if no frame yet.
  export async function capturePng() {
    if (!videoEl || !videoEl.videoWidth || !videoEl.videoHeight) return null;
    const cap = document.createElement("canvas");
    cap.width  = videoEl.videoWidth;
    cap.height = videoEl.videoHeight;
    const cctx = cap.getContext("2d");
    if (!cctx) return null;
    cctx.drawImage(videoEl, 0, 0, cap.width, cap.height);
    const blob = await new Promise((res) => cap.toBlob(res, "image/png"));
    if (!blob) return null;
    return new Uint8Array(await blob.arrayBuffer());
  }
```

- [ ] **Step 2: App — component ref + screenshot state**

In `src/App.svelte`, just after the audio state declarations (the block ending `let _hasAudio = false;`, ~line 436), add:

```svelte
  // ── Screenshot ────────────────────────────────────────────────────────────────
  let feedOverlayComp = null;   // bound <FeedOverlay> instance (for capturePng)
  let shotMsg = "";             // transient confirmation text
  let shotErr = false;          // colour the confirmation as an error
  let _shotTimer = null;
```

- [ ] **Step 3: App — screenshot handler**

Add these functions near the other feed helpers (e.g. right after the `_teardownAudio` function, ~line 458). `new Date()` is the real browser Date here (this is app runtime, not a workflow script):

```svelte
  function _pad(n, w = 2) { return String(n).padStart(w, "0"); }
  function _shotStamp() {
    const d = new Date();
    return `${d.getFullYear()}${_pad(d.getMonth() + 1)}${_pad(d.getDate())}-`
         + `${_pad(d.getHours())}${_pad(d.getMinutes())}${_pad(d.getSeconds())}-${_pad(d.getMilliseconds(), 3)}`;
  }
  function _flashShot(msg, err = false) {
    shotMsg = msg; shotErr = err;
    if (_shotTimer) clearTimeout(_shotTimer);
    _shotTimer = setTimeout(() => { shotMsg = ""; }, 3000);
  }
  async function takeScreenshot() {
    try {
      const bytes = await feedOverlayComp?.capturePng();
      if (!bytes) { _flashShot("No feed to capture", true); return; }
      const path = await invoke("save_screenshot", { bytes: Array.from(bytes), stamp: _shotStamp() });
      _flashShot("Saved → " + path);
    } catch (e) {
      _flashShot("Screenshot failed", true);
    }
  }
```

- [ ] **Step 4: App — bind the FeedOverlay instance**

Add `bind:this={feedOverlayComp}` to the monitor `<FeedOverlay>` (the same element edited in Task 4):

```svelte
          <FeedOverlay
            bind:this={feedOverlayComp}
            stream={setupComplete ? (videoStream ?? null) : null}
            muted={$feedMuted}
            volume={$feedVolume}
            hidden={!cameraOk || $feedHidden}
            roiHidden={$roiHidden}
          />
```

- [ ] **Step 5: App — add the screenshot button + confirmation**

In the monitor `.feed-controls`, after the ROI toggle button's `</button>` (from Task 4, Step 8) and before the `</div>` closing `.feed-controls`, add:

```svelte
          <div class="fc-divider"></div>
          <button class="fc-btn fc-vid-btn" title="Save screenshot" disabled={!cameraOk}
            on:click={takeScreenshot}>
            <svg viewBox="0 0 16 16" class="fc-icon"><path d="M2 5h3l1-1.5h4l1 1.5h3v8H2z"/><circle cx="8" cy="9" r="2.5"/></svg>
            <span class="fc-vid-label">Shot</span>
          </button>
          {#if shotMsg}
            <span class="fc-shot-msg" class:fc-shot-err={shotErr} title={shotMsg}>{shotMsg}</span>
          {/if}
```

- [ ] **Step 6: App — confirmation style**

In the `<style>` block, after the `.fc-vid-label` rule (currently line 2071), add:

```svelte
  .fc-shot-msg {
    font-size: .6rem; color: var(--tx-dim); flex: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left;
  }
  .fc-shot-err { color: var(--err); }
```

(`direction: rtl` keeps the filename visible when the path is too long to fit.)

- [ ] **Step 7: App — clear the timer on teardown**

In the existing `onDestroy` (currently ~line 1414), add the timer cleanup:

```svelte
  onDestroy(()=>{
    if (unlisten) unlisten();
    stopCamera(); stopRoiPoll(); stopFeedPoll(); _teardownAudio();
    if (_shotTimer) clearTimeout(_shotTimer);
    if (trackerCameraPaused) send({type:"resume_camera"});
  });
```

- [ ] **Step 8: Rust — add the `save_screenshot` command**

In `src-tauri/src/lib.rs`, add this function after the `send_to_tracker` command (~line 187, before `pub fn run()`):

```rust
/// Save a PNG screenshot (raw bytes from the frontend canvas) into the user's
/// Pictures\pbenguin folder. Returns the full path written.
#[tauri::command]
fn save_screenshot(app: tauri::AppHandle, bytes: Vec<u8>, stamp: String) -> Result<String, String> {
    let dir = app
        .path()
        .picture_dir()
        .map_err(|e| e.to_string())?
        .join("pbenguin");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("mkw-{stamp}.png"));
    std::fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}
```

(`app.path()` uses the `tauri::Manager` trait, already imported at line 5. No new dependency.)

- [ ] **Step 9: Rust — register the command**

In the `invoke_handler` list (currently line 201), add `save_screenshot`. Change:

```rust
        .invoke_handler(tauri::generate_handler![start_tracker, stop_tracker, restart_tracker, send_to_tracker, open_url, discord::discord_set_presence, discord::discord_clear_presence, sync::sync_set_config, sync::sync_test_connection, sync::sync_resolve_pending, sync::sync_discard_pending, sync::sync_list_pending, sync::sync_course_reads, sync::sync_roster, sync::sync_pb_best])
```

to (append `, save_screenshot` before the closing `]`):

```rust
        .invoke_handler(tauri::generate_handler![start_tracker, stop_tracker, restart_tracker, send_to_tracker, open_url, save_screenshot, discord::discord_set_presence, discord::discord_clear_presence, sync::sync_set_config, sync::sync_test_connection, sync::sync_resolve_pending, sync::sync_discard_pending, sync::sync_list_pending, sync::sync_course_reads, sync::sync_roster, sync::sync_pb_best])
```

- [ ] **Step 10: Rust — compile check**

Run from `src-tauri/`: `cargo check`
Expected: compiles clean (no errors from the new command).

- [ ] **Step 11: Type-check the frontend**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 12: Regression suite**

Run: `npm run test:js`
Expected: all pass.

- [ ] **Step 13: Manual smoke**

Run the app (`npm run tauri dev`) with a live feed:
- Click **Shot**: a confirmation `Saved → …\Pictures\pbenguin\mkw-<stamp>.png` appears for ~3s.
- Open the file: it is a clean **1920×1080** PNG of the game feed with **no** ROI boxes or dots.
- Click Shot again within the same second: a second distinct file is written (millisecond suffix differs).
- Relaunch the app and confirm volume, mute, the preview toggle, and the ROI toggle all restored to their last values.

- [ ] **Step 14: Commit**

```bash
git add src/components/FeedOverlay.svelte src/App.svelte src-tauri/src/lib.rs
git commit -m "feat(feed): screenshot button saving clean 1080p PNG to Pictures\\pbenguin" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Done-when

- Volume, mute, preview-feed, and ROI-boxes toggles persist across app relaunch (localStorage).
- Display off hides the video, the ROI boxes, and the minimap replay dots.
- ROI off (feed shown) hides only the ROI boxes; minimap dots remain.
- The screenshot button writes a clean 1920×1080 PNG to `Pictures\pbenguin` with an on-screen confirmation.
- `npm run test:js`, `npm run check`, and `cargo check` (in `src-tauri/`) all pass.
