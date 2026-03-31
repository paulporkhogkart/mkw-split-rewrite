<script>
  import { onMount, onDestroy } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
  import { getVersion } from "@tauri-apps/api/app";
  import { invoke } from "@tauri-apps/api/core";

  let trackerStatus = "Connecting to tracker...";
  let version = "";
  let logs = [];
  let logEl;

  let devices = [];
  let configuredDevice = "";
  let activeDevice = "";
  let restartNeeded = false;
  let unlisten;

  // Update state
  let pendingUpdate = null;
  let updateVersion = "";
  let downloadTotal = 0;
  let downloadReceived = 0;
  let updateReady = false;

  $: downloadPercent = downloadTotal > 0
    ? Math.min(100, Math.round(downloadReceived / downloadTotal * 100))
    : null;

  function pushLog(line) {
    logs = [...logs.slice(-199), line];
    setTimeout(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; }, 0);
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "ready":
        trackerStatus = "Tracker connected";
        invoke("send_to_tracker", { message: JSON.stringify({ type: "list_devices" }) });
        break;
      case "devices_list":
        devices = msg.devices ?? [];
        configuredDevice = msg.configured ?? "";
        activeDevice = msg.active ?? "";
        break;
      case "screen_change":
        pushLog(`[screen] ${msg.from} → ${msg.to}`);
        break;
      case "selection_update":
        pushLog(`[selection] ${msg.character ?? "-"} / ${msg.kart ?? "-"} / ${msg.course ?? "-"} / ${msg.costume ?? "-"}`);
        break;
      case "lap_update":
        pushLog(`[lap] ${msg.current ?? "-"} / ${msg.total ?? "-"}${msg.split ? `  split: ${msg.split}` : ""}`);
        break;
      case "coin_update":
        pushLog(`[coins] ${msg.coins}`);
        break;
      case "mush_update":
        pushLog(`[mushrooms] ${msg.count}`);
        break;
      case "finish":
        pushLog(`[finish] ${msg.result ?? "-"}  time: ${msg.total_time ?? "-"}`);
        break;
      case "error":
        pushLog(`[error] ${msg.message}`);
        break;
      default:
        pushLog(JSON.stringify(msg));
    }
  }

  async function handleDeviceChange(e) {
    const value = e.target.value;
    await invoke("send_to_tracker", {
      message: JSON.stringify({ type: "update_config", key: "camera_device", value })
    });
    configuredDevice = value;
    restartNeeded = true;
  }

  async function restartTracker() {
    restartNeeded = false;
    devices = [];
    trackerStatus = "Restarting tracker...";
    await invoke("restart_tracker");
  }

  async function applyUpdate() {
    if (pendingUpdate) await pendingUpdate.install();
  }

  async function checkForUpdate() {
    try {
      const update = await check();
      if (!update) return;
      pendingUpdate = update;
      updateVersion = update.version;
      await update.download((event) => {
        if (event.event === "Started") {
          downloadTotal = event.data.contentLength ?? 0;
          downloadReceived = 0;
        } else if (event.event === "Progress") {
          downloadReceived += event.data.chunkLength;
        } else if (event.event === "Finished") {
          updateReady = true;
        }
      });
    } catch {
      // silently ignore — update check is best-effort
    }
  }

  onMount(async () => {
    version = await getVersion();

    await invoke("start_tracker");

    unlisten = await listen("tracker-event", (event) => {
      try {
        handleMessage(JSON.parse(event.payload));
      } catch {
        pushLog(event.payload);
      }
    });

    // Update check runs fully in background — never blocks tracker startup
    checkForUpdate();
  });

  onDestroy(() => {
    if (unlisten) unlisten();
  });
</script>

