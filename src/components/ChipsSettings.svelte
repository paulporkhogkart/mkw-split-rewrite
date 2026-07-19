<script>
  // Chips tab: on-demand cache stats + opt-in full-pack download (spec 2026-07-19).
  import { onMount, onDestroy } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { fmtBytes, packLabel, progressFrac } from "../lib/chipsSettings.js";

  let status = null, progress = null, unlisten = null, err = "";

  async function refresh() {
    try { status = await invoke("chips_get_status"); } catch (e) { err = String(e); }
  }
  onMount(async () => {
    refresh();
    unlisten = await listen("chips-progress", (ev) => {
      progress = ev.payload;
      if (progress.state === "error") err = progress.error || "download failed";
      if (progress.state === "done" || progress.state === "error") refresh();
    });
  });
  onDestroy(() => unlisten && unlisten());

  const start  = () => { err = ""; invoke("chips_start_pack").then(refresh); };
  const pause  = () => invoke("chips_pause_pack").then(refresh);
  const cancel = () => invoke("chips_cancel_pack").then(() => { progress = null; refresh(); });
  const nuke   = () => invoke("chips_delete_cache").then(() => { progress = null; refresh(); });

  $: downloading = status?.packWanted && !status?.packComplete && !status?.packPaused;
  $: frac = progressFrac(progress);
</script>

<div class="step-centred">
  <h2>Chips</h2>
  <p>Animated character/kart chips on the player cards. By default they download on demand
     and stay cached, so anything seen once is instant. The full pack makes every chip
     instant, even offline.</p>

  <div class="discord-section">
    <h3 class="discord-heading">Cache</h3>
    <div class="kvrow"><span>Cached</span>
      <span>{status ? `${status.cachedFiles} files · ${fmtBytes(status.cachedBytes)}` : "…"}</span></div>
    <div class="kvrow"><span>Pack version</span><span>{status?.currentTag ?? "not fetched yet"}</span></div>
    <button class="btn-sm" on:click={nuke}>Delete chip cache</button>
    <p class="discord-note">Also covered by app-data deletion. Chips re-download on demand.</p>
  </div>

  <div class="discord-section">
    <h3 class="discord-heading">Full pack</h3>
    <div class="kvrow"><span>{status ? packLabel(status, progress) : "…"}</span></div>
    {#if frac != null && downloading}
      <div class="bar"><div class="fill" style="width:{frac * 100}%"></div></div>
      <div class="discord-note">{progress.shard} · {fmtBytes(progress.shard_bytes)}</div>
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
    <div class="btns">
      {#if downloading}
        <button class="btn-sm" on:click={pause}>Pause</button>
        <button class="btn-sm" on:click={cancel}>Cancel</button>
      {:else if status?.packPaused}
        <button class="btn-primary" on:click={start}>Resume</button>
        <button class="btn-sm" on:click={cancel}>Cancel</button>
      {:else}
        <button class="btn-primary" on:click={start}>
          {status?.updateAvailable ? "Update pack" : "Download full pack (6.3 GB)"}</button>
      {/if}
    </div>
    <p class="discord-note">Resumes where it left off after pause or app restart.
       Nothing re-downloads.</p>
  </div>
</div>

<style>
  .kvrow { display: flex; justify-content: space-between; font-size: .72rem; color: var(--tx); }
  .bar { height: 6px; background: var(--panel); border: 1px solid var(--bd); border-radius: 3px; overflow: hidden; }
  .fill { height: 100%; background: var(--accent); transition: width .3s; }
  .btns { display: flex; gap: .5rem; margin-top: .2rem; }
  .err { font-size: .68rem; color: var(--bad, #e5484d); }
  /* .step-centred/.discord-* come from the parent modal's scope — duplicate the handful
     used here into this component's scope (Svelte styles don't cascade): */
  .step-centred { max-width: 560px; margin: 0 auto; padding: .5rem 0; display: flex; flex-direction: column; gap: .75rem; }
  .step-centred h2 { color: var(--tx); font-size: .95rem; font-weight: 600; }
  .step-centred p { font-size: .76rem; color: var(--tx-mut); line-height: 1.6; }
  .discord-section { display: flex; flex-direction: column; gap: .35rem; padding: .55rem .7rem;
    border-radius: var(--r); background: var(--panel-2); border: 1px solid var(--bd); }
  .discord-heading { font-size: .63rem; color: var(--tx-mut); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; margin: 0; }
  .discord-note { font-size: .66rem; color: var(--tx-dim); margin: .1rem 0 0; line-height: 1.5; }
  .btn-primary { background: var(--accent-bg); color: var(--tx); border: 1px solid var(--accent); border-radius: var(--r);
    padding: .28rem .7rem; font-family: inherit; font-size: .72rem; cursor: pointer; }
  .btn-sm { background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .16rem .45rem; font-family: inherit; font-size: .68rem; cursor: pointer; }
</style>
