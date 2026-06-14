# First-time Setup Audio Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play the capture feed's audio on the first-time setup camera step, with a mute/volume control, so the user can confirm the selected audio device — scoped to that step only, leaving the re-run settings modal untouched.

**Architecture:** Reuse the existing single Web Audio gain node in `src/App.svelte` (`_setupAudio`/`_teardownAudio`/`_gainNode`). Relax the `setupComplete`-only guard to also allow the first-time camera step, bound audio's lifetime to that step via explicit start/stop hooks in `goStep`, and reuse the monitor's `.feed-controls` markup/styles for the control. No new audio path, no new config, no changes to the monitor or `SettingsModal`.

**Tech Stack:** Svelte 4 (single-file `App.svelte`), Web Audio API, Vite. Spec: `docs/superpowers/specs/2026-06-14-setup-audio-playback-design.md`.

**Note on testing:** This is frontend-only, audio-device-dependent UI behavior with no pure logic to unit-test in isolation (confirmed in the spec's Testing section). The automated gate for each task is `npm run check` (svelte-check, must stay **0 errors / 0 warnings**); the final task runs `npm run build` and lists the manual in-app verification checklist. There are intentionally no `*.test.*` files for this change — substituting fake unit tests for audio playback would add noise without coverage.

---

### Task 1: Relax the `_setupAudio()` guard to allow the first-time camera step

**Files:**
- Modify: `src/App.svelte` (function `_setupAudio`, ~line 427)

- [ ] **Step 1: Apply the edit**

Find this exact block:

```js
  function _setupAudio() {
    _teardownAudio();
    if (!videoStream) return;
    if (!setupComplete) return;
```

Replace it with:

```js
  function _setupAudio() {
    _teardownAudio();
    if (!videoStream) return;
    // Audio is allowed in the monitor (setupComplete) and on the first-time setup
    // camera step, so the user can verify the selected audio device. It stays
    // silent on the other setup steps (language / sync).
    const onSetupCamStep = appView === "setup" && wizardStep === "camera";
    if (!setupComplete && !onSetupCamStep) return;
```

(`appView === "setup"` already implies `!setupComplete`, so the condition reads: allow when in the monitor, or when on the first-time camera step.) Leave the rest of the function — the `_hasAudio` assignment, the `AudioContext`/`createGain`/`connect` calls — unchanged.

- [ ] **Step 2: Verify it compiles cleanly**

Run: `npm run check`
Expected: `svelte-check ... 0 errors, 0 warnings` (no new diagnostics referencing `App.svelte`).

- [ ] **Step 3: Commit**

```bash
git add src/App.svelte
git commit -m "feat(setup): allow feed audio on the first-time camera step

Relax the _setupAudio() guard from setupComplete-only to also permit
appView===\"setup\" && wizardStep===\"camera\". startCamera()'s tail call now
connects audio when the setup camera stream opens / on audio-device change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Bound audio to the camera step with start/stop hooks in `goStep`

**Files:**
- Modify: `src/App.svelte` (function `goStep`, ~line 1180)

**Why:** With Task 1's guard relaxed, `startCamera()`'s tail call to `_setupAudio()` covers the *first* arrival on the camera step and every audio-device change. Two gaps remain: (a) *re-entering* the camera step when the stream is already live does not re-run `startCamera()` (it is guarded by `pythonCameraStatus !== "ok"`), so audio would not reconnect; (b) the stream keeps running on the `language`/`sync` steps, so audio must be explicitly torn down on leaving the camera step (it is not, since `stopCamera()` is not called). This task adds both.

- [ ] **Step 1: Apply the edit**

Find the end of the `goStep` function — this exact block (the `if (step==="camera")` block followed by the function's closing brace):

```js
      // Re-run setup: browser camera is already live from the main feed;
      // only restart it if it stopped.
      if (setupComplete && cameraStatus==="idle") startCamera(selectedBrowserDeviceId||undefined);
    }
  }
```

Replace it with (adds the audio block before the final `}` of the function):

```js
      // Re-run setup: browser camera is already live from the main feed;
      // only restart it if it stopped.
      if (setupComplete && cameraStatus==="idle") startCamera(selectedBrowserDeviceId||undefined);
    }
    // First-time setup: the feed's audio plays only while the camera step is the
    // active tab (so the user can verify the selected audio device). Other steps
    // keep the stream running but silent. Re-run setup is unaffected — its audio
    // comes from the background monitor. _setupAudio() is idempotent and is a
    // no-op until the stream opens, so the first arrival (stream still opening)
    // safely falls through to startCamera()'s tail call, while a re-entry with a
    // live stream reconnects here.
    if (!setupComplete) {
      if (step==="camera") _setupAudio();
      else                 _teardownAudio();
    }
  }
```

- [ ] **Step 2: Verify it compiles cleanly**

Run: `npm run check`
Expected: `0 errors, 0 warnings`.

- [ ] **Step 3: Commit**

```bash
git add src/App.svelte
git commit -m "feat(setup): start/stop feed audio on camera-step entry/leave

goStep now calls _setupAudio() when (re)entering the first-time camera step and
_teardownAudio() when leaving it, so audio is silenced on the language/sync
steps even though the stream keeps running.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Add the mute/volume control under the setup camera preview

**Files:**
- Modify: `src/App.svelte` (camera-step markup in the `appView === "setup"` block, ~line 1644)