<main>
  <h1>MKW Tracker {#if version}<span id="version">v{version}</span>{/if}</h1>
  <p id="tracker">{trackerStatus}</p>

  {#if devices.length > 0}
    <div id="device-row">
      <label for="device-select">Input</label>
      <select id="device-select" on:change={handleDeviceChange}>
        {#if !configuredDevice}
          <option value="" disabled selected>— pick a device —</option>
        {/if}
        {#each devices as d}
          <option value={d} selected={d === configuredDevice}>{d}</option>
        {/each}
      </select>
      {#if restartNeeded}
        <button class="action-btn" on:click={restartTracker}>Restart</button>
      {/if}
    </div>
  {/if}

  {#if updateVersion}
    <div id="update-strip">
      <span id="update-label">
        {#if updateReady}
          v{updateVersion} ready
        {:else}
          v{updateVersion} {downloadPercent !== null ? `${downloadPercent}%` : "…"}
        {/if}
      </span>
      {#if !updateReady}
        <div id="update-bar-track">
          <div id="update-bar-fill" style="width: {downloadPercent ?? 0}%"></div>
        </div>
      {:else}
        <button class="action-btn" on:click={applyUpdate}>Restart to apply</button>
      {/if}
    </div>
  {/if}

  <div id="log" bind:this={logEl}>
    {#each logs as line}
      <div class="line">{line}</div>
    {/each}
  </div>
</main>

<style>
  :global(body) {
    margin: 0;
    background: #0d0d1a;
    color: #e8e8f0;
    font-family: monospace;
  }
  main {
    padding: 2rem;
  }
  h1 {
    font-size: 1.2rem;
    margin: 0 0 0.5rem;
    color: #7eb8f7;
  }
  #version {
    font-size: 0.8rem;
    color: #888;
    margin-left: 0.4rem;
  }
  p {
    margin: 0.25rem 0;
    font-size: 0.9rem;
  }
  #device-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.6rem 0 0.4rem;
    font-size: 0.8rem;
  }
  #device-row label {
    color: #888;
    flex-shrink: 0;
  }
  #device-select {
    flex: 1;
    background: #0a0a14;
    color: #e8e8f0;
    border: 1px solid #2a2a3a;
    border-radius: 3px;
    padding: 0.2rem 0.3rem;
    font-family: monospace;
    font-size: 0.75rem;
    min-width: 0;
  }
  .action-btn {
    background: #1a1a2e;
    color: #7eb8f7;
    border: 1px solid #3a3a5a;
    border-radius: 3px;
    padding: 0.2rem 0.5rem;
    font-family: monospace;
    font-size: 0.75rem;
    cursor: pointer;
    flex-shrink: 0;
  }
  .action-btn:hover {
    background: #2a2a4a;
  }
  #update-strip {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.3rem 0 0.4rem;
    font-size: 0.75rem;
  }
  #update-label {
    color: #4caf50;
    flex-shrink: 0;
  }
  #update-bar-track {
    flex: 1;
    height: 3px;
    background: #1a1a2e;
    border-radius: 2px;
    overflow: hidden;
  }
  #update-bar-fill {
    height: 100%;
    background: #4caf50;
    border-radius: 2px;
    transition: width 0.2s ease;
  }
  #log {
    margin-top: 0.75rem;
    height: 160px;
    overflow-y: auto;
    background: #0a0a14;
    border: 1px solid #2a2a3a;
    border-radius: 4px;
    padding: 0.4rem 0.5rem;
    scrollbar-width: thin;
    scrollbar-color: #2a2a3a #0a0a14;
  }
  #log::-webkit-scrollbar {
    width: 6px;
  }
  #log::-webkit-scrollbar-track {
    background: #0a0a14;
  }
  #log::-webkit-scrollbar-thumb {
    background: #2a2a3a;
    border-radius: 3px;
  }
  #log::-webkit-scrollbar-thumb:hover {
    background: #3a3a55;
  }
  .line {
    font-size: 0.72rem;
    color: #8ab4d0;
    white-space: pre-wrap;
    word-break: break-all;
    line-height: 1.4;
  }
</style>
