<script>
  import { race, presence, myPlayerId } from "../lib/stores.js";
  import { fmtSplit } from "../lib/format.js";
  // Delta formatting/colour rules are shared with the player cards.
  import { fmtTimeMs, lapDeltaVm, pbDelta } from "../lib/playerCard.js";

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

  // My presence entry carries the server-side PB comparison: the PB run's lap
  // durations, one LiveSplit delta row per completed lap, and the pinned PB total.
  $: me = $myPlayerId != null ? $presence[$myPlayerId] : null;
  $: pbLaps = me?.pb_laps_ms ?? null;
  $: lapDs = me?.lap_deltas ?? null;
  $: pbTotalMs = me?.pb_ms ?? null;
  $: totalDelta = $race?.finishTime && pbTotalMs != null ? pbDelta($race.finishTime, pbTotalMs) : null;
</script>

{#if $race}
  {#if $race.invalidated}
    <!-- The run was killed by an overlay (Photo Mode / GameChat): its readouts are no
         longer tracked, so show only the cause until the next run restores them. -->
    <div class="row row-invalid">
      <span class="invalid-msg">Run invalidated{$race.invalidReason ? ` - ${$race.invalidReason}` : ""}</span>
    </div>
  {:else}
    {@const laps = lapList($race)}
    {@const splits = $race.splits ?? {}}

    {#each laps as n}
      {@const splitStr = fmtSplit(splits[n])}
      {@const isActive = n === $race.curLap}
      {@const hasValue = splits[n] != null && splits[n] !== ""}
      {@const d = lapDs ? lapDeltaVm(lapDs[n - 1], "segment") : null}
      <div class="row">
        <span class="lbl" class:lbl-dim={!hasValue && !isActive}>Lap {n}</span>
        <span class="pbv">{pbLaps && pbLaps[n - 1] != null ? fmtTimeMs(pbLaps[n - 1]) : ""}</span>
        <span class="dlt {d ? d.cls : ''}">{d ? d.text : ""}</span>
        <span
          class="val"
          class:val-dim={!hasValue && !isActive}
        >{splitStr}</span>
      </div>
    {/each}

    <div class="row row-total">
      <span class="lbl-total">Total</span>
      <span class="pbv">{$race.dnf ? "" : (pbTotalMs != null ? fmtTimeMs(pbTotalMs) : "")}</span>
      <span class="dlt {totalDelta ? totalDelta.cls : ''}">{$race.dnf ? "" : (totalDelta ? totalDelta.text : "")}</span>
      <span class="val" class:dnf={$race.dnf}>{$race.dnf ? "DNF" : ($race.finishTime ?? "-")}</span>
    </div>

    <div class="row">
      <span class="lbl">Coins</span>
      <span class="pbv"></span><span class="dlt"></span>
      <span class="val">{$race.coins ?? "-"}</span>
    </div>

    <div class="row">
      <span class="lbl">Mushrooms</span>
      <span class="pbv"></span><span class="dlt"></span>
      <span class="val">{$race.mushrooms != null ? `×${$race.mushrooms}` : "-"}</span>
    </div>
  {/if}
{/if}

<style>
  .row {
    display: grid;
    /* label | PB reference | LiveSplit delta | live value */
    grid-template-columns: 1fr auto auto auto;
    column-gap: 9px;
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

  .pbv { color: var(--tx-dim); font-size: 11px; white-space: nowrap; text-align: right; }

  .dlt { font-size: 11px; font-weight: 600; white-space: nowrap; text-align: right; transition: color .35s; }
  .dlt.behind-loss { color: var(--ls-behind); }
  .dlt.behind-gain { color: var(--ls-behind-soft); }
  .dlt.ahead-loss  { color: var(--ls-ahead-soft); }
  .dlt.ahead-gain  { color: var(--ls-ahead); }
  .dlt.gold        { color: var(--ls-gold); }

  .val {
    color: var(--tx);
    white-space: nowrap;
    flex-shrink: 0;
    text-align: right;
  }

  .val-dim {
    color: var(--tx-dim);
  }

  .val.dnf {
    color: var(--ls-behind);
    font-weight: 600;
  }

  .row-total {
    font-weight: 600;
    border-top: 1px solid var(--bd-soft);
  }

  .row-invalid { justify-items: start; padding: 9px 12px; }
  .invalid-msg { color: var(--ls-behind); font-weight: 600; font-size: 12.5px; }

  .lbl-total {
    color: var(--tx);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
