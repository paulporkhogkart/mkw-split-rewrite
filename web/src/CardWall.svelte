<script>
  import { onDestroy } from "svelte";
  import { presence, serverConnection } from "../../src/lib/stores.js";
  import PlayerCard from "../../src/components/PlayerCard.svelte";
  import ActivityLog from "./ActivityLog.svelte";

  // Render in stable ascending player_id order; the server seeds all roster players
  // (offline), so the wall is populated as soon as the first snapshot lands.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: connected = $serverConnection.connected;
  // No live link -> every card renders stale/offline (the website has no local-self echo).
  $: anyRacing = connected && players.some((p) => p.online && p.screen === "RACING" && !p.final_time);

  // One shared clock for all cards: ~30fps while someone races (so the ms timer ticks),
  // else a cheap 1s tick. Avoids a per-card animation loop. (Mirrors PlayerPanel.svelte.)
  let now = Date.now();
  let fast = 0, slow = 0, clockRacing = null;
  function setClock(racing) {
    if (racing === clockRacing) return;
    clockRacing = racing;
    clearInterval(fast); clearInterval(slow); fast = 0; slow = 0;
    now = Date.now();
    if (racing) fast = setInterval(() => (now = Date.now()), 33);
    else slow = setInterval(() => (now = Date.now()), 1000);
  }
  $: setClock(anyRacing);
  onDestroy(() => { clearInterval(fast); clearInterval(slow); });
</script>

{#if players.length}
  <div class="wall">
    {#each players as p (p.player_id)}
      <div class="cell"><PlayerCard entry={p} {now} stale={!connected} /></div>
    {/each}
  </div>
{:else}
  <div class="empty">Connecting to the season server…</div>
{/if}

<ActivityLog {now} />

<style>
  /* One centered row of native ~189px cards; shrink to 170px to hold the row, then
     wrap-and-center; one full-width column on a phone. */
  .wall { max-width: 1200px; margin: 16px auto 0; padding: 0 18px; display: flex; flex-wrap: wrap;
          justify-content: center; gap: 8px; }
  .cell { flex: 0 1 189px; min-width: 170px; height: 172px; }
  .cell > :global(.tt) { width: 100%; }
  @media (max-width: 430px) { .cell { flex-basis: 100%; min-width: 0; } }
  .empty { text-align: center; color: var(--tx-mut); font-size: .8rem; padding: 48px 0; }
</style>
