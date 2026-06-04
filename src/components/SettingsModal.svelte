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
  import { invoke }        from "@tauri-apps/api/core";
  import { discordEnabled, twitchButtonEnabled, twitchLabel, twitchUrl } from "../lib/discordSettings.js";
  import { serverUrl, authToken, cc } from "../lib/syncSettings.js";

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
    <div class="wiz-dialog" class:wiz-dialog-narrow={wizardStep === "language" || wizardStep === "discord" || wizardStep === "sync"}>

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
            <h2>Sync</h2>
            <p>Upload your runs to the competition server so they appear on the leaderboard and broadcast. Get your token from whoever runs the server.</p>

            <div class="discord-fields">
              <label class="discord-label" for="sync-url">Server URL</label>
              <input id="sync-url" class="discord-input" type="text" bind:value={$serverUrl}
                placeholder="https://your-server.example" />
              <label class="discord-label" for="sync-token">Your token</label>
              <input id="sync-token" class="discord-input" type="password" bind:value={$authToken}
                placeholder="paste your token" />
              <label class="discord-label" for="sync-cc">Engine class (cc)</label>
              <input id="sync-cc" class="discord-input" type="number" bind:value={$cc} min="50" step="50" />
            </div>
            <p class="discord-note">Runs queue locally and upload when the server is reachable, so a flaky connection is fine. Leave the URL blank to disable uploading.</p>

            <div class="cam-nav" style="justify-content:flex-end">
              <button class="btn-primary" on:click={onClose}>Done</button>
            </div>
          </div>

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

  .wiz-dialog {
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    display: flex; flex-direction: column; overflow: hidden;
    width: 100%; max-width: 960px; max-height: 100%; align-self: center; margin: auto;
    transition: max-width .2s ease;
  }
  .wiz-dialog-narrow { max-width: 480px; }

  .wiz-tabs {
    display: flex; flex-shrink: 0; background: var(--panel);
    border-bottom: 1px solid var(--bd); overflow-x: auto; scrollbar-width: none;
  }
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
  .discord-row input[type="checkbox"] { accent-color: var(--accent); cursor: pointer; }
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
</style>
