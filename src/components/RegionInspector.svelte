<script>
  import { createEventDispatcher } from "svelte";
  import { scoreColor, fmtScore } from "../lib/format.js";

  /** Data-URL of the live crop from the capture feed (Canny edges when isCostume). */
  export let liveCrop = null;
  /** Data-URL of the stored template image, or null if not captured yet. */
  export let template = null;
  /** Match score object: { score, threshold, matched } | null. Updated live (~3 Hz). */
  export let score = null;
  /** True for costume regions - the live crop is a Canny-edge image. */
  export let isCostume = false;
  /** True while a template capture is in flight (disables Recapture). */
  export let capturing = false;

  const dispatch = createEventDispatcher();

  $: pct    = score ? Math.min(Math.max(score.score, 0), 1) * 100 : 0;
  $: thrPct = score ? Math.min(Math.max(score.threshold, 0), 1) * 100 : 0;
</script>

<div class="inspector">
  <!-- Live match readout - the score updates continuously against the feed. -->
  <div class="insp-head">
    <span class="insp-title">Live match</span>
    {#if score}
      <span class="insp-dot" class:ok={score.matched}></span>
      <span class="insp-score" style="color:{scoreColor(score.score)}">{fmtScore(score.score)}</span>
    {:else}
      <span class="insp-score insp-score-empty">-</span>
    {/if}
  </div>

  <!-- Live crop vs stored template -->
  <div class="thumbs">
    <figure class="thumb">
      {#if liveCrop}<img src={liveCrop} alt="live crop" />{:else}<div class="thumb-empty"></div>{/if}
      <figcaption>{isCostume ? "live · edges" : "live"}</figcaption>
    </figure>
    <figure class="thumb">
      {#if template}<img src={template} alt="stored template" />{:else}<div class="thumb-empty"></div>{/if}
      <figcaption>template</figcaption>
    </figure>
  </div>

  <!-- Score bar with a tick marking the pass threshold -->
  <div class="bar">
    <div class="bar-fill" style="width:{pct}%; background:{score ? scoreColor(score.score) : 'var(--track)'}"></div>
    {#if score}
      <div class="bar-thresh" style="left:{thrPct}%" title="threshold {fmtScore(score.threshold)}"></div>
    {/if}
  </div>

  <button
    type="button"
    class="insp-capture"
    disabled={capturing}
    on:click={() => dispatch("capture")}
  >{capturing ? "Capturing…" : "Recapture template"}</button>
</div>

<style>
  .inspector { display: flex; flex-direction: column; gap: 9px; }

  /* Header: title + match dot + live score */
  .insp-head { display: flex; align-items: center; gap: 7px; }
  .insp-title {
    font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em;
    color: var(--tx-mut); margin-right: auto;
  }
  .insp-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--idle); flex: none; }
  .insp-dot.ok { background: var(--ok); }
  .insp-score { font-size: 13px; font-variant-numeric: tabular-nums; line-height: 1; }
  .insp-score-empty { color: var(--tx-dim); }

  /* Thumbnails */
  .thumbs { display: flex; gap: 8px; }
  .thumb { flex: 1; margin: 0; }
  .thumb img, .thumb-empty {
    display: block; width: 100%; height: 54px; object-fit: contain;
    background: var(--feed-bg); border: 1px solid var(--bd); border-radius: var(--r-sm);
    image-rendering: pixelated;
  }
  .thumb figcaption {
    margin-top: 3px; font-size: 10px; color: var(--tx-dim); text-align: center;
    text-transform: lowercase; letter-spacing: .02em;
  }

  /* Score bar + threshold tick */
  .bar { position: relative; height: 4px; background: var(--track); border-radius: var(--r-sm); overflow: hidden; }
  .bar-fill { height: 100%; transition: width .12s, background .12s; }
  .bar-thresh {
    position: absolute; top: -1px; width: 2px; height: 6px;
    background: var(--tx-mut); transform: translateX(-1px);
  }

  /* Single action */
  .insp-capture {
    width: 100%; padding: 6px 0; font-size: 12px; font-family: inherit;
    color: var(--tx-mut); background: var(--panel-2);
    border: 1px solid var(--bd); border-radius: var(--r); cursor: pointer;
    transition: background .12s, color .12s, border-color .12s;
  }
  .insp-capture:hover:not(:disabled) { background: var(--raised); color: var(--tx); border-color: var(--accent-soft); }
  .insp-capture:disabled { opacity: .45; cursor: default; }
</style>
