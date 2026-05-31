<script>
  import { scoreColor, fmtScore } from "../lib/format.js";

  /**
   * Ordered list of candidates.
   * @type {{ name: string, score: number }[]}
   */
  export let candidates = [];

  /** Clamp a value to [min, max]. */
  function clamp(v, min, max) {
    return Math.min(max, Math.max(min, v));
  }
</script>

<div class="well">
  {#each candidates as c (c.name)}
    <div class="cand-row">
      <span class="cand-name">{c.name}</span>
      <div class="bar-track">
        <div
          class="bar-fill"
          style="width:{clamp(c.score * 100, 0, 100)}%; background:{scoreColor(c.score)}"
        ></div>
      </div>
      <span class="cand-score" style="color:{scoreColor(c.score)}">{fmtScore(c.score)}</span>
    </div>
  {/each}
</div>

<style>
  .well {
    background: var(--well);
  }
  .cand-row {
    display: grid;
    grid-template-columns: 1fr 50px auto;
    gap: 9px;
    align-items: center;
    padding: 4px 12px;
    font-size: 12px;
    box-sizing: border-box;
  }
  .cand-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--tx-mut);
  }
  .bar-track {
    height: 3px;
    background: var(--track);
    overflow: hidden;
    border-radius: 0;
  }
  .bar-fill {
    height: 100%;
  }
  .cand-score {
    font-size: 11px;
    white-space: nowrap;
    flex-shrink: 0;
  }
</style>
