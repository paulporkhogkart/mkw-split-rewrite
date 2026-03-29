<script>
  import { onMount } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
  import { getVersion } from "@tauri-apps/api/app";
  import { invoke } from "@tauri-apps/api/core";

  let trackerStatus = "Connecting to tracker...";
  let updateStatus = "";
  let version = "";

  onMount(async () => {
    version = await getVersion();

    // Check for update before starting the tracker. If an update is found,
    // install it and let NSIS relaunch — Python is never spawned so there are
    // no file locks to worry about.
    try {
      const update = await check();
      if (update) {
        updateStatus = `Updating to v${update.version}…`;
        await update.downloadAndInstall();
        // NSIS handles relaunch — do not call relaunch() here.
        return;
      }
    } catch (e) {
      // Non-fatal — no network, no release, etc.
      console.warn("Update check failed:", e);
    }

    // No update: start the Python tracker and listen for its events.
    await invoke("start_tracker");

    await listen("tracker-event", (event) => {
      console.log("[tracker]", event.payload);
      try {
        const msg = JSON.parse(event.payload);
        if (msg.type === "ready") trackerStatus = "Tracker connected";
      } catch {}
    });
  });
</script>

<main>
  <h1>MKW Tracker {#if version}<span id="version">v{version}</span>{/if}</h1>
  <p id="tracker">{trackerStatus}</p>
  {#if updateStatus}
    <p id="update">{updateStatus}</p>
  {/if}
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
</style>
