<!-- web/src/PlayerProfile.svelte -->
<script>
  import { playerSummaryUrl } from "./lib/api.js";
  import { fmtTime } from "./lib/activityFormat.js";
  import { playerKey } from "../../src/lib/playerKey.js";
  import StrategyPanel from "./StrategyPanel.svelte";

  export let slug;

  let summary = null;
  let error = null;

  $: load(slug);
  async function load(s) {
    summary = null; error = null;
    try {
      const res = await fetch(playerSummaryUrl(s));
      if (res.status === 404) { error = "No such player."; return; }
      if (!res.ok) { error = "Couldn't load this player."; return; }
      summary = await res.json();
    } catch (e) { error = String(e); }
  }

  const pct = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
  const ord = (r) => (r == null || r === 0 ? "—" : `#${r}`);
</script>

{#if error}
  <p class="err">{error}</p>
{:else if summary}
  <section class="profile">
    <header class="head">
      <img class="figure" src={`/players/${playerKey(summary.profile.display_name)}.gif`} alt={summary.profile.display_name} />
      <div class="tiles">
        <div class="tile"><span class="k">Turf</span><span class="v">{summary.headline.turf.pct}%</span><span class="r">{ord(summary.headline.turf.rank)}</span></div>
        <div class="tile"><span class="k">Total time</span><span class="v">{fmtTime(summary.headline.time.total_ms)}</span><span class="r">{ord(summary.headline.time.rank)}</span></div>
        <div class="tile"><span class="k">Golf</span><span class="v">{summary.headline.golf.points}</span><span class="r">{ord(summary.headline.golf.rank)}</span></div>
        <div class="tile"><span class="k">% off WR</span><span class="v">{pct(summary.headline.offwr.avg_pct)}</span><span class="r">{ord(summary.headline.offwr.rank)}</span></div>
      </div>
    </header>

    <h2>{summary.profile.display_name}</h2>

    <table class="pbs">
      <thead><tr><th>Course</th><th>PB</th><th>Rank</th><th>WR</th><th>Δ WR</th><th>Gap ↑</th></tr></thead>
      <tbody>
        {#each summary.pbs as r (r.slug)}
          <tr>
            <td class="course">{r.course}</td>
            <td class="num">{fmtTime(r.your_ms)}</td>
            <td class="num">{r.your_rank}/{r.field_size}</td>
            <td class="num">{r.wr_ms == null ? "—" : fmtTime(r.wr_ms)}</td>
            <td class="num">{pct(r.off_wr_pct)}</td>
            <td class="num">{r.gap_to_next_ms == null ? "—" : `+${(r.gap_to_next_ms / 1000).toFixed(3)}`}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    <StrategyPanel pbs={summary.pbs} />
  </section>
{:else}
  <p class="loading">Loading…</p>
{/if}

<style>
  .profile { padding: 1rem; }
  .head { display: flex; gap: 16px; align-items: center; }
  .figure { width: 120px; height: 120px; object-fit: contain; }
  .tiles { display: grid; grid-template-columns: repeat(2, minmax(120px, 1fr)); gap: 8px; }
  .tile { display: flex; flex-direction: column; padding: 8px 12px; border: 1px solid var(--line, #333); border-radius: 8px; }
  .tile .k { font-size: 0.75em; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.04em; }
  .tile .v { font-size: 1.3em; font-variant-numeric: tabular-nums; }
  .tile .r { font-size: 0.8em; opacity: 0.6; font-variant-numeric: tabular-nums; }
  .pbs { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  .pbs th, .pbs td { padding: 4px 8px; border-bottom: 1px solid var(--line, #2a2a2a); text-align: left; }
  .pbs .num { font-variant-numeric: tabular-nums; text-align: right; }
  .err { color: #d66; padding: 1rem; }
  .loading { padding: 1rem; opacity: 0.6; }
</style>
