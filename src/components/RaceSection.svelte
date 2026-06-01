<script>
  import { race } from "../lib/stores.js";
  import { fmtSplit } from "../lib/format.js";

  /**
   * Build the lap list to render.
   * totLap drives the count when known; fall back to the number of split keys.
   * Returns an empty array when both are absent.
   */
  function lapList(r) {
    const total = r.totLap ?? Object.keys(r.splits ?? {}).length;
    if (!total) return [];
    return Array.from({ length: total }, (_, i) => i + 1);
  }
</script>

{#if $race}
  {@const laps = lapList($race)}
  {@const splits = $race.splits ?? {}}

  {#each laps as n}
    {@const splitStr = fmtSplit(splits[n])}
    {@const isActive = n === $race.curLap}
    {@const hasValue = splits[n] != null && splits[n] !== ""}
    <div class="row">
      <span class="lbl" class:lbl-dim={!hasValue && !isActive}>Lap {n}</span>
      <span
        class="val"
        class:val-dim={!hasValue && !isActive}
      >{splitStr}</span>
    </div>
  {/each}

  <div class="row row-total">
    <span class="lbl-total">Total</span>
    <span class="val">{$race.finishTime ?? "-"}</span>
  </div>

  <div class="row">
    <span class="lbl">Coins</span>
    <span class="val">{$race.coins ?? "-"}</span>
  </div>

  <div class="row">
    <span class="lbl">Mushrooms</span>
    <span class="val">{$race.mushrooms != null ? `×${$race.mushrooms}` : "-"}</span>
  </div>
{/if}

<style>
  .row {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    padding: 5px 12px;
    font-size: 12.5px;
    font-family: inherit;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum";
    line-height: 1.4;
  }

  .lbl {
    color: var(--tx-mut);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .lbl-dim {
    color: var(--tx-dim);
  }

  .val {
    color: var(--tx);
    white-space: nowrap;
    flex-shrink: 0;
    text-align: right;
  }

  .val-dim {
    color: var(--tx-dim);
  }

  .row-total {
    font-weight: 600;
    border-top: 1px solid var(--bd-soft);
  }

  .lbl-total {
    color: var(--tx);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
