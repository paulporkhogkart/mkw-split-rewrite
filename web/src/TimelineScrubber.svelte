<script>
  // Self-contained, presentational timeline strip. Props in, events out; it knows nothing about
  // the map or the data — so it can be placed anywhere (placement is provisional per SP4).
  import { createEventDispatcher } from "svelte";

  export let snapshots = [];
  export let index = 0;
  export let playing = false;
  export let coloredTicks = true; // tint each tick by the colour of the course that flipped there

  const dispatch = createEventDispatcher();

  $: n = snapshots.length;
  $: last = Math.max(0, n - 1);
  $: atLive = index >= last;
  $: cur = snapshots[index] || null;

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

    <div class="track-wrap">
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
      <div class="ends" aria-hidden="true">
        <span>{snapshots[0]?.date ?? ""}</span>
        <span>{snapshots[last]?.date ?? ""}</span>
      </div>
    </div>

    <div class="readout">
      <span class="date">{cur?.date ?? ""}</span>
      {#if atLive}<span class="live">LIVE</span>{/if}
    </div>
  </div>
{/if}

<style>
  /* Sits inside the World Map console (which provides the panel surface), so the scrubber itself
     is a transparent transport row. */
  .scrubber {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    box-sizing: border-box;
    padding: 4px 0 0;
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
  .play:hover { border-color: var(--accent); color: #fff; }
  .play:focus-visible { outline: none; border-color: var(--accent); }
  .play svg { width: 15px; height: 15px; fill: currentColor; display: block; }

  .track-wrap { flex: 1; min-width: 0; }

  .track { position: relative; height: 26px; }

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
    background: var(--accent-soft);
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
    height: 16px;
    margin: 0;
    background: transparent;
    -webkit-appearance: none;
    appearance: none;
    cursor: pointer;
  }
  .range:focus { outline: none; }
  .range::-webkit-slider-runnable-track { background: transparent; height: 16px; }
  .range::-moz-range-track { background: transparent; height: 16px; }
  .range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--accent);
    border: 2px solid #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.55);
  }
  .range::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--accent);
    border: 2px solid #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.55);
  }
  .range:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 3px var(--accent-bg); }
  .range:focus-visible::-moz-range-thumb { box-shadow: 0 0 0 3px var(--accent-bg); }

  .ends {
    display: flex;
    justify-content: space-between;
    margin-top: 4px;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--tx-dim);
  }

  .readout {
    flex: none;
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 96px;
    justify-content: flex-end;
  }
  .date {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-size: 13px;
    color: var(--tx);
    letter-spacing: 0.2px;
  }
  .live {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
    color: #fff;
    background: var(--accent);
    padding: 2px 5px;
    border-radius: var(--r-sm);
  }

  @media (max-width: 560px) {
    .scrubber { gap: 10px; padding: 8px 10px; }
    .readout { min-width: 84px; }
  }
</style>
