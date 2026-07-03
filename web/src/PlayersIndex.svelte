<!-- web/src/PlayersIndex.svelte -->
<script>
  import { onMount } from "svelte";
  import { rosterUrl, territoryUrl } from "./lib/api.js";
  import { playerSlug } from "./lib/playerSlug.js";
  import { playerKey } from "../../src/lib/playerKey.js";

  let players = [];   // [{ display_name, slug, key, turfPct }]
  let error = null;

  onMount(async () => {
    try {
      const [roster, territory] = await Promise.all([
        fetch(rosterUrl()).then((r) => r.json()),
        fetch(territoryUrl()).then((r) => r.json()),
      ]);
      const total = territory.length || 1;
      const owned = {};
      for (const c of territory) if (c.owner_player_id != null) owned[c.owner_player_id] = (owned[c.owner_player_id] || 0) + 1;
      players = roster.map((p) => ({
        display_name: p.display_name,
        slug: playerSlug(p.display_name),
        key: playerKey(p.display_name),
        turfPct: Math.round(((owned[p.player_id] || 0) / total) * 100),
      }));
    } catch (e) { error = String(e); }
  });
</script>

<section class="players-index">
  <h1>Players</h1>
  {#if error}<p class="err">Couldn't load players: {error}</p>{/if}
  <div class="grid">
    {#each players as p (p.slug)}
      <a class="card" href={`/players/${p.slug}`}>
        <img class="figure" src={`/players/${p.key}.gif`} alt={p.display_name} loading="lazy" />
        <span class="name">{p.display_name}</span>
        <span class="turf">{p.turfPct}% turf</span>
      </a>
    {/each}
  </div>
</section>

<style>
  .players-index { padding: 1rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
  .card { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px;
          text-decoration: none; color: inherit; border: 1px solid var(--line, #333); border-radius: 8px; }
  .figure { width: 100px; height: 100px; object-fit: contain; }
  .name { font-weight: 600; }
  .turf { font-variant-numeric: tabular-nums; opacity: 0.7; font-size: 0.85em; }
  .err { color: #d66; }
</style>
