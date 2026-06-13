<script>
  import { onDestroy } from "svelte";
  import { presence, serverConnection } from "../lib/stores.js";
  import { serverUrl } from "../lib/syncSettings.js";
  import { emptyState } from "../lib/playerPanel.js";
  import PlayerCard from "./PlayerCard.svelte";

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
  onDestroy(() => { clearInterval(fast); clearInterval(slow); });

  $: empty = emptyState(configured);
</script>

{#if players.length}
  <div class="panel" style="--n:{players.length}">
    {#each players as p (p.player_id)}<PlayerCard entry={p} {now} stale={!connected && !p._localSelf} />{/each}
  </div>
{:else}
  <div class="empty">
    <div class="empty-title">{empty.title}</div>
    <div class="empty-hint">{empty.hint}</div>
  </div>
{/if}

<style>
  .panel { display: grid; grid-template-columns: repeat(var(--n, 5), 1fr); gap: 1px;
           background: var(--bd); height: 100%; }
  .empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
           gap: 4px; text-align: center; }
  .empty-title { font-size: .8rem; color: var(--tx-mut); }
  .empty-hint { font-size: .68rem; color: var(--tx-dim); letter-spacing: .02em; }
</style>
