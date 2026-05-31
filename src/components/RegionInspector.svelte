<script>
  import { createEventDispatcher } from "svelte";
  import { scoreColor, fmtScore } from "../lib/format.js";

  /**
   * Data-URL of the live crop from the capture feed, or null if not available.
   * When isCostume is true this is the Canny-edge image.
   */
  export let liveCrop = null;

  /**
   * Data-URL of the stored template image, or null if not captured yet.
   */
  export let template = null;

  /**
   * Match score object: { score: number, threshold: number, matched: boolean } | null
   */
  export let score = null;

  /**
   * When true the region is a costume region — live crop is Canny edges.
   * The label changes from "Live" to "Live (edges)".
   */
  export let isCostume = false;

  /**
   * Whether a template capture is in progress (disables Capture button).
   */
  export let capturing = false;

  const dispatch = createEventDispatcher();
</script>

<div class="reg-inspector">
  <!-- Thumbnails: live crop vs stored template -->
  <div class="reg-thumbs">
    <div class="reg-thumb">
      <span class="thumb-cap">{isCostume ? "Live (edges)" : "Live"}</span>
      {#if liveCrop}
        <img src={liveCrop} alt={isCostume ? "live edges" : "live crop"} />
      {:else}
        <div class="reg-thumb-empty"></div>
      {/if}
    </div>
    <div class="reg-thumb">
      <span class="thumb-cap">Template</span>
      {#if template}
        <img src={template} alt="stored template" />
      {:else}
        <div class="reg-thumb-empty"></div>
      {/if}
    </div>
  </div>

  <!-- Match score row -->
  <div class="match-row">
    <span class="match-label">Match</span>
    {#if score != null}
      <div class="match-bar-track">
        <div
          class="match-bar-fill"
          style="width:{Math.min(score.score, 1) * 100}%; background:{scoreColor(score.score)}"
        ></div>
      </div>
      <span class="match-score" style="color:{scoreColor(score.score)}">{fmtScore(score.score)}</span>
    {:else}
      <div class="match-bar-track"><div class="match-bar-fill" style="width:0%"></div></div>
      <span class="match-score match-score-empty">—</span>
    {/if}
  </div>

  <!-- Action buttons -->
  <div class="inspector-brow">
    <button
      type="button"
      class="b"
      disabled={capturing}
      on:click={() => dispatch("capture")}
    >{capturing ? "Capturing…" : "Capture"}</button>
    <button
      type="button"
      class="b b-pri"
      on:click={() => dispatch("test")}
    >Test</button>
  </div>
</div>

<style>
  .reg-inspector { display: flex; flex-direction: column; gap: 8px; }

  /* Thumbnails */
  .reg-thumbs { display: flex; gap: 8px; }
  .reg-thumb { flex: 1; font-size: .58rem; color: var(--tx-mut); text-align: center; }
  .thumb-cap { display: block; margin-bottom: 3px; }
  .reg-thumb img {
    display: block; width: 100%; height: 56px; object-fit: contain;
    background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r);
    image-rendering: pixelated;
  }
  .reg-thumb-empty {
    display: block; width: 100%; height: 56px;
    background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r);
  }

  /* Match score row */
  .match-row { display: flex; align-items: center; gap: 9px; font-size: .78rem; }
  .match-label { color: var(--tx-mut); flex: none; }
  .match-bar-track { flex: 1; height: 3px; background: var(--track); overflow: hidden; border-radius: var(--r-sm); }
  .match-bar-fill { height: 100%; border-radius: var(--r-sm); transition: width .15s, background .15s; }
  .match-score { font-variant-numeric: tabular-nums; font-size: .76rem; flex: none; min-width: 2.4ch; text-align: right; }
  .match-score-empty { color: var(--tx-dim); }

  /* Action buttons */
  .inspector-brow { display: flex; gap: 7px; }
  .b {
    flex: 1; text-align: center; border: 1px solid var(--bd); border-radius: var(--r);
    padding: 5px 0; font-size: .78rem; color: var(--tx); background: transparent;
    font-family: inherit; cursor: pointer;
  }
  .b:hover:not(:disabled) { background: var(--raised); }
  .b:disabled { opacity: .4; cursor: default; }
  .b-pri { border-color: var(--accent); color: #9cc4ec; }
  .b-pri:hover:not(:disabled) { background: var(--accent-bg); }
</style>
