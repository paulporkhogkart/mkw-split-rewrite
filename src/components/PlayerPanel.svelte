<script>
  import { onDestroy } from "svelte";
  import { presence, serverConnection } from "../lib/stores.js";
  import { serverUrl } from "../lib/syncSettings.js";
  import { emptyState } from "../lib/playerPanel.js";
  import LiveCard from "./LiveCard.svelte";
  import { loadManifest, createBitmapCache } from "../lib/chipStream.js";

  // Shared chip plumbing for the whole panel: ONE manifest load + ONE bitmap cache, handed
  // down to every LiveCard. loadManifest resolves null when the pack was never fetched /
  // is unreachable (offline chips.localhost) — cards render chipless by design, no error UI.
  const CHIPS_BASE = "http://chips.localhost/";
  let manifest = null;
  loadManifest(CHIPS_BASE).then((m) => (manifest = m));
  const bitmapCache = createBitmapCache(12);

  // presence is { [player_id]: entry }; render in stable ascending player_id order.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: connected = $serverConnection.connected;
  // No live link → cards render stale/offline, EXCEPT our own, which the local-self echo keeps
  // live from the engine (tagged _localSelf). Our own live race also keeps the clock fast.
  $: anyRacing = players.some((p) => p.online && p.screen === "RACING" && !p.final_time && (connected || p._localSelf));
  $: configured = !!($serverUrl || "").trim();

  // One clock for all cards: ~30 fps while someone races (so the ms timer ticks), else a cheap
  // 1 s tick. Avoids a per-card animation loop.
  let now = Date.now();
  let fast = 0, slow = 0, clockRacing = null;
  function setClock(racing) {
    if (racing === clockRacing) return;        // only re-arm on an actual mode change
    clockRacing = racing;
    clearInterval(fast); clearInterval(slow); fast = 0; slow = 0;
    now = Date.now();
    if (racing) fast = setInterval(() => (now = Date.now()), 33);
    else slow = setInterval(() => (now = Date.now()), 1000);
  }
  $: setClock(anyRacing);
  onDestroy(() => {
    clearInterval(fast); clearInterval(slow);
    bitmapCache.dispose(); // view toggles destroy/recreate this panel; deterministic GPU-bitmap release beats GC
  });

  $: empty = emptyState(configured);

  // Card scale: LiveCard is a fixed 250x150 design-space card that scales itself via the
  // CSS var --s (transform: scale, origin top-left) — a transform never changes layout size,
  // so the per-card "cell" wrapper below must carry the POST-scale footprint explicitly
  // (width/height in px) for the centered flex row to lay out at the right size. One
  // measurement (the panel's own clientWidth/clientHeight) sizes every card; --s is
  // whichever axis is tighter so the card never overflows its cell in either dimension.
  let panelW = 0, panelH = 0;
  const GAP = 1; // px — mirrors .panel's `gap: 1px` below
  $: n = players.length;
  $: cellW = n ? (panelW - GAP * (n - 1)) / n : 0;
  $: cardScale = n && panelH ? Math.max(0, Math.min(cellW / 250, panelH / 150)) : 0;
  $: cardW = 250 * cardScale;
  $: cardH = 150 * cardScale;
</script>

{#if players.length}
  <div class="stage">
    <div class="panel" bind:clientWidth={panelW} bind:clientHeight={panelH}>
      {#each players as p (p.player_id)}
        <div class="cell" style="--s:{cardScale};width:{cardW}px;height:{cardH}px">
          <LiveCard entry={p} {now} stale={!connected && !p._localSelf} {manifest} {bitmapCache} />
        </div>
      {/each}
    </div>
  </div>
{:else}
  <div class="empty">
    <div class="empty-title">{empty.title}</div>
    <div class="empty-hint">{empty.hint}</div>
  </div>
{/if}

<style>
  /* .stage carries breathing-room padding so the torn card's +6px bd offset and the fire
     window's poke (right:-32px/bottom:-26px in card-design space, scaled down by --s at
     runtime) have room to paint before hitting the player-band's own overflow:hidden
     (App.svelte, out of scope here) — .panel itself stays flush so the --s measurement
     below reflects exactly the space the grid tracks get. */
  .stage { height: 100%; box-sizing: border-box; padding: 4px 24px 20px 4px; overflow: visible; }
  .panel { display: flex; justify-content: center; align-items: center; gap: 1px;
           height: 100%; overflow: visible; }
  .cell { position: relative; overflow: visible; flex: 0 0 auto; }
  .empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
           gap: 4px; text-align: center; }
  .empty-title { font-size: .8rem; color: var(--tx-mut); }
  .empty-hint { font-size: .68rem; color: var(--tx-dim); letter-spacing: .02em; }
</style>
