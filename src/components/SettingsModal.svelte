<script>
  // SettingsModal.svelte - returning-user Settings modal shell.
  //
  // Rendering decisions:
  //   • Shown when wizardOpen===true, which only happens post-setup (the title-bar
  //     gear calls openSettings → openWizard with setupComplete===true). First-time
  //     setup uses the full-screen inline view in App.svelte, not this modal.
  //   • Two tabs (RERUN_STEPS = ["language","camera"]): LanguageSelectors on the
  //     "language" tab; SourceCheck + DeviceSelectors on the "camera" tab.
  //
  // The <video> element for the browser feed is rendered here (inside SourceCheck via
  // bind:videoEl) so that App.svelte's reactive statement
  //   `$: if (wizVideoEl) wizVideoEl.srcObject = videoStream ?? null`
  // continues to work - App passes `bind:wizVideoEl` which maps to `bind:videoEl` here.

  import SourceCheck       from "./SourceCheck.svelte";
  import DeviceSelectors   from "./DeviceSelectors.svelte";
  import LanguageSelectors from "./LanguageSelectors.svelte";
  import TrailSettings     from "./TrailSettings.svelte";
  import SyncSettings      from "./SyncSettings.svelte";
  import ChipsSettings     from "./ChipsSettings.svelte";
  import { invoke }        from "@tauri-apps/api/core";
  import { discordEnabled, twitchButtonEnabled, twitchLabel, twitchUrl } from "../lib/discordSettings.js";
  import { deltaMode } from "../lib/cardSettings.js";
  import KeybindRecorder from "./KeybindRecorder.svelte";
  import { screenshotKeybind, screenshotSaveFile, screenshotClipboard, screenshotDir } from "../lib/screenshotSettings.js";
  import { open as openDialog, ask } from "@tauri-apps/plugin-dialog";

  async function chooseScreenshotDir() {
    try {
      const picked = await openDialog({ directory: true, title: "Choose screenshot folder" });
      if (typeof picked === "string" && picked) screenshotDir.set(picked);
    } catch (_) { /* dialog cancelled/unavailable */ }
  }

  // The only data-delete path in the product (replaces the old NSIS uninstall
  // checkbox, spec 2026-07-19 §5) - uninstalling pbenguin never touches app data.
  async function deleteAppData() {
    const yes = await ask(
      "Delete ALL pbenguin data — settings, replays, minimap tuning — and quit?\nThis cannot be undone.",
      { title: "Delete app data", kind: "warning", okLabel: "Delete and quit", cancelLabel: "Cancel" },
    );
    if (yes) invoke("delete_app_data").catch((e) => console.error("delete_app_data failed", e));
  }

  // Open the save folder in File Explorer (browse only). Empty dir → the default
  // Pictures\pbenguin, resolved backend-side.
  function openScreenshotDir() {
    invoke("open_screenshot_dir", { dir: $screenshotDir || null }).catch(() => {});
  }

  // ── Background modes (spec 2026-07-17 §3) ────────────────────────────────────
  // camelCase mirror of the Rust store; keys sent back are the snake_case store keys.
  let bg = { closeToTray: false, startAtLogin: false, runWrService: false, keepTrackingInTray: false };
  const BG_KEYS = {
    closeToTray: "close_to_tray",
    startAtLogin: "start_at_login",
    runWrService: "run_wr_service",
    keepTrackingInTray: "keep_tracking_in_tray",
  };
  async function loadBg() {
    try { bg = await invoke("wr_get_settings"); } catch { /* pre-first-run: defaults stand */ }
  }
  async function setBg(field, value) {
    bg = { ...bg, [field]: value };                    // optimistic; store is the truth on reopen
    try { await invoke("wr_set_setting", { key: BG_KEYS[field], value }); }
    catch (e) { console.error("wr_set_setting failed", e); loadBg(); }
  }
  loadBg();

  // ── Modal open/close ──────────────────────────────────────────────────────────
  export let wizardOpen    = false;
  export let setupComplete = false;

  // ── Step gating ───────────────────────────────────────────────────────────────
  export let wizardStep = "language";   // "language" | "camera"
  export let STEPS      = [];
  export let STEP_LABELS = {};

  export let onGoStep   = (step) => {};
  export let onClose    = ()     => {};

  // ── Camera / device state (read-only from App) ────────────────────────────────
  export let wizVideoEl            = null;   // bind:wizVideoEl → SourceCheck bind:videoEl
  export let cameraOk              = false;
  export let cameraStatus          = "idle";
  export let trackerCameraPaused   = false;
  export let engineFrame           = null;
  export let pythonCameraOk        = false;
  export let pythonCameraStatus    = "idle";
  export let pythonCameraError     = "";
  export let trackerConnected      = false;
  export let bothCamerasOk         = false;
  export let browserDevices        = [];
  export let selectedBrowserDeviceId = "";
  export let audioDevices          = [];
  export let selectedAudioDeviceId = "";
  export let restartNeeded         = false;

  // ── Language state ────────────────────────────────────────────────────────────
  export let LANGUAGES       = [];
  export let appLanguage     = "en_uk";
  export let switch2Language = "en_uk";

  // ── Callbacks (existing App.svelte handlers, unchanged) ───────────────────────
  export let onCameraDeviceChange    = (e) => {};
  export let onAudioDeviceChange     = (e) => {};
  export let onRestartTracker        = ()  => {};
  export let onReleaseForSettings    = ()  => {};
  export let onRetryNow              = ()  => {};
  export let onAppLanguageChange     = ()  => {};
  export let onSwitch2LanguageChange = ()  => {};
