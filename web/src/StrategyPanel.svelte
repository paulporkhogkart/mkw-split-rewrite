<!-- web/src/StrategyPanel.svelte -->
<script>
  import { golfList, turfList, timeList } from "./lib/strategy.js";

  export let pbs = [];

  const MODES = [
    { key: "golf", label: "GOLF", hint: "cheapest next place to gain" },
    { key: "turf", label: "TURF", hint: "softest #1 to steal" },
    { key: "time", label: "TIME", hint: "your worst PBs vs WR" },
  ];
  let mode = "golf";

  $: rows = mode === "golf" ? golfList(pbs) : mode === "turf" ? turfList(pbs) : timeList(pbs);

  const secs = (ms) => `${(ms / 1000).toFixed(3)}s`;
  function line(r) {
    if (mode === "time") return `${r.off_wr_pct.toFixed(1)}% off WR`;
    if (mode === "turf") return `shave ${secs(r.your_ms - r.leader_ms)} → take #1 (leader ${r.leader_off_wr_pct.toFixed(1)}% off WR)`;
    return `shave ${secs(r.your_ms - r.next_rank_ms)} → ${r.your_rank}${nth(r.your_rank)} → ${r.your_rank - 1}${nth(r.your_rank - 1)}`;
  }
  const nth = (n) => (n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th");
</script>

<section class="strategy">
  <div class="tabs">
    {#each MODES as m (m.key)}
      <button class:on={mode === m.key} on:click={() => (mode = m.key)} title={m.hint}>{m.label}</button>
    {/each}
  </div>
  <p class="hint">{MODES.find((m) => m.key === mode).hint}</p>
  {#if rows.length === 0}
    <p class="empty">Nothing to show here.</p>
  {:else}
    <ol class="rows">
      {#each rows as r (r.slug)}
        <li><span class="course">{r.course}</span><span class="advice">{line(r)}</span></li>
      {/each}
    </ol>
  {/if}
</section>

<style>
  .strategy { margin-top: 1.5rem; }
  .tabs { display: flex; gap: 4px; }
  .tabs button { padding: 4px 14px; border: 1px solid var(--line, #333); background: transparent; color: inherit;
                 cursor: pointer; letter-spacing: 0.08em; font-weight: 600; border-radius: 6px; }
  .tabs button.on { background: var(--line, #333); }
  .hint { opacity: 0.6; font-size: 0.85em; margin: 6px 0; }
  .rows { list-style: none; padding: 0; margin: 0; }
  .rows li { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; border-bottom: 1px solid var(--line, #2a2a2a); }
  .course { font-weight: 600; }
  .advice { font-variant-numeric: tabular-nums; opacity: 0.85; }
  .empty { opacity: 0.6; }
</style>
