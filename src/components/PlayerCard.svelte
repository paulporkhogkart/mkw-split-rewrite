<script>
  import { viewModel } from "../lib/playerCard.js";
  import { figureFor, onpaceFigure } from "../lib/playerFigures.js";
  import { sampleAt, deltaTrendAt, DELAY_MS } from "../lib/raceTimerBuffer.js";
  import { deltaMode } from "../lib/cardSettings.js";
  import { updateFire } from "../lib/fireState.js";
  import Fire from "./Fire.svelte";
  export let entry;
  export let now = Date.now();            // driven by PlayerPanel (fast while racing)
  export let stale = false;              // server link is down: render this card offline (FIRSTS only)
  // Sample the delay buffer only while actually racing: the held/paused states
  // replay the stashed readout, and sampling through a pause would ratchet the
  // monotonic floor above the frozen clock.
  $: isRacing = !stale && !!entry && entry.online !== false && entry.screen === "RACING" && !entry.final_time;
  // Render the live timer + bar DELAY_MS in the past so the finish lines up.
  $: delayed = isRacing ? sampleAt(entry.player_id, now - DELAY_MS) : null;
  // Pace-mode shade needs the delta's direction at the same delayed clock.
  $: trend = isRacing && $deltaMode === "pace" ? deltaTrendAt(entry.player_id, now - DELAY_MS) : null;
  $: vm = viewModel(entry, now, delayed, { deltaMode: $deltaMode, trend, stale });
  // Nothing picked yet: an idle online card shows the offline-style career stats
  // instead of three empty "—" rows (offline cards always take the stat block).
  $: hasSel = !!(vm.char || vm.kart || vm.trk);
  // On fire swaps to the player's on-pace portrait (falls back to the online figure).
  $: fig = onFire ? (onpaceFigure(vm.name) || figureFor(vm.name, true)) : figureFor(vm.name, vm.online);
  // On fire: lit while racing AND on PB pace. Pace mode reads the live (delayed)
  // pace delta; laps mode reads the last completed lap split. fireState applies
  // the "consistently ahead" on-window + anti-flicker off-window.
  $: aheadNow = isRacing && ($deltaMode === "laps"
      ? !!(entry.lap_delta && entry.lap_delta.delta_ms != null && entry.lap_delta.delta_ms < 0)
      : !!(delayed && delayed.pb_delta_ms != null && delayed.pb_delta_ms < 0));
  $: onFire = entry ? updateFire(entry.player_id, { ahead: aheadNow, racing: isRacing, now, mode: $deltaMode }) : false;
</script>