**Why:** Reuse the monitor's existing `.feed-controls` markup and styles (defined in `App.svelte`'s `<style>`, ~line 1937), audio-only (drop the monitor's video-hide button and the `.fc-divider` that separated them). Placing it in the setup markup — not in the shared `DeviceSelectors` component — keeps `DeviceSelectors` and the re-run `SettingsModal` untouched. Bound to the existing `feedMuted`/`feedVolume`; shows `"no audio"` when no audio track is present (no device picked yet, or `"none"`), mirroring the monitor.

- [ ] **Step 1: Apply the edit**

Find this exact block (the close of `<SourceCheck>` followed by the blank line and `<div class="cam-below">`):

```svelte
            />

            <div class="cam-below">
```

Replace it with:

```svelte
            />

            <!-- Audio monitor: play back the selected audio device so the user can
                 confirm it's the right one. Reuses the monitor's .feed-controls
                 (audio-only — no video-hide toggle here). -->
            <div class="feed-controls">
              {#if _hasAudio}
                <button class="fc-btn" title={feedMuted ? "Unmute" : "Mute"}
                  on:click={() => feedMuted = !feedMuted}>
                  {#if feedMuted}
                    <svg viewBox="0 0 16 16" class="fc-icon"><path d="M8 2v12l-4-3H1V7h3L8 4V2zm4.5 2.5a6 6 0 010 7M11 5.5a4 4 0 010 5"/><line x1="1" y1="1" x2="15" y2="15" stroke-linecap="round"/></svg>
                  {:else if feedVolume < 0.35}
                    <svg viewBox="0 0 16 16" class="fc-icon"><path d="M8 2v12l-4-3H1V7h3L8 4V2z"/><path d="M11 6a2.5 2.5 0 010 4"/></svg>
                  {:else}
                    <svg viewBox="0 0 16 16" class="fc-icon"><path d="M8 2v12l-4-3H1V7h3L8 4V2z"/><path d="M11 5.5a4 4 0 010 5M13 3.5a7 7 0 010 9"/></svg>
                  {/if}
                </button>
                <input type="range" min="0" max="1" step="0.01"
                  bind:value={feedVolume}
                  on:input={() => { if (feedVolume > 0) feedMuted = false; }}
                  class="fc-slider" title="Volume" />
                <span class="fc-vol">{Math.round(feedVolume * 100)}%</span>
              {:else if cameraOk}
                <span class="fc-no-audio">no audio</span>
              {/if}
            </div>

            <div class="cam-below">
```

No CSS changes are needed — `.feed-controls`, `.fc-btn`, `.fc-icon`, `.fc-slider`, `.fc-vol`, `.fc-no-audio` are already defined in this component's `<style>` and apply here automatically.

- [ ] **Step 2: Verify it compiles cleanly**

Run: `npm run check`
Expected: `0 errors, 0 warnings`. (In particular, no "unused CSS selector" warnings — the reused classes are now referenced in two places, which is fine.)

- [ ] **Step 3: Commit**

```bash
git add src/App.svelte
git commit -m "feat(setup): mute/volume control on the first-time camera step

Audio-only reuse of the monitor's .feed-controls under the SourceCheck preview;
shows \"no audio\" until a working device is selected. DeviceSelectors and the
re-run SettingsModal are untouched.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Build + manual verification

**Files:** none (verification only)

- [ ] **Step 1: Production build**

Run: `npm run build`
Expected: build completes with no errors (Vite emits to `dist-ui/`).

- [ ] **Step 2: Manual in-app verification**

Launch the app into first-time setup (the engine must be running for the camera to open). Confirm each item from the spec's Testing section:

1. **Camera step playback:** on the camera step, select an audio device from the dropdown → its audio plays through the speakers; the mute button toggles it; the volume slider changes the level; the `%` readout tracks the slider.
2. **Step scoping:** navigate camera → `sync` (or `language`) → audio stops. Return to camera → audio resumes.
3. **No-audio state:** select `"none"` (or before any device is picked) → the control shows `"no audio"` and there is silence.
4. **Completion:** finish setup → the monitor's audio works exactly as before (no double audio, no silence).
5. **Re-run regression:** open the settings modal post-setup → audio still plays via the background monitor on every tab, unchanged.

- [ ] **Step 3: Final state**

No commit required (Tasks 1–3 are already committed). If any manual check fails, treat it as a bug against this plan, diagnose with `superpowers:systematic-debugging`, and fix before declaring complete.

---

## Self-Review

**Spec coverage:**
- Guard relaxation (spec §Changes 1) → Task 1. ✅
- Start/stop hooks in `goStep` (spec §Changes 2) → Task 2. ✅
- Mute/volume UI reusing `.feed-controls`, `"no audio"` fallback, in setup markup not `DeviceSelectors` (spec §Changes 3) → Task 3. ✅
- Re-run modal / monitor untouched (spec §Out of scope) → guaranteed by editing only the `appView === "setup"` markup and the `!setupComplete` branch in `goStep`; verified by Task 4 step 2 check 5. ✅
- Manual testing checklist (spec §Testing) → Task 4. ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N"; every code step shows full literal code. ✅

**Type/name consistency:** `_setupAudio`, `_teardownAudio`, `_hasAudio`, `feedMuted`, `feedVolume`, `appView`, `wizardStep`, `setupComplete`, `cameraOk` are all existing `App.svelte` identifiers used consistently across tasks and matching their current definitions. CSS classes (`.feed-controls`, `.fc-btn`, `.fc-icon`, `.fc-slider`, `.fc-vol`, `.fc-no-audio`) match the existing `<style>` selectors. ✅
