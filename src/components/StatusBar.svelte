<script>
  import { C } from "../lib/palette.js";
  import { scoreColor, screenLabel, fmtScore } from "../lib/format.js";

  /** Whether the IPC connection to the backend is established (trackerConnected). */
  export let connected = false;
  /**
   * Whether the backend is actively sending heartbeats (backendAlive).
   * Only meaningful when connected=true; when connected=true but alive=false the
   * process is connected but has stalled.
   */
  export let alive = false;
  /** Whether a backend process has been spawned at all (trackerSpawned). */
  export let spawned = false;
  /** Current backend screen enum name (e.g. "RACING") or "-" placeholder */
  export let screenName = "-";
  /** Latest live confidence score (0–1) */
  export let score = 0.0;
  /** Latest reported frames-per-second */
  export let fps = 0;
  /** Capture resolution width */
  export let frameW = 1920;
  /** Capture resolution height */
  export let frameH = 1080;
  /** Whether the season server (presence WS) is currently connected. */
  export let serverConnected = false;
  /** Epoch-ms of the last live server frame, or null if never. */
  export let serverSyncedAt = null;

  // Dot colour mirrors App.svelte's original statusDot reactive:
  //   connected && alive  → ok   (green)
  //   connected, no alive → warn (amber)
  //   not connected       → idle (grey)
  $: dotColor = !connected ? C.idle : alive ? C.ok : C.warn;
  // Independent of the engine dot: green = live link, amber = offline but we have a cached
  // snapshot, grey = never connected.
  $: srvColor = serverConnected ? C.ok : serverSyncedAt != null ? C.warn : C.idle;
  $: srvLabel = serverConnected ? "server" : serverSyncedAt != null ? "server offline" : "no server";
</script>

<footer class="statusbar">
  <span class="hb-dot" style="background:{dotColor}"></span>
  {#if connected && alive}
    <span class="sb-screen">{screenLabel(screenName)}</span>
    <span class="sb-sep">|</span>
    <span class="sb-score" style="color:{scoreColor(score)}">{fmtScore(score)}</span>
    <span class="sb-sep">|</span>
    <span class="sb-fps">{fps} fps</span>
    <span class="sb-spacer"></span>
    <span class="sb-res">{frameW}×{frameH}</span>
  {:else if connected}
    <span class="sb-warn">backend stalled</span>
    <span class="sb-spacer"></span>
  {:else if spawned}
    <span class="sb-idle">engine starting…</span>
    <span class="sb-spacer"></span>
  {:else}
    <span class="sb-idle">launching…</span>
    <span class="sb-spacer"></span>
  {/if}
  <span class="sb-sep">|</span>
  <span class="sb-srv"><span class="srv-dot" style="background:{srvColor}"></span>{srvLabel}</span>
</footer>

<style>
  .statusbar {
    flex: none; display: flex; align-items: center; gap: 8px;
    height: 24px; padding: 0 12px;
    background: var(--panel); border-top: 1px solid var(--bd);
    font-family: var(--ui); font-size: .68rem; color: var(--tx-mut);
    font-variant-numeric: tabular-nums; font-feature-settings: "tnum";
  }
  .sb-screen { color: var(--tx); }
  .sb-sep    { color: var(--tx-dim); }
  .sb-fps,
  .sb-res    { color: var(--tx-mut); }
  .sb-warn   { color: var(--warn); }
  .sb-idle   { color: var(--tx-dim); font-style: italic; }
  .sb-spacer { flex: 1; }
  .sb-srv    { display: inline-flex; align-items: center; gap: 5px; color: var(--tx-mut); }
  .srv-dot   { width: 7px; height: 7px; border-radius: 50%; flex: none; }
</style>
