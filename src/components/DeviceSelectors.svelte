<script>
  // DeviceSelectors.svelte - Video and Audio device <select>s.
  // All state is owned by App.svelte; this component reads it and dispatches
  // change events back using the same handler functions.

  export let browserDevices = [];
  export let selectedBrowserDeviceId = "";
  export let audioDevices = [];
  export let selectedAudioDeviceId = "";
  export let pythonCameraStatus = "idle";
  export let cameraStatus = "idle";
  export let restartNeeded = false;

  // Callbacks - pass the existing App.svelte handlers directly.
  export let onCameraDeviceChange = (e) => {};   // handleCameraDeviceChange
  export let onAudioDeviceChange  = (e) => {};   // handleAudioDeviceChange
  export let onRestartTracker     = ()  => {};   // restartTracker
</script>

{#if browserDevices.length > 0}
  <div class="device-row">
    <label for="ds-cam">Video</label>
    {#if pythonCameraStatus === "opening" || cameraStatus === "requesting"}
      <div class="select-loading">
        <span class="spin">◌</span>
        <span>{browserDevices.find(d => d.deviceId === selectedBrowserDeviceId)?.label || "Opening…"}</span>
      </div>
    {:else}
      <select id="ds-cam" on:change={onCameraDeviceChange}>
        {#each browserDevices as d}
          <option value={d.deviceId} selected={d.deviceId === selectedBrowserDeviceId}>
            {d.label || `Video ${d.deviceId.slice(0, 6)}…`}
          </option>
        {/each}
      </select>
    {/if}
    {#if restartNeeded}
      <button class="btn-sm" on:click={onRestartTracker}>Restart</button>
    {/if}
  </div>
{/if}

{#if audioDevices.length > 0}
  <div class="device-row">
    <label for="ds-aud">Audio</label>
    <select id="ds-aud" on:change={onAudioDeviceChange}>
      <option value="none" selected={!selectedAudioDeviceId || selectedAudioDeviceId === "none"}>(none)</option>
      {#each audioDevices as d}
        <option value={d.deviceId} selected={d.deviceId === selectedAudioDeviceId}>
          {d.label || `Audio ${d.deviceId.slice(0, 6)}…`}
        </option>
      {/each}
    </select>
  </div>
{/if}

<style>
  .device-row { display: flex; align-items: center; gap: .4rem; font-size: .72rem; flex-shrink: 0; }
  /* Fixed label width so the Video/Audio dropdowns share the same left edge... */
  .device-row label { color: var(--tx-dim); flex-shrink: 0; min-width: 3rem; }
  /* ...and a fixed select width so both dropdowns are exactly the same size. */
  .device-row select { width: 14rem; flex: none; }
  .select-loading { width: 14rem; display: flex; align-items: center; gap: .3rem; color: var(--tx-mut); font-size: .72rem; font-style: italic; overflow: hidden; }
  .spin { animation: spin 1.2s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .btn-sm {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .16rem .45rem; font-family: inherit; font-size: .68rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-sm:hover { background: var(--raised); color: var(--tx); }
</style>
