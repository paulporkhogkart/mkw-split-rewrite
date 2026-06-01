<script>
  import { createEventDispatcher } from "svelte";
  import { scoreColor, fmtScore } from "../lib/format.js";

  /** Primary value to display (left cell) */
  export let value = "";
  /** Confidence/match score 0–1 (right cell) */
  export let score = 0;
  /** True when there is no value - renders value dim + italic, score as 0.00 dim */
  export let empty = false;
  /** True when this row is expanded (shows candidates below) - triggers raised bg */
  export let expanded = false;

  const dispatch = createEventDispatcher();
</script>

<button
  type="button"
  class="row"
  class:row-empty={empty}
  class:row-expanded={expanded}
  on:click={() => dispatch("toggle")}
>
  <span class="val">{value}</span>
  <span
    class="sc"
    style="color:{empty ? 'var(--tx-dim)' : scoreColor(score)}"
  >{empty ? "0.00" : fmtScore(score)}</span>
</button>

<style>
  .row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 10px;
    align-items: center;
    padding: 5px 12px;
    font-size: 12.5px;
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    border-radius: 0;
    font-family: inherit;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum";
    color: var(--tx);
    cursor: pointer;
    box-sizing: border-box;
    line-height: 1.4;
  }
  .row:hover {
    background: var(--panel);
  }
  .row.row-expanded {
    background: var(--raised);
  }
  .row.row-expanded:hover {
    background: var(--raised);
  }
  .val {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row-empty .val {
    color: var(--tx-dim);
    font-style: italic;
  }
  .sc {
    font-size: 11.5px;
    white-space: nowrap;
    flex-shrink: 0;
  }
</style>
