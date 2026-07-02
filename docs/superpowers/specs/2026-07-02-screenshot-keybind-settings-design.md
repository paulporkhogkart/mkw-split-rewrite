# Screenshot keybind + Screenshots settings tab — Design

**Date:** 2026-07-02
**Status:** Approved (pending spec review)

## Goal

Give screenshots a configurable hotkey and a dedicated **Screenshots** settings tab.
Taking a screenshot (button OR hotkey) plays a shutter sound and, per user
preference, saves a file and/or copies the image to the clipboard. The save
folder is user-selectable.

## Requirements (from the request)

1. New **Screenshots** tab in the returning-user settings modal.
2. Hotkey to take a screenshot — same action as the existing "Shot" button.
3. Hotkey supports **key combinations** (modifiers + a key), easily set/adjusted in the tab.
4. Hotkey only fires when the **pbenguin window is focused**.
5. Hotkey does **not** fire while any modal is open, during first-time setup, or
   on any non-monitor view (e.g. Edit Screens) — only on the live monitor.
6. Default hotkey = **F12** (free — see "F12 note" below).
7. On capture (button or hotkey), play `TWL_CMN_SE_SHUTTER.wav`. Sound always
   plays, independent of the feed mute state.
8. Capture now **also copies the image to the clipboard** (in addition to file save).
9. Two checkboxes in the tab: **Save to file** and **Copy to clipboard**, both
   **checked by default**.
10. User-selectable **save folder**, defaulting to the current `Pictures\pbenguin`.
11. The Screenshots tab does **not** appear during first-time setup.

## Existing pieces (context)

- `App.svelte:takeScreenshot()` → `FeedOverlay.capturePng()` (clean feed → PNG
  `Uint8Array`) → Rust `save_screenshot(bytes, stamp)` writes `Pictures\pbenguin\mkw-<stamp>.png`.
- Feed-control "Shot" button calls `takeScreenshot()`.
- Settings tabs: `RERUN_STEPS` (post-setup modal) vs `FIRST_TIME_STEPS` (setup),
  `App.svelte:493`. `SettingsModal.svelte` renders per-step bodies.
- Settings persistence idiom: localStorage-backed writable stores
  (`src/lib/discordSettings.js`).
- Sound idiom: `import snd from "../assets/x.wav"; new Audio(snd).play()`
  (`RunReviewModal.svelte`).
- No frontend keydown handler exists yet.
- No clipboard/dialog Tauri plugin yet.

## Design

### 1. Settings store — `src/lib/screenshotSettings.js`

localStorage-backed writable stores (same shape as `discordSettings.js`):

| Store | Key | Default |
|-------|-----|---------|
| `screenshotKeybind`   | `screenshot_keybind`    | `"F12"` |
| `screenshotSaveFile`  | `screenshot_save_file`  | `true`  |
| `screenshotClipboard` | `screenshot_clipboard`  | `true`  |
| `screenshotDir`       | `screenshot_dir`        | `""` (empty ⇒ default `Pictures\pbenguin`) |

Each store subscribes to write its value back to localStorage on change.

### 2. Keybind helpers — `src/lib/keybind.js` (pure, unit-tested)

Canonical string form so the recorder, storage, matcher, and display all agree.

- `formatKeybind(event)` → canonical string, e.g. `"F12"`, `"Ctrl+Shift+S"`.
  - Modifier order is fixed: `Ctrl` → `Alt` → `Shift` → `Meta`, then the main key.
  - Main key normalized from `event.key`: single letters upper-cased, `" "` →
    `"Space"`, others (`F12`, `ArrowUp`, `Enter`, digits, punctuation) taken as-is.
  - Returns `null` if the event's key is itself a modifier (so the recorder keeps
    waiting for a non-modifier key — you can't bind a bare `Ctrl`).
- `matchesKeybind(event, combo)` → boolean. Builds `formatKeybind(event)` and
  compares case-insensitively to `combo`. Returns false for a null format or an
  empty combo.
- `prettyKeybind(combo)` → display string (e.g. keeps `"F12"`, `"Ctrl+Shift+S"`);
  trivial for now (identity / friendly join) but centralizes future symbol mapping.

### 3. Keybind recorder — `src/components/KeybindRecorder.svelte`

- A button showing the current binding (`prettyKeybind`). Click → "recording"
  state ("Press a key…").
- While recording, an element-level `on:keydown` captures the next event:
  - `Escape` cancels (keeps the old binding).
  - `formatKeybind(event)` — if `null` (modifier-only), keep waiting; else set the
    binding, `preventDefault`, and leave recording state.
- `preventDefault`/`stopPropagation` while recording so the global handler and the
  browser don't act on the keys being recorded.
- Emits the new combo via `bind:value` (a prop bound to `$screenshotKeybind`).

### 4. Global hotkey handler — `App.svelte`

- `<svelte:window on:keydown={onGlobalKeydown} />`.
- `onGlobalKeydown(e)`:
  - Fire **only on the live monitor**: require `appView === "main"` AND
    `$viewStore !== "edit"`. This rejects startup, first-time setup, and the
    Edit Screens view (which is `appView === "main" && $viewStore === "edit"`) —
    the screenshot only makes sense where the live feed is shown. A reactive
    `$: screenshotAllowed = appView === "main" && $viewStore !== "edit" && !anyModalOpen`
    centralizes the gate.
  - Ignore if any modal is open: `wizardOpen || reviewHead || ghostWarnOpen`
    (reactive `$: anyModalOpen = …`).
  - Ignore if the event target is an editable field
    (`INPUT`/`TEXTAREA`/`isContentEditable`).
  - If `matchesKeybind(e, $screenshotKeybind)` → `e.preventDefault()` +
    `takeScreenshot()`.
