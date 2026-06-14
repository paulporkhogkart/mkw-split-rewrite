# Sync step in first-time setup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a skippable Sync step to first-time setup, with the server URL pre-filled to `https://api.thekartoff.com` and the token field password-masked.

**Architecture:** Pre-fill the URL via a pure `resolveServerUrl()` helper in `src/lib/syncSettings.js` (first-run-only default). Extract the existing modal sync UI (fields + note + "Test connection") into a self-contained `src/components/SyncSettings.svelte`, used by both `SettingsModal.svelte` and the inline first-time-setup view in `App.svelte`. Each parent supplies its own nav buttons.

**Tech Stack:** Svelte 4, Vite/Vitest, Tauri (`@tauri-apps/api/core` `invoke`).

**Spec:** `docs/superpowers/specs/2026-06-14-sync-first-time-setup-design.md`

---

### Task 1: Pre-fill the server URL on first run (`syncSettings.js`)

**Files:**
- Modify: `src/lib/syncSettings.js`
- Test: `src/lib/syncSettings.test.js` (create)

Extract the default-resolution into a pure, exported helper so it is unit-testable
without mocking `localStorage` or resetting modules — mirroring how
`trailSettings.js` exports pure helpers.

- [ ] **Step 1: Write the failing test**

Create `src/lib/syncSettings.test.js`:

```js
import { describe, it, expect } from "vitest";
import { resolveServerUrl, DEFAULT_SERVER_URL } from "./syncSettings.js";

describe("resolveServerUrl", () => {
  it("absent key (null) → deploy default URL (first run pre-fill)", () => {
    expect(resolveServerUrl(null)).toBe(DEFAULT_SERVER_URL);
    expect(DEFAULT_SERVER_URL).toBe("https://api.thekartoff.com");
  });

  it("deliberately cleared (\"\") → stays blank (uploading disabled)", () => {
    expect(resolveServerUrl("")).toBe("");
  });

  it("stored value → returned verbatim", () => {
    expect(resolveServerUrl("https://example.test")).toBe("https://example.test");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/syncSettings.test.js`
Expected: FAIL — `resolveServerUrl`/`DEFAULT_SERVER_URL` are not exported.

- [ ] **Step 3: Implement the helper and use it for the store default**

In `src/lib/syncSettings.js`, replace the `serverUrl` declaration. Current:

```js
export const serverUrl = writable(ls.getItem(URL_KEY) || "");
export const authToken = writable(ls.getItem(TOKEN_KEY) || "");
```

becomes:

```js
// The friend-group competition server (see docs/pi-deploy.md step 8). Pre-filled on
// first run only: an absent key gets the default, but a deliberately-cleared "" stays
// blank so "leave the URL blank to disable uploading" keeps working.
export const DEFAULT_SERVER_URL = "https://api.thekartoff.com";
export function resolveServerUrl(stored) {
  return stored === null ? DEFAULT_SERVER_URL : stored;
}

export const serverUrl = writable(resolveServerUrl(ls.getItem(URL_KEY)));
export const authToken = writable(ls.getItem(TOKEN_KEY) || "");
```

