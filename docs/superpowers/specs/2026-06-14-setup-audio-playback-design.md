# First-time setup audio playback — design

**Date:** 2026-06-14
**Status:** Approved (design)

## Goal

During the **first-time setup** camera step, play the live capture feed's audio
through the speakers so the user can hear the selected audio device and confirm
it is the right one. Today audio is silent throughout first-time setup. Add a
mute + volume control to that step. Audio must play **only while the camera step
is active** (not on the `language` or `sync` steps), and the existing re-run
settings modal must be left untouched.

## Background — current state

All audio is played through a single Web Audio gain node owned by `App.svelte`:

- `_setupAudio()` (`src/App.svelte:427`) tears down any existing node, then builds
  `_audioCtx` + `_gainNode` from `videoStream` and connects it to the speakers.
  `_hasAudio` is set true when the stream carries an audio track.
- Both `<video>` elements (`FeedOverlay`'s monitor video and `SourceCheck`'s
  wizard preview) stay `muted` deliberately, so the gain node is the *only*
  audio path — otherwise the track would play twice (the "two audio streams"
  bug, documented in `FeedOverlay.svelte:107`).
- `_setupAudio()` has a hard guard at line 430: **`if (!setupComplete) return;`**.
  This is exactly why audio plays in the re-run settings modal (where
  `setupComplete === true` and the background monitor's stream drives the gain
  node) but is silent during first-time setup.
- `_setupAudio()` is called at the tail of `startCamera()` (`src/App.svelte:1032`)
  — so it fires when the stream first opens and on every audio-device change
  (`handleAudioDeviceChange` → `startCamera`) — and from `completeSetup()`.
- `stopCamera()` calls `_teardownAudio()`.
- Gain stays in sync with the controls via
  `$: if (_gainNode) _gainNode.gain.value = feedMuted ? 0 : feedVolume;`
  (`feedVolume` default `0.5`, `feedMuted` default `false`).

First-time setup is the inline `appView === "setup"` view (`src/App.svelte:1602`),
a wizard over `FIRST_TIME_STEPS = ["language", "camera", "sync"]`. The camera
feed (`SourceCheck`) renders **only** on the `camera` step, but `videoStream`
keeps running on the other steps (they just don't render the preview).
`appView === "setup"` implies `setupComplete === false`. The re-run settings UI
is the separate `SettingsModal` component, driven by `wizardOpen`/`wizardStep`.

The monitor view already has a feed-controls block (`src/App.svelte:1546`–1568):
mute button + volume `<input type="range">` + `%` readout, shown when
`_hasAudio`, else a `"no audio"` label. It binds `feedMuted`/`feedVolume`.

## Core idea

Reuse the existing audio path verbatim. Only two things change:

1. **Relax the guard** so `_setupAudio()` is also allowed on the first-time
   setup camera step.
2. **Bound the lifetime** to the camera step: start when the step's stream is
   live, stop when leaving the step. Because the stream keeps running on other
   steps, "stop" cannot rely on `stopCamera()`; it is an explicit teardown at the
   step-leave transition.

No new audio plumbing, no second audio path, no change to the monitor / re-run
mechanism.

## Changes (all in `src/App.svelte`)

### 1. Guard relaxation — `_setupAudio()`

Replace:

```js
if (!setupComplete) return;
```

with a condition that also permits the first-time setup camera step:

```js
const onSetupCamStep = appView === "setup" && wizardStep === "camera";
if (!setupComplete && !onSetupCamStep) return;
```

(`appView === "setup"` already implies `!setupComplete`, so this reads as: allow
when `setupComplete`, or when on the first-time camera step.) Everything below
the guard — `_hasAudio`, the context build, the gain-sync reactive — is unchanged
and now works for first-time setup.

### 2. Start / stop hooks — `goStep(step)`

- **Start (re-entry case):** the *first* arrival on the camera step opens the
  stream via `_openMatchedCameras()` → `startCamera()` → `_setupAudio()` (tail
  call), so it is already covered. But *re-entering* the camera step when the
  stream is already live does **not** re-run `startCamera()` (guarded by
  `pythonCameraStatus !== "ok"`). So add an explicit `_setupAudio()` for that
  case.
- **Stop:** when navigating to a non-camera step during first-time setup, call
  `_teardownAudio()`.

Concretely, at the end of `goStep`:

```js
if (!setupComplete) {
  if (step === "camera") _setupAudio();   // no-op until the stream is live; reconnects on re-entry
  else                   _teardownAudio();
}
```

`_setupAudio()` is idempotent (it tears down first), so calling it when audio is
already connected is safe; calling it before the stream exists is a no-op (early
`if (!videoStream) return;`), and `startCamera`'s tail call will connect once the
stream resolves.

`completeSetup()` (`src/App.svelte:1173`) already flips `setupComplete = true` and
calls `_setupAudio()`, handing audio to the monitor path — unchanged.

### 3. UI — mute/volume control on the camera step

Add a feed-controls block into the first-time camera step markup (inside the
`appView === "setup"` block, under the `SourceCheck` preview, mirroring the
monitor's under-feed placement). It reuses the **existing** `.feed-controls`
markup and styles:

- When `_hasAudio`: mute toggle (`feedMuted`), volume slider bound to
  `feedVolume`, and the `%` readout — identical to the monitor controls.
- Else: a `"no audio"` label (same as the monitor), which doubles as feedback
  that no working audio device is selected yet (none picked, or `"none"`).

This lives in `App.svelte`'s setup markup, **not** in the shared `DeviceSelectors`
component, so the re-run `SettingsModal` is unaffected and `DeviceSelectors` stays
pristine.

## Out of scope / unchanged

- The re-run `SettingsModal` and the monitor feed-controls — untouched.
- Audio device resolution, mic-permission unlocking, and groupId pairing in
  `startCamera()` — unchanged. (On first arrival, before the user picks an audio
  device, the stream is typically video-only, so `"no audio"` shows until they
  select a device; selecting one restarts the stream with that audio track and
  playback begins.)
- No new config keys; `feedMuted`/`feedVolume` are reused as-is.

## Rejected alternative

A single reactive statement governing audio on/off for both setup and monitor
(`$: want = videoStream && (setupComplete || onSetupCamStep); …`). More uniform,
but it changes the *mechanism* of the already-working re-run/monitor path,
risking a double-audio or teardown/rebuild-thrash regression on stream changes.
The user asked to keep that path as-is, so explicit start/stop at the two
well-defined transition points (step change, setup completion) is the lower-risk
choice.

## Testing

This is frontend-only, audio-device-dependent behavior with no pure logic to unit
test in isolation; verification is manual in-app:

1. **First-time setup, camera step:** select an audio device → its audio plays;
   mute/volume control adjusts it; `%` updates.
2. **Step scoping:** navigate camera → `sync`/`language` → audio stops; return to
   camera → audio resumes.
3. **No-audio state:** select `"none"` (or before any device is picked) → control
   shows `"no audio"`, silence.
4. **Completion:** finish setup → monitor audio works exactly as before (no
   double audio, no silence).
5. **Regression — re-run modal:** open settings post-setup → audio still plays via
   the background monitor on every tab, unchanged.
6. `npm run check` (svelte-check) stays 0/0; `npm run build` green.
