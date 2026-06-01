<script>
  // SourceCheck.svelte - dual-feed source check panes.
  // The left pane's <video> element is owned by App.svelte (to preserve the
  // `wizVideoEl` binding used by `$: wizVideoEl.srcObject = videoStream`).
  // App passes `bind:videoEl` so the bind:this wires into App's wizVideoEl.
  import { C } from "../lib/palette.js";

  export let videoEl = null;      // bind:videoEl → wizVideoEl in App.svelte

  // Browser / app feed state
  export let cameraOk = false;
  export let cameraStatus = "idle";          // "idle"|"requesting"|"busy"|"error"|"ok"
  export let trackerCameraPaused = false;

  // Python engine feed state
  export let engineFrame = null;
  export let pythonCameraOk = false;
  export let pythonCameraStatus = "idle";    // "idle"|"opening"|"ok"|"error"
  export let pythonCameraError = "";
  export let trackerConnected = false;
</script>

<div class="cam-dual">
  <!-- Left pane: browser / app feed -->
  <div class="cam-pane">
    <div class="cam-pane-label">Browser / App Input</div>
    <div class="preview-wrapper">
      {#if cameraOk}
        <!-- video element is bound to App.svelte's wizVideoEl via bind:videoEl -->
        <video bind:this={videoEl} autoplay playsinline muted class="preview-video"></video>
      {:else if cameraStatus === "requesting"}
        <div class="preview-placeholder"><span class="spin">◌</span><span>Opening…</span></div>
      {:else if cameraStatus === "busy"}
        <div class="preview-placeholder">
          <span class="preview-icon">⊗</span>
          <span class="cam-pane-err-label">Blocked - device in exclusive use</span>
        </div>
      {:else if cameraStatus === "error"}
        <div class="preview-placeholder">
          <span class="preview-icon">⊗</span><span class="cam-pane-err-label">Camera error</span>
        </div>
      {:else if trackerCameraPaused}
        <div class="preview-placeholder">
          <span class="preview-icon" style="color:{C.txMut}">○</span>
          <span class="cam-pane-err-label">Camera released</span>
        </div>
      {:else}
        <div class="preview-placeholder"><span class="spin">◌</span><span>Waiting…</span></div>
      {/if}
    </div>
    <div class="cam-pane-status"
      class:cam-status-ok={cameraOk}
      class:cam-status-err={cameraStatus === "busy" || cameraStatus === "error"}
      class:cam-status-warn={trackerCameraPaused && !cameraOk}>
      <span class="cam-dot"></span>
      {cameraOk ? "Connected" : cameraStatus === "requesting" ? "Opening…" : cameraStatus === "busy" ? "Blocked" : cameraStatus === "error" ? "Error" : trackerCameraPaused ? "Released" : "Waiting"}
    </div>
  </div>

  <!-- Right pane: Python engine feed -->
  <div class="cam-pane">
    <div class="cam-pane-label">Python Engine Input</div>
    <div class="preview-wrapper">
      {#if engineFrame && !trackerCameraPaused}
        <img src={engineFrame} alt="Engine feed" class="preview-video" style="object-fit:contain"/>
      {:else if trackerCameraPaused}
        <div class="preview-placeholder">
          <span class="preview-icon" style="color:{C.txMut}">○</span>
          <span class="cam-pane-err-label">Camera released</span>
        </div>
      {:else if pythonCameraStatus === "error"}
        <div class="preview-placeholder">
          <span class="preview-icon">⊗</span>
          <span class="cam-pane-err-label">Can't access device{pythonCameraError ? `: ${pythonCameraError}` : ""}</span>
        </div>
      {:else}
        <div class="preview-placeholder">
          <span class="spin">◌</span>
          <span>{pythonCameraStatus === "opening" ? "Opening and verifying…" : !trackerConnected ? "Connecting to engine…" : "Waiting for camera…"}</span>
        </div>
      {/if}
    </div>
    <div class="cam-pane-status"
      class:cam-status-ok={pythonCameraOk}
      class:cam-status-err={pythonCameraStatus === "error"}
      class:cam-status-warn={trackerCameraPaused}>
      <span class="cam-dot"></span>
      {pythonCameraOk ? "Connected" : trackerCameraPaused ? "Released" : pythonCameraStatus === "error" ? "Error" : pythonCameraStatus === "opening" ? "Opening…" : "Waiting"}
    </div>
  </div>
</div>

<style>
  .cam-dual  { display: flex; gap: .75rem; }
  .cam-pane  { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: .3rem; }
  .cam-pane-label { font-size: .63rem; color: var(--tx-mut); text-transform: uppercase; letter-spacing: .06em; }
  .cam-pane-status { display: flex; align-items: center; gap: .3rem; font-size: .65rem; color: var(--tx-mut); }
  .cam-pane-status .cam-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--tx-dim); flex-shrink: 0; }
  .cam-status-ok  { color: var(--ok); } .cam-status-ok .cam-dot  { background: var(--ok); }
  .cam-status-err { color: var(--err); } .cam-status-err .cam-dot { background: var(--err); }
  .cam-status-warn { color: var(--tx-mut); } .cam-status-warn .cam-dot { background: var(--tx-mut); }
  .cam-pane-err-label { font-size: .72rem; color: var(--tx-dim); }

  .preview-wrapper {
    position: relative; width: 100%; aspect-ratio: 16/9;
    background: var(--feed-bg); border: 1px solid var(--bd); border-radius: var(--r); overflow: hidden;
  }
  .preview-video { width: 100%; height: 100%; display: block; object-fit: contain; }
  .preview-placeholder {
    width: 100%; height: 100%; position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: .35rem; font-size: .75rem; color: var(--tx-dim);
    padding: 0 .75rem; box-sizing: border-box; text-align: center;
  }
  .preview-icon { font-size: 1.4rem; line-height: 1; }
  .spin { animation: spin 1.2s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
