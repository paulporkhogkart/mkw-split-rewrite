# Sync step in first-time setup — design

**Date:** 2026-06-14
**Status:** Approved, ready for implementation plan

## Goal

Add a **Sync** step to the first-time setup flow so a new user configures server
uploading during setup, not only later via the gear menu. The server URL is
pre-filled to the value from the deploy guide; the token field is password-masked.

## Background / current state

- First-time setup is a full-screen **inline view in `src/App.svelte`** (not the
  `SettingsModal`). Its steps are `FIRST_TIME_STEPS = ["language", "camera"]`. The
  camera step holds the **"Finish setup"** button (`completeSetup`).
- The returning-user **`SettingsModal.svelte`** already has a working `sync` step
  (`RERUN_STEPS = [..., "sync", ...]`): Server URL + token fields, an explanatory
  note, and a "Test connection" button. The token field is already
  `type="password"`. The "Test connection" state machine (`syncTest` +
  `testSyncConnection`) lives inline in `SettingsModal`.
- Sync settings persist to `localStorage` via `src/lib/syncSettings.js`
  (`serverUrl`, `authToken` writables), decoupled from the Python config.
- The deploy guide (`docs/pi-deploy.md`, step 8) specifies the server URL
  `https://api.thekartoff.com`; each player pastes their own minted token.

## Decisions

- **Skippable, not gated.** The user can click "Finish setup" with a blank token.
  This matches the existing "Leave the URL blank to disable uploading" behaviour —
  runs queue locally and upload once a token is set later in Settings → Sync.
- **Pre-fill the URL as the actual value** (not a placeholder) so it is used as-is.
- **Token left blank** (each person has their own).
- **Order:** `language → camera → sync`, sync last. "Finish setup" moves to the
  sync step; the camera step's button becomes "Continue".

## Changes

### 1. `src/lib/syncSettings.js` — pre-populate the URL on first run only

Default `serverUrl` to `https://api.thekartoff.com` **only when the key has never
been set**. The current `ls.getItem(URL_KEY) || ""` cannot be reused with a
non-empty default, because `||` would also re-apply the default to a deliberately
**cleared** (`""`) URL — breaking the documented "leave URL blank to disable
uploading." Distinguish absent (`null` → default) from cleared (`""` → stay blank):

```js
const DEFAULT_SERVER_URL = "https://api.thekartoff.com";
const storedUrl = ls.getItem(URL_KEY);
export const serverUrl = writable(storedUrl === null ? DEFAULT_SERVER_URL : storedUrl);
```

`authToken` is unchanged (defaults to blank). The existing `subscribe` →
`setItem` writes the resolved value back, so after first load the key is present
and the default no longer applies.

### 2. New `src/components/SyncSettings.svelte`

Self-contained component following the `TrailSettings.svelte` pattern. Contains:

- The Server URL field (`type="text"`, bound to `$serverUrl`).
- The token field (`type="password"`, bound to `$authToken`).
- The explanatory note ("Runs queue locally and upload when the server is
  reachable…").
- The **"Test connection"** button + its `syncTest` state machine and
  `testSyncConnection()` (moved verbatim from `SettingsModal`), which calls
  `pushSyncConfig()` then `invoke("sync_test_connection")`.

It does **not** render nav buttons (Done / Back / Finish) — each parent supplies
its own. Styling reuses the existing `discord-*` / `sync-test*` class idioms
(copied into the component's `<style>`), matching how the other extracted settings
components are self-styled.

### 3. `src/components/SettingsModal.svelte`

- Replace the inline `{:else if wizardStep === "sync"}` field markup + note +
  test block with `<SyncSettings />`, keeping the heading, intro `<p>`, and the
  existing "Done" nav button.
- Remove the now-unused `syncTest` state, `testSyncConnection`, and the
  `pushSyncConfig` / `invoke` imports if no longer referenced elsewhere in the
  file (verify before deleting). Keep `serverUrl`/`authToken` imports only if
  still used (they will move into the component, so likely removable).

### 4. `src/App.svelte`

- `FIRST_TIME_STEPS = ["language", "camera", "sync"]`. (`STEP_LABELS.sync`
  already exists.)
- Import `SyncSettings`.
- Camera step (setup view): change the **"Finish setup"** button to **"Continue"**
  → `goStep("sync")`. (The camera-sharing gate `disabled={!bothCamerasOk}` stays
  on this button so the user still can't advance past camera without both feeds.)
- Add a new `{:else if wizardStep === "sync"}` branch in the inline setup view
  rendering `<SyncSettings />` inside the `step-centred` layout, with nav: **Back**
  → `goStep("camera")` and **"Finish setup"** → `completeSetup`. No gating on the
  Finish button.

## Out of scope

- No i18n: the camera and sync steps already use hardcoded English; sync follows
  suit (only the language step uses `tr(...)`).
- No change to the Python/Rust sync pipeline or the `sync_test_connection` Tauri
  command.

## Testing

- `npx svelte-check` → expect 0 errors / 0 warnings.
- Add a unit test for `syncSettings.js` covering the default logic: key absent
  (`getItem` → `null`) yields the default URL; key present-but-empty (`""`) stays
  blank; key present with a value is returned verbatim. (Reset module state /
  mock `localStorage` per the existing test setup.)
- `npm run build` succeeds.
- Existing vitest suite stays green.

## Manual verification (user)

- Fresh first run: setup shows Language → Video → **Sync**; Sync shows the URL
  pre-filled to `https://api.thekartoff.com`, token field shows dots when typed;
  "Finish setup" works with a blank token.
- Returning user: Settings → Sync looks and behaves exactly as before.
