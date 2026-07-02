# Screenshot Keybind + Screenshots Settings Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable screenshot hotkey (combos, default F12) and a Screenshots settings tab with save-to-file / copy-to-clipboard toggles and a selectable save folder; capture plays a shutter sound.

**Architecture:** Frontend-only settings persisted in localStorage-backed Svelte stores (matching `discordSettings.js`/`feedSettings.js`). Pure keybind helpers (`keybind.js`) drive a `KeybindRecorder` component and a `<svelte:window on:keydown>` handler in `App.svelte`, gated to the live monitor view with no modal open. Capture reuses the existing `FeedOverlay.capturePng()` PNG bytes: file-save via the extended Rust `save_screenshot`, clipboard via a new Rust `copy_screenshot_to_clipboard` (PNG→RGBA decode + clipboard-manager plugin). Folder picking uses the Tauri dialog plugin.

**Tech Stack:** Svelte, Tauri 2 (Rust), `tauri-plugin-clipboard-manager`, `tauri-plugin-dialog`, `image` crate, Vitest.

## Global Constraints

- All new settings persist in **localStorage** only (the Python engine never reads them). Use the `safeStorage()` + pure-loader idiom from `src/lib/feedSettings.js` so modules import cleanly under Node (tests).
- Store keys: `screenshot_keybind` (default `"F12"`), `screenshot_save_file` (default `true`), `screenshot_clipboard` (default `true`), `screenshot_dir` (default `""` = `Pictures\pbenguin`).
- Hotkey fires **only** when: `appView === "main"` AND `$viewStore !== "edit"` AND no modal open (`!wizardOpen && !reviewHead && !ghostWarnOpen`) AND the event target is not an editable field.
- The shutter plays **only when a screenshot is actually produced** (save and/or clipboard enabled) — never when both are off. Independent of feed mute.
- The Screenshots tab is added to `RERUN_STEPS` **only**, never `FIRST_TIME_STEPS`.
- Tauri window label is `main`; capabilities live in `src-tauri/capabilities/default.json`.
- JS asset imports for `.wav` are already supported by Vite (see `run-review.wav`).
- Keybind canonical form: modifier order `Ctrl` → `Alt` → `Shift` → `Meta`, then the main key; single letters upper-cased; `" "` → `"Space"`. Matching is case-insensitive.

---

### Task 1: Pure keybind helpers

**Files:**
- Create: `src/lib/keybind.js`
- Test: `src/lib/keybind.test.js`

**Interfaces:**
- Produces:
  - `formatKeybind(e) -> string | null` — canonical combo from a keydown-like `{key, ctrlKey, altKey, shiftKey, metaKey}`; `null` if `e.key` is a bare modifier.
  - `matchesKeybind(e, combo) -> boolean` — case-insensitive compare of `formatKeybind(e)` to `combo`; false for empty combo or null format.
  - `prettyKeybind(combo) -> string` — display form (identity for now).

- [ ] **Step 1: Write the failing test**

