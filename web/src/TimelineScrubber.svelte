<script>
  // Self-contained, presentational timeline strip: a play toggle + a scrub track. Props in,
  // events out; it knows nothing about the map or the data. The date readout now lives on the
  // map pane itself (WorldMap), so there's no date/LIVE/end-label chrome here.
  import { createEventDispatcher } from "svelte";

  export let snapshots = [];
  export let index = 0;
  export let playing = false;
  export let coloredTicks = true; // tint each tick by the colour of the course that flipped there

  const dispatch = createEventDispatcher();

  $: n = snapshots.length;
  $: last = Math.max(0, n - 1);

  const pct = (i) => (last > 0 ? (i / last) * 100 : 0);
  const onScrub = (e) => dispatch("scrub", { index: +e.target.value });
  const toggle = () => dispatch("toggle");
</script>

{#if n > 0}
  <div class="scrubber">
    <button class="play" on:click={toggle} aria-label={playing ? "Pause" : "Play"} title={playing ? "Pause" : "Play"}>
      {#if playing}
        <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="3" width="3" height="10" /><rect x="9" y="3" width="3" height="10" /></svg>
      {:else}
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3l8 5-8 5z" /></svg>
      {/if}
    </button>

    <div class="track">
      <div class="rail" aria-hidden="true"></div>
      <div class="fill" aria-hidden="true" style="width:{pct(index)}%"></div>
      <div class="ticks" aria-hidden="true">
        {#each snapshots as s, i}
          <span class="tick" style="left:{pct(i)}%;{coloredTicks && s.gainColor ? `--c:${s.gainColor}` : ''}"></span>
        {/each}
      </div>
      <input
        class="range"
        type="range"
        min="0"
        max={last}
        step="1"
        value={index}
        on:input={onScrub}
        aria-label="Timeline position"
      />
    </div>
  </div>
{/if}

<style>
  /* Sits inside the World Map console, which provides the panel surface, so the scrubber itself
     is a transparent transport row: a play button + the scrub track. */
  .scrubber {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    box-sizing: border-box;
    background: transparent;
    border: 0;
  }

  .play {
    flex: none;
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    padding: 0;
    background: var(--panel-2);
    border: 1px solid var(--bd);
    border-radius: var(--r);
    color: var(--tx);
    cursor: pointer;
    transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
  }
  .play:hover { border-color: var(--tx-mut); color: #fff; background: var(--raised); }
  .play:focus-visible { outline: none; border-color: var(--tx-mut); }
  .play svg { width: 15px; height: 15px; fill: currentColor; display: block; }

  .track { position: relative; flex: 1; min-width: 0; height: 26px; }

  /* The rail, fill and ticks sit centred behind the (transparent-track) range input. */
  .rail {
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
    height: 4px;
    border-radius: var(--r-sm);
    background: var(--bd-soft);
  }
  .fill {
    position: absolute;
    left: 0;
    top: 50%;
    transform: translateY(-50%);
    height: 4px;
    border-radius: var(--r-sm);
    background: rgba(255, 255, 255, 0.18);
    transition: width 0.15s linear;
  }
  .ticks { position: absolute; inset: 0; pointer-events: none; }
  .tick {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    width: 2px;
    height: 9px;
    border-radius: 1px;
    background: var(--c, var(--tx-dim));
    opacity: 0.5;
  }

  .range {
    position: absolute;
    left: 0;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
    width: 100%;
    height: 18px;
    margin: 0;
    background: transparent;
    -webkit-appearance: none;
    appearance: none;
    cursor: pointer;
  }
  .range:focus { outline: none; }
  .range::-webkit-slider-runnable-track { background: transparent; height: 18px; }
  .range::-moz-range-track { background: transparent; height: 18px; }
  .range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 2px;
    height: 18px;
    border-radius: 1px;
    background: #f3f4f6;
    border: none;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 1px 4px rgba(0, 0, 0, 0.6);
  }
  .range::-moz-range-thumb {
    width: 2px;
    height: 18px;
    border-radius: 1px;
    background: #f3f4f6;
    border: none;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 1px 4px rgba(0, 0, 0, 0.6);
  }
  .range:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 0 0 3px rgba(255, 255, 255, 0.3); }
  .range:focus-visible::-moz-range-thumb { box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 0 0 3px rgba(255, 255, 255, 0.3); }

  @media (max-width: 560px) {
    .scrubber { gap: 10px; }
  }
</style>