Leave the existing `subscribe` writers and `safeStorage()` unchanged. (Note:
`safeStorage()`'s fallback `getItem: () => null` correctly yields the default under
Node, which is fine — the store value is only consumed in the browser/Tauri runtime.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/syncSettings.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/syncSettings.js src/lib/syncSettings.test.js
git commit -m "feat(sync): pre-fill server URL to deploy default on first run"
```

---

### Task 2: Extract `SyncSettings.svelte` and use it in the modal

**Files:**
- Create: `src/components/SyncSettings.svelte`
- Modify: `src/components/SettingsModal.svelte`

The component is self-contained (heading, intro, fields, note, "Test connection"
button + its state machine) with its own scoped styles, so both parents render
identical sync UI and only add their own nav. This is a UI/refactor task; verify
with `svelte-check` (no unit test — the moved logic is unchanged).

- [ ] **Step 1: Create `src/components/SyncSettings.svelte`**

```svelte
<script>
  // SyncSettings.svelte - the "Sync" settings block: server URL + token + a
  // "Test connection" button. Self-contained (heading/intro/note/styles) so it is
  // identical in the first-time setup view (App.svelte) and the returning-user
  // Settings modal; each parent supplies its own nav buttons. Persists to
  // syncSettings (localStorage), decoupled from the Python config.
  import { invoke } from "@tauri-apps/api/core";
  import { serverUrl, authToken } from "../lib/syncSettings.js";
  import { pushSyncConfig } from "../lib/sync.js";

  let syncTest = { state: "idle", msg: "" };   // idle | testing | ok | err
  async function testSyncConnection() {
    syncTest = { state: "testing", msg: "" };
    try {
      await pushSyncConfig();   // ensure the latest URL/token are in the uploader first
      const msg = await invoke("sync_test_connection");
      syncTest = { state: "ok", msg };
    } catch (e) {
      syncTest = { state: "err", msg: typeof e === "string" ? e : (e?.message ?? String(e)) };
    }
  }
</script>

<div class="sync">
  <h2>Sync</h2>
  <p>Upload your runs to the competition server so they appear on the leaderboard and broadcast. Get your token from whoever runs the server.</p>

  <div class="fields">
    <label class="label" for="sync-url">Server URL</label>
    <input id="sync-url" class="input" type="text" bind:value={$serverUrl}
      placeholder="https://your-server.example" />
    <label class="label" for="sync-token">Your token</label>
    <input id="sync-token" class="input" type="password" bind:value={$authToken}
      placeholder="paste your token" />
  </div>
  <p class="note">Runs queue locally and upload when the server is reachable, so a flaky connection is fine. Leave the URL blank to disable uploading.</p>

  <div class="test">
    <button class="btn-nav" on:click={testSyncConnection} disabled={syncTest.state === "testing"}>
      {syncTest.state === "testing" ? "Testing…" : "Test connection"}
    </button>
    {#if syncTest.state === "ok"}
      <p class="test-msg test-ok">{syncTest.msg}</p>
    {:else if syncTest.state === "err"}
      <p class="test-msg test-err">{syncTest.msg}</p>
    {/if}
  </div>
</div>

<style>
  .sync { display: flex; flex-direction: column; gap: .75rem; }
  .sync h2 { color: var(--tx); font-size: .95rem; font-weight: 600; letter-spacing: .01em; }
  .sync p  { font-size: .76rem; color: var(--tx-mut); line-height: 1.6; margin: 0; }

  .fields { display: flex; flex-direction: column; gap: .35rem; }
  .label {
    font-size: .7rem; color: var(--tx-dim); margin: .1rem 0 0;
  }
  .input {
    background: var(--panel); color: var(--tx); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .22rem .45rem;
    font-family: inherit; font-size: .72rem; width: 100%; box-sizing: border-box;
    transition: border-color .12s;
  }
  .input:focus { outline: none; border-color: var(--accent); }
  .input::placeholder { color: var(--tx-dim); }
  .note { font-size: .66rem; color: var(--tx-dim); line-height: 1.5; }

  .btn-nav {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .24rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-nav:hover { background: var(--raised); color: var(--tx); }
  .btn-nav:disabled { opacity: .35; cursor: default; }

  .test { display: flex; flex-direction: column; gap: .4rem; margin-top: .2rem; align-items: flex-start; }
  .test-msg { font-size: .68rem; line-height: 1.5; margin: 0; }
  .test-ok  { color: var(--ok); }
  .test-err { color: var(--warn); }
</style>
```

- [ ] **Step 2: Wire the component into `SettingsModal.svelte` — imports**

In `src/components/SettingsModal.svelte`, in the `<script>` import block, add the
new component import and remove imports that move into it. Current relevant lines:

```js
  import LanguageSelectors from "./LanguageSelectors.svelte";
  import TrailSettings     from "./TrailSettings.svelte";
  import { invoke }        from "@tauri-apps/api/core";
  import { discordEnabled, twitchButtonEnabled, twitchLabel, twitchUrl } from "../lib/discordSettings.js";
  import { serverUrl, authToken } from "../lib/syncSettings.js";
  import { deltaMode } from "../lib/cardSettings.js";
  import { pushSyncConfig } from "../lib/sync.js";
```

becomes (keep `invoke` — the camera step still uses it for `open_url`; drop
`serverUrl`/`authToken`/`pushSyncConfig` — now only used inside `SyncSettings`):

```js
  import LanguageSelectors from "./LanguageSelectors.svelte";
  import TrailSettings     from "./TrailSettings.svelte";
  import SyncSettings      from "./SyncSettings.svelte";
  import { invoke }        from "@tauri-apps/api/core";
  import { discordEnabled, twitchButtonEnabled, twitchLabel, twitchUrl } from "../lib/discordSettings.js";
  import { deltaMode } from "../lib/cardSettings.js";
```

- [ ] **Step 3: Remove the now-dead "Test connection" state from `SettingsModal.svelte`**

Delete this block (the `// ── Sync "Test connection" ──` comment through the end of
`testSyncConnection`):

```js
  // ── Sync "Test connection" ────────────────────────────────────────────────────
  let syncTest = { state: "idle", msg: "" };   // idle | testing | ok | err
  async function testSyncConnection() {
    syncTest = { state: "testing", msg: "" };
    try {
      await pushSyncConfig();   // ensure the latest URL/token are in the uploader first
      const msg = await invoke("sync_test_connection");
      syncTest = { state: "ok", msg };
    } catch (e) {
      syncTest = { state: "err", msg: typeof e === "string" ? e : (e?.message ?? String(e)) };
    }
  }
```

- [ ] **Step 4: Replace the inline sync markup in `SettingsModal.svelte`**

Replace the entire SYNC step body. Current:

```svelte
        <!-- ── SYNC step ──────────────────────────────────────────────────── -->
        {:else if wizardStep === "sync"}
          <div class="step-centred">
            <h2>Sync</h2>
            <p>Upload your runs to the competition server so they appear on the leaderboard and broadcast. Get your token from whoever runs the server.</p>

            <div class="discord-fields">
              <label class="discord-label" for="sync-url">Server URL</label>
              <input id="sync-url" class="discord-input" type="text" bind:value={$serverUrl}
                placeholder="https://your-server.example" />
              <label class="discord-label" for="sync-token">Your token</label>
              <input id="sync-token" class="discord-input" type="password" bind:value={$authToken}
                placeholder="paste your token" />
            </div>
            <p class="discord-note">Runs queue locally and upload when the server is reachable, so a flaky connection is fine. Leave the URL blank to disable uploading.</p>

            <div class="sync-test">
              <button class="btn-nav" on:click={testSyncConnection} disabled={syncTest.state === "testing"}>
                {syncTest.state === "testing" ? "Testing…" : "Test connection"}
              </button>
              {#if syncTest.state === "ok"}
                <p class="sync-test-msg sync-test-ok">{syncTest.msg}</p>
              {:else if syncTest.state === "err"}
                <p class="sync-test-msg sync-test-err">{syncTest.msg}</p>
              {/if}
            </div>

            <div class="cam-nav" style="justify-content:flex-end">
              <button class="btn-primary" on:click={onClose}>Done</button>
            </div>
          </div>
```

becomes:

```svelte
        <!-- ── SYNC step ──────────────────────────────────────────────────── -->
        {:else if wizardStep === "sync"}
          <div class="step-centred">
            <SyncSettings />
            <div class="cam-nav" style="justify-content:flex-end">
              <button class="btn-primary" on:click={onClose}>Done</button>
            </div>
          </div>
```

Leave the now-unused `.sync-test*` style rules in `SettingsModal.svelte` to be
cleaned up — actually delete them to avoid dead CSS. Remove these rules from the
`<style>` block:

```css
  /* Sync "Test connection" */
  .sync-test { display: flex; flex-direction: column; gap: .4rem; margin-top: .2rem; align-items: flex-start; }
  .sync-test-msg { font-size: .68rem; line-height: 1.5; margin: 0; }
  .sync-test-ok  { color: var(--ok); }
  .sync-test-err { color: var(--warn); }
```

(Keep the `.discord-*` rules — the Discord step still uses them.)

- [ ] **Step 5: Verify the modal compiles clean**

Run: `npm run check` (→ `svelte-check`)
Expected: 0 errors, 0 warnings. (Confirms no unused-import/var or missing-ref from
the moved code. Svelte flags unused CSS selectors as warnings, so this also catches
any leftover dead `.sync-test*`.)

- [ ] **Step 6: Commit**

```bash
git add src/components/SyncSettings.svelte src/components/SettingsModal.svelte
git commit -m "refactor(sync): extract SyncSettings.svelte, reuse in Settings modal"
```

---

### Task 3: Add the Sync step to first-time setup (`App.svelte`)

**Files:**
- Modify: `src/App.svelte`

- [ ] **Step 1: Import the component**

In `src/App.svelte`, after the `LanguageSelectors` import (line ~20), add:

```js
  import SyncSettings from "./components/SyncSettings.svelte";
```

- [ ] **Step 2: Add `sync` to the first-time steps**

Change (line ~449):

```js
  const FIRST_TIME_STEPS = ["language", "camera"];
```

to:

```js
  const FIRST_TIME_STEPS = ["language", "camera", "sync"];
```

(`STEP_LABELS.sync` already exists; `RERUN_STEPS` is unchanged.)

- [ ] **Step 3: Camera step — "Finish setup" becomes "Continue"**

In the inline setup view, change the camera step's nav button. Current
(line ~1677-1682):

```svelte
                <div class="cam-nav">
                  <button class="btn-nav" on:click={()=>goStep("language")}>Back</button>
                  <button class="btn-primary" disabled={!bothCamerasOk} on:click={completeSetup}>
                    Finish setup
                  </button>
                </div>
```

becomes (keep the `bothCamerasOk` gate so camera still can't be skipped; advance to
the new sync step instead of finishing):

```svelte
                <div class="cam-nav">
                  <button class="btn-nav" on:click={()=>goStep("language")}>Back</button>
                  <button class="btn-primary" disabled={!bothCamerasOk} on:click={()=>goStep("sync")}>
                    Continue
                  </button>
                </div>
```

- [ ] **Step 4: Add the Sync step branch**

In the inline setup view's `wiz-body`, the step chain currently ends:

```svelte
            </div>
          </div>

        {/if}
      </div>
    </div>
```

Insert a new `{:else if}` branch immediately before the closing `{/if}` (after the
camera step's closing `</div>`s, matching the indentation of the `{:else if
wizardStep === "camera"}` branch). The Sync step is skippable — "Finish setup" has
no `disabled` gate:

```svelte
        {:else if wizardStep === "sync"}
          <div class="step-centred">
            <SyncSettings />
            <div class="cam-nav">
              <button class="btn-nav" on:click={()=>goStep("camera")}>Back</button>
              <button class="btn-primary" on:click={completeSetup}>Finish setup</button>
            </div>
          </div>

        {/if}
```

(The setup view already styles `.step-centred`, `.cam-nav`, `.btn-nav`,
`.btn-primary`, so no new CSS is needed.)

- [ ] **Step 5: Verify the app compiles clean**

Run: `npm run check` (→ `svelte-check`)
Expected: 0 errors, 0 warnings.

- [ ] **Step 6: Verify the production build succeeds**

Run: `npm run build`
Expected: build completes, writes to `dist-ui/` with no errors.

- [ ] **Step 7: Commit**

```bash
git add src/App.svelte
git commit -m "feat(setup): add skippable Sync step to first-time setup"
```

---

### Task 4: Full suite + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full frontend test suite**

Run: `npm run test:js` (→ `vitest run`)
Expected: all tests pass, including the new `syncSettings.test.js` (3 tests).

- [ ] **Step 2: Confirm svelte-check is clean app-wide**

Run: `npm run check` (→ `svelte-check`)
Expected: 0 errors, 0 warnings.

---

## Manual verification (hand off to user)

- **Fresh first run** (clear `localStorage` or use a fresh profile): setup shows
  **Language → Video → Sync**. The Sync step shows the Server URL pre-filled to
  `https://api.thekartoff.com`; typing in the token field shows dots; "Finish setup"
  completes setup with a blank token.
- **Returning user:** Settings (gear) → **Sync** tab looks and behaves exactly as
  before (URL/token persisted, "Test connection" works, "Done" closes).
- **Blank-to-disable still works:** clear the Server URL, reopen the app — it stays
  blank (not re-defaulted).

## Self-Review notes

- **Spec coverage:** URL pre-fill (Task 1) · token masked `type="password"` (Task 2,
  carried into `SyncSettings`) · component extraction + modal reuse (Task 2) ·
  `FIRST_TIME_STEPS` + camera→sync→finish flow, skippable (Task 3) · null-vs-`""`
  unit test (Task 1) · svelte-check/build/vitest (Tasks 2–4). All spec sections map
  to a task.
- **Naming consistency:** `resolveServerUrl` / `DEFAULT_SERVER_URL` used identically
  in `syncSettings.js`, its test, and (via the store) `SyncSettings.svelte`.
- **No placeholders:** every code step shows the full before/after.
