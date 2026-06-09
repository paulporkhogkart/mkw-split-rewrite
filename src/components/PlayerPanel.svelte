<script>
  import { presence } from "../lib/stores.js";
  import PlayerCard from "./PlayerCard.svelte";
  // presence is { [player_id]: entry }; render in stable ascending player_id order.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
</script>

{#if players.length}
  <div class="panel" style="--n:{players.length}">
    {#each players as p (p.player_id)}<PlayerCard entry={p} />{/each}
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
