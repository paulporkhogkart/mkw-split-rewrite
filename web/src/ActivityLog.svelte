<script>
  import { onMount, onDestroy } from "svelte";
  import { activity } from "../../src/lib/stores.js";
  import { toRow } from "./lib/activityFormat.js";
  import { API_BASE } from "./lib/api.js";
  import { loadActivityHistory, startActivityStream } from "./activityClient.js";

  export let now = Date.now();   // shared clock from CardWall (for relative "when")

  $: rows = $activity.map((r) => toRow(r, now)).filter(Boolean);

  let stop = () => {};
  onMount(() => { loadActivityHistory(API_BASE); stop = startActivityStream(API_BASE); });
  onDestroy(() => stop());
</script>

{#if rows.length}
  <section class="log" aria-label="Activity log">
    {#each rows as r (r.id)}
      <div class="row" class:pb={!!r.strip} style={r.strip ? `--pc:${r.strip}` : ""}>
        <div class="when">{r.when}</div>
        <div class="who" class:sys={r.sys} style={r.who.color ? `color:${r.who.color}` : ""}>{r.who.text}</div>
        <div class="where" class:dim={r.where.dim}>{r.where.text}</div>
        <div class="what">{#each r.what as s, i (i)}<span class={s.cls} style={s.color ? `color:${s.color}` : ""}>{s.text}</span>{/each}</div>
      </div>
    {/each}
  </section>
{/if}

<style>
  .log { max-width: 720px; margin: 22px auto 40px; padding: 0 18px; }
  .row { display: grid; grid-template-columns: 42px 74px 150px 1fr; align-items: baseline; column-gap: 12px;
         padding: 7px 14px 7px 12px; border-bottom: 1px solid var(--bd-soft); border-left: 2px solid transparent;
         background: var(--panel); font-size: 12.5px; }
  .row:first-child { border-top: 1px solid var(--bd-soft); border-top-left-radius: var(--r); border-top-right-radius: var(--r); }
  .row:last-child { border-bottom-left-radius: var(--r); border-bottom-right-radius: var(--r); }
  .row.pb { border-left-color: var(--pc); }
  .when { font-size: 11px; color: var(--tx-dim); text-align: right; white-space: nowrap; }
  .who { font-weight: 600; color: var(--tx-mut); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .who.sys { font-size: 10px; font-weight: 700; letter-spacing: .11em; text-transform: uppercase; color: var(--tx-dim); }
  .where { color: var(--tx-mut); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .where.dim { color: var(--tx-dim); }
  .what { color: var(--tx-mut); }
  .what :global(.t) { color: var(--tx); font-weight: 600; }
  .what :global(.delta) { color: var(--tx-mut); }
  .what :global(.dim) { color: var(--tx-dim); }
  .what :global(.name) { font-weight: 600; }
</style>