- Webview keydown only fires when the window is focused ⇒ requirement 4 is met
  intrinsically; no OS-global hotkey registration.

### 5. `takeScreenshot()` rework — `App.svelte`

```
async function takeScreenshot() {
  play shutter (new Audio(shutterSnd).play(), swallow autoplay errors) — ALWAYS
  const bytes = await feedOverlayComp?.capturePng();
  if (!bytes) { flash "No feed to capture" (err); return; }
  let savedPath = null, copied = false;
  if ($screenshotSaveFile) savedPath = await invoke("save_screenshot", {bytes:Array.from(bytes), stamp, dir: $screenshotDir || null});
  if ($screenshotClipboard) { await invoke("copy_screenshot_to_clipboard", {bytes:Array.from(bytes)}); copied = true; }
  flash a summary ("Saved → <path>", "Copied to clipboard", "Saved + copied", or
    "Screenshot: nothing enabled" if both off);
}
```

- Sound plays before the async capture so feedback is immediate.
- Errors in save/clipboard flash an error but don't crash; each guarded.

### 6. Rust — `src-tauri/src/lib.rs`

- **`save_screenshot(bytes, stamp, dir: Option<String>)`** — extend the existing
  command: if `dir` is `Some(non-empty)`, write there (create dirs); else keep the
  current `Pictures\pbenguin` default. Returns the written path.
- **`copy_screenshot_to_clipboard(bytes) -> Result<(), String>`** (new) — decode
  the PNG (`image` crate) → RGBA8 → `tauri::image::Image::new(rgba, w, h)` →
  `app.clipboard().write_image(&img)`. Passing the already-compressed PNG bytes
  keeps the IPC payload small (vs shipping ~8 MB of raw RGBA across the boundary).
- Register both new/changed commands in `generate_handler!`.

### 7. Plugins & deps

- `Cargo.toml`: add `tauri-plugin-clipboard-manager = "2"`,
  `tauri-plugin-dialog = "2"`, and `image = "0.25"` (PNG decode).
- `lib.rs`: `.plugin(tauri_plugin_clipboard_manager::init())` +
  `.plugin(tauri_plugin_dialog::init())`.
- `capabilities/default.json`: add `clipboard-manager:allow-write-image` and
  `dialog:allow-open`.
- `package.json`: add `@tauri-apps/plugin-dialog` (folder picker from JS). The
  clipboard is done in Rust, so no JS clipboard plugin needed.

### 8. Screenshots tab — `SettingsModal.svelte` + `App.svelte`

- `App.svelte`: add `"screenshots"` to `RERUN_STEPS` **only** (not
  `FIRST_TIME_STEPS`) ⇒ requirement 11. Add `STEP_LABELS.screenshots = "Screenshots"`.
- `SettingsModal.svelte`: new `{:else if wizardStep === "screenshots"}` branch:
  - **Hotkey** row: `<KeybindRecorder bind:value={$screenshotKeybind} />` + a
    "Reset to F12" link.
  - **Save to file** checkbox (`bind:checked={$screenshotSaveFile}`).
  - **Copy to clipboard** checkbox (`bind:checked={$screenshotClipboard}`).
  - **Save folder**: current path (or "Pictures\pbenguin (default)") + "Choose
    folder…" button → `@tauri-apps/plugin-dialog` `open({directory:true})` →
    set `$screenshotDir`; + "Use default" to clear it.
  - Styled with the existing `.discord-*`/`.step-centred` idioms already in the modal.
- Stores imported into `SettingsModal.svelte` from `screenshotSettings.js`.

### 9. Sound asset

- Move `temp/TWL_CMN_SE_SHUTTER.wav` → `src/assets/shutter.wav`; import in
  `App.svelte`. (Matches `run-review.wav` living in `src/assets/`.)

## F12 note

In a **release** Tauri build, devtools is disabled, so F12 is free — safe default.
In `tauri dev`, F12 may open WebView2 devtools before our handler's
`preventDefault` runs. This only affects development; the shipped app is
unaffected. Documented, not worked around.

## Edge cases

- Both checkboxes off → capture plays the shutter and flashes "nothing enabled";
  no file, no clipboard.
- No feed yet → "No feed to capture" (unchanged behaviour).
- Recording a modifier-only chord → recorder keeps waiting (can't bind bare `Ctrl`).
- Typing the combo inside a text field or with a modal open → suppressed by the
  gates in §4.
- Rapid re-trigger → a fresh `new Audio()` per shot, so overlapping shutters are fine.

## Testing

- **vitest** `src/lib/keybind.test.js`: `formatKeybind` (plain key, each modifier,
  combined order, modifier-only ⇒ null, space/letter normalization);
  `matchesKeybind` (match, case-insensitivity, mismatch, empty combo ⇒ false).
- **vitest** `screenshotSettings` defaults + localStorage round-trip (mirrors any
  existing settings-store test; light).
- **Rust**: `copy_screenshot_to_clipboard` PNG-decode path is thin; clipboard I/O
  isn't unit-testable in CI, so keep the command minimal and rely on a manual
  smoke. `save_screenshot` custom-dir branch verified by manual smoke.
- **Manual smoke** (documented in the plan): F12 + button both capture; shutter
  plays; file lands in default and custom folders; image pastes into an external
  app; no fire while a modal is open or in first-time setup.

## Out of scope

- OS-global hotkeys (fire when pbenguin is unfocused) — explicitly not wanted.
- Rebindable keys for anything other than the screenshot.
- A separate keybinds framework — one binding, kept simple.
