<script>
  import { onDestroy } from "svelte";
  import { presence, serverConnection } from "../lib/stores.js";
  import { serverUrl } from "../lib/syncSettings.js";
  import { connectionChip, emptyState } from "../lib/playerPanel.js";
  import { C } from "../lib/palette.js";
  import PlayerCard from "./PlayerCard.svelte";

  // presence is { [player_id]: entry }; render in stable ascending player_id order.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: connected = $serverConnection.connected;
  // No live link → every card renders stale/offline, so nobody is "racing" (also drives the clock).
  $: anyRacing = connected && players.some((p) => p.online && p.screen === "RACING" && !p.final_time);
  $: configured = !!($serverUrl || "").trim();

  // One clock for all cards + the "last sync" label: ~30 fps while someone races (so the ms timer
  // ticks), else a cheap 1 s tick (offline "last seen" / "last sync"). Avoids a per-card loop.
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

  $: chip = connectionChip($serverConnection, now);
  $: chipColor = chip.tier === "live" ? C.ok : chip.tier === "offline" ? C.warn : C.idle;
  $: empty = emptyState(configured);
</script>

<div class="wrap">
  <div class="head">
    <span class="title">PLAYERS</span>
    <span class="chip"><span class="dot" style="background:{chipColor}"></span>{chip.label}</span>
  </div>
  {#if players.length}
    <div class="panel" style="--n:{players.length}">
      {#each players as p (p.player_id)}<PlayerCard entry={p} {now} stale={!connected} />{/each}
    </div>
  {:else}
    <div class="empty">
      <div class="empty-title">{empty.title}</div>
      <div class="empty-hint">{empty.hint}</div>
    </div>
  {/if}
</div>

<style>
  .wrap { display: flex; flex-direction: column; height: 100%; }
  .head { flex: none; display: flex; align-items: center; justify-content: space-between;
          height: 20px; padding: 0 9px; background: var(--panel); border-bottom: 1px solid var(--bd); }
  .title { font-size: .62rem; letter-spacing: .14em; text-transform: uppercase; color: var(--tx-dim); }
  .chip { display: inline-flex; align-items: center; gap: 6px; font-size: .62rem; letter-spacing: .04em;
          color: var(--tx-mut); font-variant-numeric: tabular-nums; }
  .chip .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .panel { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(var(--n, 5), 1fr);
           gap: 1px; background: var(--bd); }
  .empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
           gap: 4px; text-align: center; }
  .empty-title { font-size: .8rem; color: var(--tx-mut); }
  .empty-hint { font-size: .68rem; color: var(--tx-dim); letter-spacing: .02em; }
</style>