```js
// src/lib/keybind.test.js
import { describe, it, expect } from "vitest";
import { formatKeybind, matchesKeybind, prettyKeybind } from "./keybind.js";

const ev = (o) => ({ ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...o });

describe("formatKeybind", () => {
  it("formats a plain function key", () => {
    expect(formatKeybind(ev({ key: "F12" }))).toBe("F12");
  });
  it("upper-cases single letters and orders modifiers Ctrl/Alt/Shift/Meta", () => {
    expect(formatKeybind(ev({ key: "s", ctrlKey: true, shiftKey: true }))).toBe("Ctrl+Shift+S");
    expect(formatKeybind(ev({ key: "a", ctrlKey: true, altKey: true, shiftKey: true, metaKey: true })))
      .toBe("Ctrl+Alt+Shift+Meta+A");
  });
  it("maps space to Space", () => {
    expect(formatKeybind(ev({ key: " " }))).toBe("Space");
  });
  it("returns null for a bare modifier key", () => {
    expect(formatKeybind(ev({ key: "Control", ctrlKey: true }))).toBeNull();
    expect(formatKeybind(ev({ key: "Shift", shiftKey: true }))).toBeNull();
  });
});

describe("matchesKeybind", () => {
  it("matches case-insensitively", () => {
    expect(matchesKeybind(ev({ key: "F12" }), "F12")).toBe(true);
    expect(matchesKeybind(ev({ key: "s", ctrlKey: true }), "ctrl+s")).toBe(true);
  });
  it("rejects mismatches, empty combos, and modifier-only events", () => {
    expect(matchesKeybind(ev({ key: "a" }), "F12")).toBe(false);
    expect(matchesKeybind(ev({ key: "a" }), "")).toBe(false);
    expect(matchesKeybind(ev({ key: "Control", ctrlKey: true }), "Ctrl")).toBe(false);
  });
});

describe("prettyKeybind", () => {
  it("shows the combo or empty string", () => {
    expect(prettyKeybind("Ctrl+Shift+S")).toBe("Ctrl+Shift+S");
    expect(prettyKeybind("")).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/keybind.test.js`
Expected: FAIL — cannot resolve `./keybind.js` / functions not defined.

- [ ] **Step 3: Write minimal implementation**

```js
// src/lib/keybind.js
// Pure keybind helpers: canonical string form so the recorder, storage, matcher,
// and display all agree. Takes plain keydown-like objects (testable without a DOM).

const MODIFIER_KEYS = new Set(["Control", "Alt", "Shift", "Meta"]);

function normKey(k) {
  if (k === " ") return "Space";
  return k.length === 1 ? k.toUpperCase() : k;
}

/** Canonical combo (e.g. "F12", "Ctrl+Shift+S"), or null if e.key is a bare modifier. */
export function formatKeybind(e) {
  if (MODIFIER_KEYS.has(e.key)) return null;
  const parts = [];
  if (e.ctrlKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");
  if (e.metaKey) parts.push("Meta");
  parts.push(normKey(e.key));
  return parts.join("+");
}

/** True if the event's combo equals `combo` (case-insensitive). False for empty/null. */
export function matchesKeybind(e, combo) {
  if (!combo) return false;
  const f = formatKeybind(e);
  return f !== null && f.toLowerCase() === combo.toLowerCase();
}

/** Display form of a stored combo. Identity for now; centralizes future symbol mapping. */
export function prettyKeybind(combo) {
  return combo || "";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/keybind.test.js`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/lib/keybind.js src/lib/keybind.test.js
git commit -m "feat(screenshot): pure keybind format/match helpers"
```

---

### Task 2: Screenshot settings store

**Files:**
- Create: `src/lib/screenshotSettings.js`
- Test: `src/lib/screenshotSettings.test.js`

**Interfaces:**
- Produces:
  - `loadScreenshotPrefs(store) -> { keybind, saveFile, clipboard, dir }` — pure loader from a `{getItem}` object with defaults.
  - Writable stores: `screenshotKeybind`, `screenshotSaveFile`, `screenshotClipboard`, `screenshotDir` — each persists to localStorage on change.

- [ ] **Step 1: Write the failing test**

```js
// src/lib/screenshotSettings.test.js
import { describe, it, expect } from "vitest";
import { loadScreenshotPrefs } from "./screenshotSettings.js";

function fakeStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

