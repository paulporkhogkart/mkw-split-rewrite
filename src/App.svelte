<script>
  import { onMount } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
  import { getVersion } from "@tauri-apps/api/app";
  import { invoke } from "@tauri-apps/api/core";

  let trackerStatus = "Connecting to tracker...";
  let updateStatus = "";
  let version = "";
  let logs = [];
  let logEl;

  function pushLog(line) {
    logs = [...logs.slice(-199), line];
    setTimeout(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; }, 0);
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "ready":
        trackerStatus = "Tracker connected";
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

  onMount(async () => {
    version = await getVersion();

    try {
      const update = await check();
      if (update) {
        updateStatus = `Updating to v${update.version}…`;
        await update.downloadAndInstall();
        return;
      }
    } catch (e) {
      console.warn("Update check failed:", e);
    }

    await invoke("start_tracker");

    await listen("tracker-event", (event) => {
      try {
        handleMessage(JSON.parse(event.payload));
      } catch {
        pushLog(event.payload);
      }
    });
  });
</script>

<main>
  <h1>MKW Tracker {#if version}<span id="version">v{version}</span>{/if}</h1>
  <p id="tracker">{trackerStatus}</p>
  {#if updateStatus}
    <p id="update">{updateStatus}</p>
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
  #update {
    color: #4caf50;
    margin-top: 0.5rem;
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
