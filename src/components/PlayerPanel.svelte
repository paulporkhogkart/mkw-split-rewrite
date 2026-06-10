<script>
  import { onDestroy } from "svelte";
  import { presence } from "../lib/stores.js";
  import PlayerCard from "./PlayerCard.svelte";
  // presence is { [player_id]: entry }; render in stable ascending player_id order.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: anyRacing = players.some((p) => p.online && p.screen === "RACING" && !p.final_time);

  // One clock for all cards: ~30 fps while someone races (so the ms timer ticks),
  // else a cheap 1 s tick (offline "last seen"). Avoids a per-card animation loop.
  let now = Date.now();
  let fast = 0, slow = 0;
  function setClock(racing) {
    clearInterval(fast); clearInterval(slow); fast = 0; slow = 0;
    now = Date.now();
    if (racing) fast = setInterval(() => (now = Date.now()), 33);
    else slow = setInterval(() => (now = Date.now()), 1000);
  }
  $: setClock(anyRacing);
  onDestroy(() => { clearInterval(fast); clearInterval(slow); });
</script>

{#if players.length}
  <div class="panel" style="--n:{players.length}">
    {#each players as p (p.player_id)}<PlayerCard entry={p} {now} />{/each}
  </div>
{:else}
  <span class="empty">no players</span>
{/if}

<style>
  .panel { display: grid; grid-template-columns: repeat(var(--n, 5), 1fr); gap: 1px;
           background: var(--bd); height: 100%; }
  .empty { font-size: .66rem; color: var(--tx-dim); letter-spacing: .05em; text-transform: uppercase;
           align-self: center; margin: auto; }
</style>