describe("loadScreenshotPrefs", () => {
  it("returns defaults for empty storage", () => {
    expect(loadScreenshotPrefs(fakeStorage())).toEqual({
      keybind: "F12", saveFile: true, clipboard: true, dir: "",
    });
  });
  it("reads persisted values", () => {
    const store = fakeStorage({
      screenshot_keybind: "Ctrl+Shift+S", screenshot_save_file: "false",
      screenshot_clipboard: "false", screenshot_dir: "D:/shots",
    });
    expect(loadScreenshotPrefs(store)).toEqual({
      keybind: "Ctrl+Shift+S", saveFile: false, clipboard: false, dir: "D:/shots",
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/screenshotSettings.test.js`
Expected: FAIL — cannot resolve `./screenshotSettings.js`.

- [ ] **Step 3: Write minimal implementation**

```js
// src/lib/screenshotSettings.js
// Screenshot preferences, persisted in localStorage (client-side only — the Python
// engine never reads these). Same safeStorage + pure-loader pattern as feedSettings.js.
import { writable } from "svelte/store";

const KEYS = {
  keybind:   "screenshot_keybind",
  saveFile:  "screenshot_save_file",
  clipboard: "screenshot_clipboard",
  dir:       "screenshot_dir",
};
const DEFAULTS = { keybind: "F12", saveFile: true, clipboard: true, dir: "" };

function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}

const parseBool = (raw, fallback) => (raw === "true" ? true : raw === "false" ? false : fallback);

/** Load all screenshot prefs from a storage object (defaults when absent). */
export function loadScreenshotPrefs(store) {
  return {
    keybind:   store.getItem(KEYS.keybind)   || DEFAULTS.keybind,
    saveFile:  parseBool(store.getItem(KEYS.saveFile),  DEFAULTS.saveFile),
    clipboard: parseBool(store.getItem(KEYS.clipboard), DEFAULTS.clipboard),
    dir:       store.getItem(KEYS.dir)       || DEFAULTS.dir,
  };
}

const ls = safeStorage();
const initial = loadScreenshotPrefs(ls);

export const screenshotKeybind   = writable(initial.keybind);
export const screenshotSaveFile  = writable(initial.saveFile);
export const screenshotClipboard = writable(initial.clipboard);
export const screenshotDir       = writable(initial.dir);

screenshotKeybind.subscribe((v)   => ls.setItem(KEYS.keybind, v || ""));
screenshotSaveFile.subscribe((v)  => ls.setItem(KEYS.saveFile, v ? "true" : "false"));
screenshotClipboard.subscribe((v) => ls.setItem(KEYS.clipboard, v ? "true" : "false"));
screenshotDir.subscribe((v)       => ls.setItem(KEYS.dir, v || ""));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/screenshotSettings.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/screenshotSettings.js src/lib/screenshotSettings.test.js
git commit -m "feat(screenshot): localStorage-backed settings store"
```

---

### Task 3: Rust backend — dir-aware save + clipboard copy + plugins

**Files:**
- Modify: `src-tauri/Cargo.toml` (deps)
- Modify: `src-tauri/src/lib.rs:189-202` (`save_screenshot`), add `copy_screenshot_to_clipboard`, register plugins + handlers
- Modify: `src-tauri/capabilities/default.json` (permissions)
- Move: `temp/TWL_CMN_SE_SHUTTER.wav` → `src/assets/shutter.wav`
- Modify: `package.json` (add `@tauri-apps/plugin-dialog`)

**Interfaces:**
- Produces (Tauri commands, called from `App.svelte`):
  - `save_screenshot(bytes: number[], stamp: string, dir?: string | null) -> string` (path)
  - `copy_screenshot_to_clipboard(bytes: number[]) -> void`
- Consumes: `FeedOverlay.capturePng()` PNG bytes (Task 6).

- [ ] **Step 1: Add Rust deps**

Edit `src-tauri/Cargo.toml`, under `[dependencies]` add:

```toml
tauri-plugin-clipboard-manager = "2"
tauri-plugin-dialog = "2"
image = { version = "0.25", default-features = false, features = ["png"] }
```

- [ ] **Step 2: Extend `save_screenshot` with an optional dir + add the clipboard command**

In `src-tauri/src/lib.rs`, replace the existing `save_screenshot` (lines 189-202) with both commands:

```rust
/// Save a PNG screenshot (raw bytes from the frontend canvas). Writes into `dir`
/// when given a non-empty path, else the user's Pictures\pbenguin folder. Returns
/// the full path written.
#[tauri::command]
fn save_screenshot(
    app: tauri::AppHandle,
    bytes: Vec<u8>,
    stamp: String,
    dir: Option<String>,
) -> Result<String, String> {
    let dir = match dir {
        Some(d) if !d.trim().is_empty() => std::path::PathBuf::from(d),
        _ => app
            .path()
            .picture_dir()
            .map_err(|e| e.to_string())?
            .join("pbenguin"),
    };
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("mkw-{stamp}.png"));
    std::fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

/// Copy a PNG screenshot to the OS clipboard as an image. Decodes the (already
/// compressed) PNG to RGBA8 and hands it to the clipboard-manager plugin.
#[tauri::command]
fn copy_screenshot_to_clipboard(app: tauri::AppHandle, bytes: Vec<u8>) -> Result<(), String> {
    use tauri_plugin_clipboard_manager::ClipboardExt;
    let img = image::load_from_memory_with_format(&bytes, image::ImageFormat::Png)
        .map_err(|e| e.to_string())?
        .to_rgba8();
    let (w, h) = img.dimensions();
    let image = tauri::image::Image::new_owned(img.into_raw(), w, h);
    app.clipboard().write_image(&image).map_err(|e| e.to_string())
}
```

- [ ] **Step 3: Register the plugins and the new command**

In `src-tauri/src/lib.rs`, in the `tauri::Builder` chain (near line 213-216), add the two plugins alongside the existing `.plugin(...)` calls:

```rust
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
```

Then add `copy_screenshot_to_clipboard` to the `generate_handler!` list (it already contains `save_screenshot`):

```rust
        .invoke_handler(tauri::generate_handler![start_tracker, stop_tracker, restart_tracker, send_to_tracker, open_url, save_screenshot, copy_screenshot_to_clipboard, discord::discord_set_presence, discord::discord_clear_presence, sync::sync_set_config, sync::sync_test_connection, sync::sync_resolve_pending, sync::sync_discard_pending, sync::sync_list_pending, sync::sync_course_reads, sync::sync_roster, sync::sync_pb_best])
```

- [ ] **Step 4: Grant capabilities**

In `src-tauri/capabilities/default.json`, add to the `"permissions"` array:

```json
    "clipboard-manager:allow-write-image",
    "dialog:allow-open"
```

- [ ] **Step 5: Move the shutter sound + add the JS dialog plugin**

```bash
mkdir -p src/assets
cp temp/TWL_CMN_SE_SHUTTER.wav src/assets/shutter.wav
npm install @tauri-apps/plugin-dialog@^2
```

- [ ] **Step 6: Build to verify the Rust + deps compile**

Run: `cargo build --manifest-path src-tauri/Cargo.toml`
Expected: builds clean (new plugins + `image` resolve; both commands compile).

- [ ] **Step 7: Commit**

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/lib.rs src-tauri/capabilities/default.json src/assets/shutter.wav package.json package-lock.json
git commit -m "feat(screenshot): dir-aware save + clipboard-copy command + shutter asset"
```

---

### Task 4: Keybind recorder component

**Files:**
- Create: `src/components/KeybindRecorder.svelte`

**Interfaces:**
- Consumes: `formatKeybind`, `prettyKeybind` from `src/lib/keybind.js` (Task 1).
- Produces: `<KeybindRecorder bind:value={combo} />` — a button that records the next non-modifier chord into `value` (canonical string). Escape cancels.

- [ ] **Step 1: Write the component**

```svelte
<!-- src/components/KeybindRecorder.svelte
     Click to record a hotkey. Captures the next keydown on the button; a bare
     modifier keeps it waiting, Escape cancels. Emits the canonical combo via bind:value. -->
<script>
  import { formatKeybind, prettyKeybind } from "../lib/keybind.js";

  export let value = "";
  let recording = false;

  function start() { recording = true; }

  function onKeydown(e) {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") { recording = false; return; }
    const combo = formatKeybind(e);
    if (!combo) return;               // modifier-only — keep waiting
    value = combo;
    recording = false;
  }

  function onBlur() { recording = false; }
</script>

<button type="button" class="kb" class:recording on:click={start} on:keydown={onKeydown} on:blur={onBlur}>
  {recording ? "Press a key…" : (prettyKeybind(value) || "Set hotkey")}
</button>

<style>
  .kb {
    background: var(--panel); color: var(--tx); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .22rem .6rem;
    font-family: inherit; font-size: .72rem; min-width: 8rem; cursor: pointer;
    transition: border-color .12s, background .12s;
  }
  .kb:hover { background: var(--panel-2); }
  .kb.recording { border-color: var(--accent); color: var(--accent); }
</style>
```

- [ ] **Step 2: Type-check**

Run: `npm run check`
Expected: 0 errors / 0 warnings (svelte-check passes; the component is self-contained).

- [ ] **Step 3: Commit**

```bash
git add src/components/KeybindRecorder.svelte
git commit -m "feat(screenshot): keybind recorder component"
```

---

### Task 5: Screenshots settings tab

**Files:**
- Modify: `src/components/SettingsModal.svelte` (imports + new `screenshots` step body + styles)

**Interfaces:**
- Consumes: stores from `src/lib/screenshotSettings.js` (Task 2); `KeybindRecorder` (Task 4); `open` from `@tauri-apps/plugin-dialog` (Task 3).
- Produces: renders when `wizardStep === "screenshots"`. Reads/writes the four stores; folder picker sets `$screenshotDir`.

- [ ] **Step 1: Add imports**

In `src/components/SettingsModal.svelte`, after the existing imports (near line 23), add:

```js
  import KeybindRecorder from "./KeybindRecorder.svelte";
  import { screenshotKeybind, screenshotSaveFile, screenshotClipboard, screenshotDir } from "../lib/screenshotSettings.js";
  import { open as openDialog } from "@tauri-apps/plugin-dialog";

  async function chooseScreenshotDir() {
    try {
      const picked = await openDialog({ directory: true, title: "Choose screenshot folder" });
      if (typeof picked === "string" && picked) screenshotDir.set(picked);
    } catch (_) { /* dialog cancelled/unavailable */ }
  }
```

- [ ] **Step 2: Add the tab body**

In `src/components/SettingsModal.svelte`, add a new branch inside `.wiz-body` — place it after the SYNC branch and before the TRAILS branch (i.e. between the `{:else if wizardStep === "sync"}` block's closing and `{:else if wizardStep === "trails"}`):

```svelte
        <!-- ── SCREENSHOTS step ───────────────────────────────────────────── -->
        {:else if wizardStep === "screenshots"}
          <div class="step-centred">
            <h2>Screenshots</h2>
            <p>Capture the clean camera feed from the monitor view. Use the button on the feed, or the hotkey below (only while pbenguin is focused on the monitor).</p>

            <div class="discord-section">
              <h3 class="discord-heading">Hotkey</h3>
              <div class="ss-row">
                <KeybindRecorder bind:value={$screenshotKeybind} />
                <button class="btn-sm" on:click={() => screenshotKeybind.set("F12")}>Reset to F12</button>
              </div>
              <p class="discord-note">Click, then press a key or combination (e.g. Ctrl+Shift+S). Esc cancels.</p>
            </div>

            <div class="discord-section">
              <h3 class="discord-heading">On capture</h3>
              <label class="discord-row">
                <input type="checkbox" bind:checked={$screenshotSaveFile} />
                <span>Save screenshot to file</span>
              </label>
              <label class="discord-row">
                <input type="checkbox" bind:checked={$screenshotClipboard} />
                <span>Copy screenshot to clipboard</span>
              </label>
              <p class="discord-note">A shutter sound plays whenever a screenshot is taken. With both off, nothing happens.</p>
            </div>

            <div class="discord-section">
              <h3 class="discord-heading">Save folder</h3>
              <div class="ss-row">
                <span class="ss-path">{$screenshotDir || "Pictures\\pbenguin (default)"}</span>
              </div>
              <div class="ss-row">
                <button class="btn-sm" on:click={chooseScreenshotDir}>Choose folder…</button>
                <button class="btn-sm" on:click={() => screenshotDir.set("")} disabled={!$screenshotDir}>Use default</button>
              </div>
            </div>

            <div class="cam-nav" style="justify-content:flex-end">
              <button class="btn-primary" on:click={onClose}>Done</button>
            </div>
          </div>
```

- [ ] **Step 3: Add styles**

In `src/components/SettingsModal.svelte`, inside `<style>` (e.g. after the `.discord-note` rule), add:

```css
  .ss-row { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
  .ss-path {
    font-family: inherit; font-size: .7rem; color: var(--tx-mut);
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .22rem .5rem; word-break: break-all;
  }
```

- [ ] **Step 4: Type-check**

Run: `npm run check`
Expected: 0 errors / 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add src/components/SettingsModal.svelte
git commit -m "feat(screenshot): Screenshots settings tab (hotkey, toggles, folder)"
```

---

### Task 6: Wire the tab + global hotkey + capture rework in App.svelte

**Files:**
- Modify: `src/App.svelte` — `RERUN_STEPS`/`STEP_LABELS` (line 496-500), imports, `takeScreenshot()` (line 476-485), add `<svelte:window on:keydown>` + gate.

**Interfaces:**
- Consumes: stores from `screenshotSettings.js` (Task 2); `matchesKeybind` from `keybind.js` (Task 1); `save_screenshot` + `copy_screenshot_to_clipboard` (Task 3); `shutter.wav` (Task 3).
- Produces: `"screenshots"` tab in the settings modal; F12 (or configured combo) triggers `takeScreenshot()` on the monitor.

- [ ] **Step 1: Add the tab to the returning-user steps**

In `src/App.svelte`, edit `RERUN_STEPS` and `STEP_LABELS` (lines 496-500):

```js
  const RERUN_STEPS      = ["language", "camera", "discord", "sync", "trails", "screenshots"];
  const STEP_LABELS = {
    language: "Language", camera: "Video", discord: "Discord", sync: "Sync", trails: "Trails",
    screenshots: "Screenshots", screens: "Screens",
    selection: "Selection", hud: "HUD", templates: "Templates",
  };
```

(Leave `FIRST_TIME_STEPS` unchanged so the tab never appears during setup.)

- [ ] **Step 2: Add imports**

In `src/App.svelte`, near the other `src/lib` imports, add:

```js
  import shutterSnd from "./assets/shutter.wav";
  import { matchesKeybind } from "./lib/keybind.js";
  import { screenshotKeybind, screenshotSaveFile, screenshotClipboard, screenshotDir } from "./lib/screenshotSettings.js";
```

- [ ] **Step 3: Rework `takeScreenshot()`**

In `src/App.svelte`, replace the body of `takeScreenshot()` (lines 476-485) with:

```js
  async function takeScreenshot() {
    if (!$screenshotSaveFile && !$screenshotClipboard) {   // nothing would happen → no shutter
      _flashShot("Enable save or clipboard first", true);
      return;
    }
    try { new Audio(shutterSnd).play().catch(() => {}); } catch (_) { /* autoplay blocked */ }
    try {
      const bytes = await feedOverlayComp?.capturePng();
      if (!bytes) { _flashShot("No feed to capture", true); return; }
      const arr = Array.from(bytes);
      let savedPath = null, copied = false;
      if ($screenshotSaveFile) {
        savedPath = await invoke("save_screenshot", { bytes: arr, stamp: _shotStamp(), dir: $screenshotDir || null });
      }
      if ($screenshotClipboard) {
        await invoke("copy_screenshot_to_clipboard", { bytes: arr });
        copied = true;
      }
      const msg = savedPath && copied ? "Saved + copied to clipboard"
                : savedPath ? "Saved → " + savedPath
                : "Copied to clipboard";
      _flashShot(msg);
    } catch (e) {
      _flashShot("Screenshot failed", true);
    }
  }
```

- [ ] **Step 4: Add the global keydown handler + gate**

In `src/App.svelte`, near the `takeScreenshot` function, add the gate + handler:

```js
  // Screenshot hotkey: only on the live monitor, no modal open, not while typing.
  $: anyModalOpen = wizardOpen || !!reviewHead || ghostWarnOpen;
  $: screenshotAllowed = appView === "main" && $viewStore !== "edit" && !anyModalOpen;

  function onGlobalKeydown(e) {
    if (!screenshotAllowed) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if (matchesKeybind(e, $screenshotKeybind)) {
      e.preventDefault();
      takeScreenshot();
    }
  }
```

Then add the window listener in the markup — put it at the top of the top-level template (e.g. just before the root `<main ...>`/first element):

```svelte
<svelte:window on:keydown={onGlobalKeydown} />
```

- [ ] **Step 5: Type-check**

Run: `npm run check`
Expected: 0 errors / 0 warnings.

- [ ] **Step 6: Run the full JS test suite**

Run: `npm run test:js`
Expected: PASS, including the new `keybind` + `screenshotSettings` suites; no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/App.svelte
git commit -m "feat(screenshot): screenshots tab + F12 hotkey + save/clipboard/shutter capture"
```

---

### Task 7: Verification (build + manual smoke)

**Files:** none (verification only)

- [ ] **Step 1: Production build smoke**

Run: `npm run build`
Expected: Vite build succeeds (shutter.wav bundles; no import errors).

Run: `cargo build --manifest-path src-tauri/Cargo.toml`
Expected: clean.

- [ ] **Step 2: Manual smoke checklist** (run `npm run tauri dev`, complete setup or use an existing profile so `setupComplete` is true)

Verify each:
- [ ] Settings (⚙) shows a **Screenshots** tab; it is **absent** during first-time setup.
- [ ] Default hotkey shows **F12**; recorder captures a combo (e.g. Ctrl+Shift+S) and Reset restores F12.
- [ ] On the monitor: pressing the hotkey AND clicking the feed **Shot** button both capture; the **shutter plays** each time.
- [ ] File lands in `Pictures\pbenguin` by default; after "Choose folder…", new shots land in the chosen folder.
- [ ] Image **pastes** into an external app (e.g. Paint/Discord) when Copy-to-clipboard is on.
- [ ] With **both** checkboxes off: hotkey/button do **nothing** and play **no** sound (flash hint shown).
- [ ] Hotkey does **not** fire while the Settings modal (or Run Review / Ghost warning) is open, nor on the **Edit Screens** view, nor during first-time setup.
- [ ] (Note) F12 may open WebView2 devtools under `tauri dev` only; confirm the shipped/release build is unaffected (devtools disabled).

- [ ] **Step 3: Update memory + close out**

If all pass, note completion in `MEMORY.md` (topic file) per the project memory rules, then report to the user.

---

## Self-Review Notes

- **Spec coverage:** tab (T5/T6·S1), hotkey with combos (T1/T4/T6), focus-only (webview keydown, T6), no-fire on modal/setup/edit (T6·S4 gate), default F12 (T2/T4), shutter only when captured (T6·S3), clipboard copy (T3/T6), two default-on checkboxes (T2/T5), selectable folder + default (T2/T3/T5/T6), not in first-time setup (T6·S1). All mapped.
- **Placeholder scan:** none — every code step is concrete.
- **Type consistency:** `save_screenshot(bytes, stamp, dir?)`, `copy_screenshot_to_clipboard(bytes)`, `formatKeybind`/`matchesKeybind`/`prettyKeybind`, `loadScreenshotPrefs` and the four store names are used identically across tasks.