</script>

{#if wizardOpen}
  <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
  <div class="modal-backdrop wiz-backdrop"
    on:keydown|self={(e) => { if (e.key === 'Escape' && setupComplete) onClose(); }}
    role="dialog" aria-modal="true"
    tabindex="-1">
    <div class="wiz-dialog">

      <!-- Wizard tab bar -->
      <nav class="wiz-tabs">
        {#each STEPS as s}
          <button class="wiz-tab" class:active={wizardStep === s} on:click={() => onGoStep(s)}>
            {STEP_LABELS[s]}
          </button>
        {/each}
        {#if setupComplete}
          <button class="wiz-tab-close" on:click={onClose} title="Close">✕</button>
        {/if}
      </nav>

      <div class="wiz-body">

        <!-- ── LANGUAGE step ──────────────────────────────────────────────── -->
        {#if wizardStep === "language"}
          <div class="step-centred">
            <h2>Language</h2>
            <p>Choose the language used in the app and the language of your Nintendo Switch system.</p>
            <LanguageSelectors
              {LANGUAGES}
              bind:appLanguage
              bind:switch2Language
              {onAppLanguageChange}
              {onSwitch2LanguageChange}
              idPrefix="wiz"
            />

            <!-- Background modes: all default off = today's behaviour exactly. -->
            <div class="bg-section">
              <h3>Background</h3>
              <label class="bg-row">
                <input type="checkbox" checked={bg.closeToTray}
                       on:change={(e) => setBg("closeToTray", e.currentTarget.checked)} />
                Close to tray instead of quitting
              </label>
              <label class="bg-row">
                <input type="checkbox" checked={bg.startAtLogin}
                       on:change={(e) => setBg("startAtLogin", e.currentTarget.checked)} />
                Start pbenguin at login
              </label>
              <label class="bg-row">
                <input type="checkbox" checked={bg.runWrService}
                       on:change={(e) => setBg("runWrService", e.currentTarget.checked)} />
                Run the WR service
              </label>
              <div class="bg-hint">Processes WR videos only while tracking is stopped or idle 10 min+.</div>
              <div class="bg-subhead">When in tray:</div>
              <label class="bg-row bg-indent">
                <input type="checkbox" checked={bg.keepTrackingInTray}
                       on:change={(e) => setBg("keepTrackingInTray", e.currentTarget.checked)} />
                Keep live tracking running
              </label>
            </div>

            <div class="bg-section">
              <h3>Data</h3>
              <button class="btn-sm btn-danger" on:click={deleteAppData}>Delete all app data…</button>
              <div class="bg-hint">Removes settings, replays and tuning, then quits. Uninstalling pbenguin keeps this data.</div>
            </div>

            <button class="btn-primary btn-lg" on:click={() => onGoStep("camera")}>Continue</button>
          </div>

        <!-- ── CAMERA step ────────────────────────────────────────────────── -->
        {:else if wizardStep === "camera"}
          <div class="cam-setup">
            <SourceCheck
              bind:videoEl={wizVideoEl}
              {cameraOk}
              {cameraStatus}
              {trackerCameraPaused}
              {engineFrame}
              {pythonCameraOk}
              {pythonCameraStatus}
              {pythonCameraError}
              {trackerConnected}
            />

            <div class="cam-below">
              <DeviceSelectors
                {browserDevices}
                {selectedBrowserDeviceId}
                {audioDevices}
                {selectedAudioDeviceId}
                {pythonCameraStatus}
                {cameraStatus}
                {restartNeeded}
                onCameraDeviceChange={onCameraDeviceChange}
                onAudioDeviceChange={onAudioDeviceChange}
                onRestartTracker={onRestartTracker}
              />

              {#if !setupComplete}
                <div class="cam-prereq" class:cam-prereq-ok={bothCamerasOk}>
                  {#if bothCamerasOk}
                    <span class="cam-prereq-title cam-prereq-title-ok">Camera sharing enabled</span>
                    <p class="cam-prereq-body">Both feeds are reading the same device.</p>
                  {:else}
                    <span class="cam-prereq-title">Camera sharing · required</span>
                    <p class="cam-prereq-body">pbenguin needs simultaneous access to the same capture card as the app preview. Windows blocks this by default. Set it once before continuing:</p>
                    {#if trackerCameraPaused}
                      <div class="cam-release-bar cam-release-bar-released">
                        <span class="cam-release-dot"></span>
                        <span class="cam-release-msg">App feeds released - also close OBS, Discord, and any other apps currently using the camera before proceeding.</span>
                      </div>
                    {:else}
                      <div class="cam-release-bar">
                        <span class="cam-release-dot"></span>
                        <span class="cam-release-msg">Release this app's feeds and close OBS, Discord, and any other apps currently using the camera before changing this setting.</span>
                        <div style="display:flex;gap:.4rem;flex-shrink:0">
                          <button class="btn-sm" on:click={onReleaseForSettings}>Release feeds</button>
                          <button class="btn-sm" on:click={onRetryNow}>Retry</button>
                        </div>
                      </div>
                    {/if}
                    <ol class="cam-steps">
                      <li>Click <strong>Open Windows camera settings</strong> below</li>
                      <li>Find your capture card, open <strong>Advanced camera options</strong>, then <strong>Edit</strong></li>
                      <li>Turn on <strong>"Allow multiple apps to use camera at the same time"</strong></li>
                      <li>Return here, then <button class="btn-sm" on:click={onRetryNow}>Retry</button></li>
                    </ol>
                    <div class="cam-prereq-actions">
                      <button class="btn-primary" on:click={() => invoke("open_url", { url: "ms-settings:camera" }).catch(() => {})}>Open Windows camera settings</button>
                    </div>
                  {/if}
                </div>
              {/if}

              <div class="cam-actions">
                <p class="hint">Both feeds must show your capture card output before you can continue.</p>
                <div class="cam-nav">
                  <button class="btn-nav" on:click={() => onGoStep("language")}>Back</button>
                  <button class="btn-primary" on:click={onClose}>Done</button>
                </div>
              </div>
            </div>
          </div>

        <!-- ── DISCORD step ───────────────────────────────────────────────── -->
        {:else if wizardStep === "discord"}
          <div class="step-centred">
            <h2>Discord</h2>
            <p>Show your current Mario Kart World screen, course and run as a Discord status. The elapsed time counts from when pbenguin launched.</p>

            <label class="discord-row discord-master">
              <input type="checkbox" bind:checked={$discordEnabled} />
              <span>Enable Discord Rich Presence</span>
            </label>

            <div class="discord-section" class:discord-dim={!$discordEnabled}>
              <h3 class="discord-heading">Twitch button</h3>
              <label class="discord-row">
                <input type="checkbox" bind:checked={$twitchButtonEnabled} disabled={!$discordEnabled} />
                <span>Show a button linking to your stream</span>
              </label>
              <div class="discord-fields" class:discord-dim={!$discordEnabled || !$twitchButtonEnabled}>
                <label class="discord-label" for="tw-label">Button text</label>
                <input id="tw-label" class="discord-input" type="text" bind:value={$twitchLabel}
                  placeholder="Watch on Twitch" disabled={!$discordEnabled || !$twitchButtonEnabled} />
                <label class="discord-label" for="tw-url">Links to</label>
                <input id="tw-url" class="discord-input" type="text" bind:value={$twitchUrl}
                  placeholder="https://twitch.tv/yourname" disabled={!$discordEnabled || !$twitchButtonEnabled} />
              </div>
              <p class="discord-note">Appears on every screen except idle. You may not see it on your own profile, but others will.</p>
            </div>

            <div class="cam-nav" style="justify-content:flex-end">
              <button class="btn-primary" on:click={onClose}>Done</button>
            </div>
          </div>

        <!-- ── SYNC step ──────────────────────────────────────────────────── -->
        {:else if wizardStep === "sync"}
          <div class="step-centred">
            <SyncSettings />
            <div class="cam-nav" style="justify-content:flex-end">
              <button class="btn-primary" on:click={onClose}>Done</button>
            </div>
          </div>

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
                <button class="btn-sm" on:click={openScreenshotDir} title="Open this folder in File Explorer">Open in Explorer</button>
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

        <!-- ── TRAILS step ────────────────────────────────────────────────── -->
        {:else if wizardStep === "trails"}
          <TrailSettings />

          <div class="delta-set">
            <h2>PB delta</h2>
            <p>How the ± readout next to PB on the player cards updates during a race.</p>
            <label class="dm">
              <input type="radio" name="deltamode" value="pace"
                checked={$deltaMode === "pace"} on:change={() => deltaMode.set("pace")} />
              <span><b>Pace (fluid)</b><i>Continuous estimate from track position against your PB run, updated every check.</i></span>
            </label>
            <label class="dm">
              <input type="radio" name="deltamode" value="laps"
                checked={$deltaMode === "laps"} on:change={() => deltaMode.set("laps")} />
              <span><b>Lap splits</b><i>Updates only at lap lines from the read lap times. LiveSplit colours: red losing/behind, light red gaining/behind, light green losing/ahead, green gaining/ahead, gold best-ever lap.</i></span>
            </label>
          </div>

          <div class="cam-nav" style="justify-content:flex-end; max-width:600px; margin:.6rem auto 0;">
            <button class="btn-primary" on:click={onClose}>Done</button>
          </div>

        <!-- ── CHIPS step ─────────────────────────────────────────────────── -->
        {:else if wizardStep === "chips"}
          <ChipsSettings />

        {/if}

      </div><!-- /wiz-body -->

    </div><!-- /wiz-dialog -->
  </div><!-- /wiz-backdrop -->
{/if}

<style>
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
  }
  .wiz-backdrop { align-items: stretch; padding: 32px; }

  /* One fixed size for every tab — the dialog never resizes as you switch tabs.
     Sized to the widest/tallest tab (Video); shorter tabs centre their content and
     the body scrolls if a tab ever exceeds the height. */
  .wiz-dialog {
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    display: flex; flex-direction: column; overflow: hidden;
    width: 100%; max-width: 960px; height: 680px; max-height: 100%;
    align-self: center; margin: auto;
  }

  .wiz-tabs {
    display: flex; flex-shrink: 0; background: var(--panel);
    border-bottom: 1px solid var(--bd); overflow-x: auto; scrollbar-width: none;
  }
  .wiz-tabs::-webkit-scrollbar { display: none; }   /* keep this strip's bar hidden on Chromium < 121 too */
  .wiz-tab {
    background: transparent; color: var(--tx-dim); border: none;
    border-right: 1px solid var(--bd);
    padding: 7px 14px; font-family: inherit; font-size: .7rem;
    cursor: pointer; white-space: nowrap; transition: color .12s, background .12s;
  }
  .wiz-tab:hover { background: var(--panel-2); color: var(--tx-mut); }
  /* Active tab: neutral text + thin accent underline (matches ToolsPanel .tab.on). */
  .wiz-tab.active { color: var(--tx); box-shadow: inset 0 -2px 0 var(--accent); }
  .wiz-tab-close {
    margin-left: auto; background: transparent; color: var(--tx-dim); border: none;
    padding: 7px 14px; font-family: inherit; font-size: .78rem; cursor: pointer;
    transition: color .12s;
  }
  .wiz-tab-close:hover { color: var(--tx-mut); }

  .wiz-body { flex: 1; overflow: auto; padding: 1rem; min-height: 0; }

  .step-centred {
    max-width: 560px; margin: 0 auto; padding: .5rem 0;
    display: flex; flex-direction: column; gap: .75rem;
  }
  .step-centred h2 { color: var(--tx); font-size: .95rem; font-weight: 600; letter-spacing: .01em; }
  .step-centred p  { font-size: .76rem; color: var(--tx-mut); line-height: 1.6; }

  /* Camera step layout */
  .cam-setup { display: flex; flex-direction: column; gap: .9rem; }
  .cam-below { display: flex; flex-direction: column; gap: .65rem; }
  .cam-actions { display: flex; flex-direction: column; gap: .3rem; }
  .cam-nav { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }

  .cam-prereq {
    padding: .55rem .7rem; border-radius: var(--r);
    background: var(--panel-2); border: 1px solid var(--bd);
    display: flex; flex-direction: column; gap: .3rem;
  }
  .cam-prereq-title     { font-size: .63rem; color: var(--tx-mut); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
  .cam-prereq-title-ok  { color: var(--ok); }
  .cam-prereq-ok        { background: rgba(90,168,106,.05); border-color: rgba(90,168,106,.2); }
  .cam-prereq-body      { font-size: .68rem; color: var(--tx-dim); margin: 0; line-height: 1.55; }
  .cam-prereq-actions   { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-top: .15rem; }

  .cam-release-bar {
    display: flex; align-items: center; gap: .55rem;
    padding: .38rem .55rem; border-radius: var(--r);
    background: rgba(200,154,62,.05); border: 1px solid rgba(200,154,62,.18);
    transition: background .25s, border-color .25s;
  }
  .cam-release-bar-released {
    background: rgba(90,168,106,.05); border-color: rgba(90,168,106,.2);
  }
  .cam-release-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: var(--warn); transition: background .25s;
  }
  .cam-release-bar-released .cam-release-dot { background: var(--ok); }
  .cam-release-msg { flex: 1; font-size: .66rem; color: var(--warn); line-height: 1.45; transition: color .25s; }
  .cam-release-bar-released .cam-release-msg { color: var(--ok); }
  .cam-steps {
    margin: .15rem 0 .05rem; padding-left: 1.2rem;
    font-size: .68rem; color: var(--tx-dim); line-height: 1.8;
  }
  .cam-steps strong { color: var(--tx-mut); }

  /* Buttons */
  /* Primary action: neutral text on a subtle accent-tint fill + accent border
     (the app's .reg.sel idiom), never blue text. */
  .btn-primary {
    background: var(--accent-bg); color: var(--tx); border: 1px solid var(--accent); border-radius: var(--r);
    padding: .28rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; white-space: nowrap; transition: background .12s, border-color .12s;
  }
  .btn-primary:hover:not(:disabled) { background: var(--raised); }
  .btn-primary:disabled { opacity: .35; cursor: default; }
  .btn-primary.btn-lg { padding: .45rem 1.1rem; font-size: .85rem; margin-top: .5rem; }
  .btn-nav {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .24rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-nav:hover { background: var(--raised); color: var(--tx); }
  .btn-sm {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .16rem .45rem; font-family: inherit; font-size: .68rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-sm:hover { background: var(--raised); color: var(--tx); }
  .btn-danger { color: var(--err, #cf5b4e); border-color: rgba(207,91,78,.35); }
  .btn-danger:hover { background: rgba(207,91,78,.12); color: var(--err, #cf5b4e); }

  .hint { font-size: .7rem; color: var(--tx-dim); margin: 0; line-height: 1.55; }

  /* Discord section */
  .discord-section {
    display: flex; flex-direction: column; gap: .35rem;
    padding: .55rem .7rem; border-radius: var(--r);
    background: var(--panel-2); border: 1px solid var(--bd);
  }
  .discord-heading {
    font-size: .63rem; color: var(--tx-mut); font-weight: 600;
    text-transform: uppercase; letter-spacing: .06em; margin: 0;
  }
  .discord-row {
    display: flex; align-items: center; gap: .45rem;
    font-size: .72rem; color: var(--tx); cursor: pointer;
  }
  .discord-label { font-size: .7rem; color: var(--tx-dim); margin: .1rem 0 0; }
  .discord-input {
    background: var(--panel); color: var(--tx); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .22rem .45rem;
    font-family: inherit; font-size: .72rem; width: 100%; box-sizing: border-box;
    transition: border-color .12s;
  }
  .discord-input:focus { outline: none; border-color: var(--accent); }
  .discord-input::placeholder { color: var(--tx-dim); }
  .discord-input:disabled { opacity: .6; cursor: not-allowed; }
  .discord-master { font-size: .8rem; }
  .discord-fields { display: flex; flex-direction: column; gap: .35rem; }
  .discord-note { font-size: .66rem; color: var(--tx-dim); margin: .1rem 0 0; line-height: 1.5; }
  .discord-dim { opacity: .45; transition: opacity .15s; }

  /* Screenshots tab */
  .ss-row { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
  .ss-path {
    font-family: inherit; font-size: .7rem; color: var(--tx-mut);
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .22rem .5rem; word-break: break-all;
  }

  /* PB delta mode (trails tab) */
  .delta-set { max-width: 600px; margin: 1rem auto 0; padding-top: .8rem; border-top: 1px solid var(--bd); }
  .delta-set h2 { font-size: .8rem; color: var(--tx); margin: 0 0 .2rem; }
  .delta-set p { font-size: .7rem; color: var(--tx-dim); margin: 0 0 .55rem; line-height: 1.55; }
  .dm { display: flex; align-items: flex-start; gap: .5rem; cursor: pointer; padding: .25rem 0; }
  .dm input[type="radio"] { margin-top: .15rem; }
  .dm b { font-size: .72rem; color: var(--tx); font-weight: 600; display: block; }
  .dm i { font-size: .66rem; color: var(--tx-dim); font-style: normal; line-height: 1.5; display: block; }

  /* Background modes (language step, returning-user settings) */
  .bg-section { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--bd); }
  .bg-section h3 { margin: 0 0 8px; font-size: .8rem; color: var(--tx); }
  .bg-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; cursor: pointer; font-size: .72rem; color: var(--tx); }
  .bg-hint { font-size: .7rem; color: var(--tx-dim); margin: 0 0 6px 24px; line-height: 1.55; }
  .bg-subhead { font-size: .7rem; color: var(--tx-dim); margin-top: 6px; }
  .bg-indent { margin-left: 16px; }
</style>
