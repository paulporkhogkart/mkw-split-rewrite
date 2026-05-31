<script>
  import { C } from "../lib/palette.js";
  import { scoreColor, screenLabel, fmtScore } from "../lib/format.js";

  /**
   * Whether the backend is fully alive (trackerConnected && backendAlive).
   * When true, the full screen/score/fps/resolution readout is shown.
   */
  export let connected = false;
  /**
   * Whether a backend process is running at all (trackerSpawned || trackerConnected).
   * When connected=false and spawned=true the bar shows a degraded-state label.
   * When both false the bar shows "launching…".
   */
  export let spawned = false;
  /** Current backend screen enum name (e.g. "RACING") or "—" placeholder */
  export let screenName = "—";
  /** Latest live confidence score (0–1) */
  export let score = 0.0;
  /** Latest reported frames-per-second */
  export let fps = 0;
  /** Capture resolution width */
  export let frameW = 1920;
  /** Capture resolution height */
  export let frameH = 1080;

  // Three-state dot colour, mirroring App.svelte's original statusDot logic:
  //   connected (alive)        → ok   (green)
  //   spawned but not alive    → warn (amber)
  //   nothing running          → idle (grey)
  $: dotColor = connected ? C.ok : spawned ? C.warn : C.idle;
</script>

<footer class="statusbar">
  <span class="hb-dot" style="background:{dotColor}"></span>
  {#if connected}
    <span class="sb-screen">{screenLabel(screenName)}</span>
    <span class="sb-sep">|</span>
    <span class="sb-score" style="color:{scoreColor(score)}">{fmtScore(score)}</span>
    <span class="sb-sep">|</span>
    <span class="sb-fps">{fps} fps</span>
    <span class="sb-spacer"></span>
    <span class="sb-res">{frameW}×{frameH}</span>
  {:else if spawned}
    <span class="sb-warn">backend stalled</span>
    <span class="sb-spacer"></span>
  {:else}
    <span class="sb-idle">launching…</span>
    <span class="sb-spacer"></span>
  {/if}
</footer>

<style>
  .statusbar {
    flex: none; display: flex; align-items: center; gap: 8px;
    height: 24px; padding: 0 12px;
    background: var(--panel); border-top: 1px solid var(--bd);
    font-family: var(--ui); font-size: .68rem; color: var(--tx-mut);
    font-variant-numeric: tabular-nums; font-feature-settings: "tnum";
  }
  .hb-dot   { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; transition: background .6s; }
  .sb-screen { color: var(--tx); }
  .sb-sep    { color: var(--tx-dim); }
  .sb-fps,
  .sb-res    { color: var(--tx-mut); }
  .sb-warn   { color: var(--warn); }
  .sb-idle   { color: var(--tx-dim); font-style: italic; }
  .sb-spacer { flex: 1; }
</style>