<div class="tt" class:off={!vm.online} style="--pc:{vm.color}">
  <div class="spine"></div>
  {#if isRacing}<Fire color={vm.color} active={onFire} />{/if}
  {#if fig}<div class="fig" style="background-image:url({fig})"></div>{/if}
  <div class="data">
    <div class="nm">{vm.name}</div>
    {#if vm.stats && (!vm.online || !hasSel)}
    <!-- Stable career stats instead of dead selection rows: offline cards (the primary
         line carries "last seen"), and idle online cards that have yet to select anything.
         Render only the rows present — a stale (no-server) card carries FIRSTS only; a
         live-offline or idle online card carries all three. -->
    <div class="sel">
      {#if vm.stats.firsts != null}<div class="kv"><span class="kt">FIRSTS</span><span class="v">{vm.stats.firsts}</span></div>{/if}
      {#if vm.stats.runs_7d != null}<div class="kv"><span class="kt">RUNS · 7D</span><span class="v">{vm.stats.runs_7d}</span></div>{/if}
      {#if vm.stats.pbs_30d != null}<div class="kv"><span class="kt">PBS · 30D</span><span class="v">{vm.stats.pbs_30d}</span></div>{/if}
    </div>
    {:else if vm.online}
    <div class="sel">
      <div class="kv" class:dim={!vm.char}>
        <span class="k" title="Character">
          <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M9.4 8.8v-.8a1.7 1.7 0 0 0-1.7-1.7H4.5a1.7 1.7 0 0 0-1.7 1.7v.8"/><circle cx="6.1" cy="3.4" r="1.7"/>
          </svg>
        </span><span class="v">{vm.char || "—"}</span>
      </div>
      <div class="kv" class:dim={!vm.kart}>
        <span class="k" title="Kart">
          <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M1 7.1V5.9c0-.4.3-.7.6-.8l1.6-.4 1-1.3c.2-.3.5-.4.8-.4h.9c.4 0 .7.2.9.4l1 1.3 1.5.4c.4.1.6.4.6.8v1.2"/>
            <circle cx="3" cy="7.3" r="1.05"/><circle cx="7" cy="7.3" r="1.05"/>
          </svg>
        </span><span class="v">{vm.kart || "—"}</span>
      </div>
      <div class="kv" class:dim={!vm.trk}>
        <span class="k" title="Track">
          <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M1.5 8.4l2.7-5.2 1.8 3.1 1.3-1.8 2.2 3.9z"/>
          </svg>
        </span><span class="v">{vm.trk || "—"}</span>
      </div>
    </div>
    {/if}
    <div class="sp"></div>
    {#if vm.resets != null}
      <div class="foot"><span class="rk">RESETS</span><b>{vm.resets}</b></div>
    {/if}
    {#if vm.pbStr}
      <div class="pb"><span>PB</span>{vm.pbStr}{#if vm.delta}<span class="delta {vm.delta.cls}">{vm.delta.text}</span>{/if}</div>
    {/if}
    {#if vm.primary.kind === "time"}
      <div class="prim time" class:fin={vm.state === "finished"} class:beatpb={vm.finPb}>
        {#if vm.badge === "fin"}
          <span class="tag" title="Finished">
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <path d="M2 1v10.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" fill="none"/>
              <g fill="currentColor">
                <rect x="3.1" y="1.1" width="2.6" height="2.55"/><rect x="8.3" y="1.1" width="2.6" height="2.55"/>
                <rect x="5.7" y="3.65" width="2.6" height="2.55"/>
                <g opacity=".28">
                  <rect x="5.7" y="1.1" width="2.6" height="2.55"/><rect x="3.1" y="3.65" width="2.6" height="2.55"/>
                  <rect x="8.3" y="3.65" width="2.6" height="2.55"/>
                </g>
              </g>
            </svg>
          </span>
        {:else if vm.badge === "pause"}
          <span class="tag" title="Paused">
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <g fill="currentColor"><rect x="3.2" y="1.6" width="2.1" height="8.8" rx=".7"/><rect x="6.7" y="1.6" width="2.1" height="8.8" rx=".7"/></g>
            </svg>
          </span>
        {:else if vm.badge === "reset"}
          <span class="tag" title="Resetting">
            <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <polyline points="11.5 2 11.5 5 8.5 5"/>
              <path d="M10.24 7.5A4.5 4.5 0 1 1 9.18 2.82L11.5 5"/>
            </svg>
          </span>
        {/if}
        <span class="ttx">{vm.primary.text}</span>
      </div>
    {:else if vm.primary.kind === "activity"}
      <div class="prim act">{vm.primary.text}</div>
    {:else}
      <div class="prim seen">{vm.primary.text}</div>
    {/if}
    {#if vm.bar}
      <div class="barwrap">
        <div class="bar"><i style="width:{vm.bar.fill * 100}%"></i></div>
        {#each vm.bar.dividers as d}<span class="tick" style="left:{d * 100}%"></span>{/each}
        {#if vm.bar.calibrating}
          <span class="callab">{vm.bar.calLabel}</span>
        {:else if vm.state === "racing"}
          <span class="live" style="left:{vm.bar.fill * 100}%"></span>
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .tt { --pc: #888; position: relative; height: 100%; min-height: 146px; background: var(--panel);
        overflow: hidden; display: flex; }
  .tt.off { background: var(--well); }
  .spine { flex: 0 0 3px; background: var(--pc); position: relative; z-index: 4; }
  .tt.off .spine { background: var(--idle); }
  /* Portrait strip: a fixed slim slot the figure is deliberately CROPPED into
     (bottom-center sliver cut - face stays, sides bleed off). Figures scale with
     card height, so taller cards crop tighter instead of widening the strip; the
     data column keeps the rest of the card at every size. */
  .fig { flex: 0 0 56px; margin: 11px 0 0 2px; background-repeat: no-repeat;
         background-position: bottom center; background-size: auto 100%;
         position: relative; z-index: 2; }
  .tt.off .fig { filter: grayscale(1) brightness(.6); }
  .data { flex: 1; min-width: 0; padding: 9px 9px 8px 8px;
          display: flex; flex-direction: column; position: relative; z-index: 2; }
  .nm { font-size: 12px; font-weight: 700; color: var(--pc); letter-spacing: .05em; text-transform: uppercase;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tt.off .nm { color: var(--tx-mut); }
  .sel { margin-top: 6px; }
  .kv { font-size: 10px; color: var(--tx); display: flex; gap: 6px; line-height: 1.45; }
  /* Glyph art is drawn flush to its viewBox right edge and the slot right-aligns,
     so every icon ends the same distance from its value text. */
  .kv .k { color: var(--tx-dim); width: 10px; flex: 0 0 auto; display: inline-flex;
           justify-content: flex-end; padding-top: 2.5px; }
  .kv .k svg { width: 9px; height: 9px; }
  .kv .v { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  /* Offline stat labels (FIRSTS / RUNS 7D / PBS 30D). */
  .kv .kt { font-size: 7.5px; letter-spacing: .1em; color: var(--tx-dim); flex: 0 0 auto;
            min-width: 46px; padding-top: 2px; }
  .kv.dim { color: var(--tx-dim); }
  .sp { flex: 1; }
  .foot { display: flex; align-items: center; gap: 6px; }
  .rk { font-size: 7.5px; letter-spacing: .1em; color: var(--tx-dim); }
  .foot b { font-size: 11px; font-weight: 700; color: var(--tx); }
  .pb { font-size: 9.5px; color: var(--tx-dim); margin-top: 3px; display: flex; gap: 5px; align-items: center; }
  .pb span { font-size: 7.5px; letter-spacing: .1em; }
  /* All deltas follow LiveSplit conventions: sharp red = losing + behind,
     light red = gaining but behind, light green = losing but ahead, sharp
     green = gaining + ahead, gold = best-ever segment (lap mode only). The
     finished exact delta uses the settled sharp pair. */
  .delta { font-weight: 600; transition: color .35s; }
  .delta.behind-loss { color: var(--ls-behind); }
  .delta.behind-gain { color: var(--ls-behind-soft); }
  .delta.ahead-loss  { color: var(--ls-ahead-soft); }
  .delta.ahead-gain  { color: var(--ls-ahead); }
  .delta.gold        { color: var(--ls-gold); }
  .prim.time { font-size: 20px; font-weight: 700; color: var(--tx); line-height: 1; margin-top: 2px;
               min-height: 20px; display: flex; align-items: center; gap: 5px; }
  /* Trim the digits' box to cap..baseline so flex centring is optical, not em-box
     (Segoe UI's tall ascent paints glyphs ~1.3px below the box centre otherwise). */
  .prim.time .ttx { text-box: trim-both cap alphabetic; }
  /* Finished: the time takes the verdict colour - green beat the pre-race PB, red didn't.
     NB: the PB-beat class is `beatpb`, NOT `pb` - the `.pb` PB-readout row carries a
     `.pb span { font-size: 7.5px }` rule that would otherwise shrink the timer's .ttx span. */
  .prim.time.fin { color: var(--ls-behind); }
  .prim.time.fin.beatpb { color: var(--ls-ahead); }
  /* State badge beside the time: checkered flag / pause bars / reset arrow.
     No nudges: the time text is cap-trimmed (.ttx), so centre IS the digit centre. */
  .tag { display: inline-flex; color: var(--tx-dim); flex: 0 0 auto; }
  .tag svg { width: 11px; height: 11px; }
  .prim.act { font-size: 11.5px; font-weight: 600; color: var(--tx-mut); margin-top: 4px; }
  .prim.seen { font-size: 10.5px; color: var(--tx-dim); margin-top: 4px; }
  .barwrap { position: relative; margin-top: 7px; }
  .bar { height: 4px; background: var(--track); overflow: hidden; border-radius: 1px; }
  /* Ease the fill so it glides between frames — the live % unavoidably holds for a beat at each
     start/finish line (the minimap can't resolve progress where the lap loops on itself); easing
     keeps the bar gliding through that beat instead of freezing then snapping. */
  .bar > i { display: block; height: 100%; background: var(--pc); transition: width .22s linear; }
  .tick { position: absolute; top: 0; width: 1.5px; height: 4px; margin-left: -0.75px; background: var(--panel);
          box-shadow: 0 0 0 0.5px rgba(0,0,0,.35); }
  .live { position: absolute; top: 2px; width: 7px; height: 7px; margin-left: -3.5px; border-radius: 50%;
          background: var(--pc); transform: translateY(-50%); box-shadow: 0 0 0 1.5px var(--panel);
          transition: left .22s linear; }
  .live::after { content: ""; position: absolute; inset: 0; border-radius: 50%; background: var(--pc);
                 animation: ppulse 1.7s ease-out infinite; }
  @keyframes ppulse { 0% { transform: scale(1); opacity: .55; } 100% { transform: scale(2.6); opacity: 0; } }
  /* First run on a course: no model yet, the bar shell shows placeholder lap ticks. */
  .callab { position: absolute; top: -2px; left: 0; right: 0; text-align: center; font-size: 8px;
            line-height: 8px; letter-spacing: .12em; text-transform: uppercase; color: var(--idle);
            pointer-events: none; }
</style>
