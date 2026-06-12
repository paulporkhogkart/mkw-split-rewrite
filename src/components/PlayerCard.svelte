<script>
  import { viewModel } from "../lib/playerCard.js";
  import { figureFor } from "../lib/playerFigures.js";
  import { sampleAt, DELAY_MS } from "../lib/raceTimerBuffer.js";
  export let entry;
  export let now = Date.now();            // driven by PlayerPanel (fast while racing)
  // Render the live timer + bar DELAY_MS in the past so the finish lines up.
  $: delayed = entry ? sampleAt(entry.player_id, now - DELAY_MS) : null;
  $: vm = viewModel(entry, now, delayed);
  $: fig = figureFor(vm.name, vm.online);
</script>

<div class="tt" class:off={!vm.online} style="--pc:{vm.color}">
  <div class="spine"></div>
  {#if fig}<div class="fig" style="background-image:url({fig})"></div>{/if}
  <div class="data">
    <div class="nm">{vm.name}</div>
    <div class="sel">
      <div class="kv" class:dim={!vm.char}><span class="k">C</span><span class="v">{vm.char || "—"}</span></div>
      <div class="kv" class:dim={!vm.kart}><span class="k">K</span><span class="v">{vm.kart || "—"}</span></div>
      <div class="kv" class:dim={!vm.trk}><span class="k">T</span><span class="v">{vm.trk || "—"}</span></div>
    </div>
    <div class="sp"></div>
    {#if vm.resets != null}
      <div class="foot"><span class="rk">RESETS</span><b>{vm.resets}</b></div>
    {/if}
    {#if vm.pbStr}
      <div class="pb"><span>PB</span>{vm.pbStr}{#if vm.delta}<span class="delta {vm.delta.cls}">{vm.delta.text}</span>{/if}</div>
    {/if}
    {#if vm.primary.kind === "time"}
      <div class="prim time" class:fin={vm.state === "finished"} class:pb={vm.finPb}>{vm.primary.text}</div>
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
          <span class="callab">calibrating</span>
        {:else}
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
  .spine { flex: 0 0 3px; background: var(--pc); }
  .tt.off .spine { background: var(--idle); }
  /* Figure box: width derives from height at the player crops' common ~0.62 aspect
     (84:135, see scripts/gen_player_figures.py), so those figures sit flush against
     the spine and the data column at any card size; narrower crops centre with small
     margins and wider ones clip symmetrically. */
  .fig { flex: 0 0 auto; aspect-ratio: 84 / 135; margin: 11px 0 0 2px; background-repeat: no-repeat;
         background-position: bottom center; background-size: auto 100%; }
  .tt.off .fig { filter: grayscale(1) brightness(.6); }
  .data { flex: 1; min-width: 0; padding: 9px 9px 8px 8px;
          display: flex; flex-direction: column; }
  .nm { font-size: 12px; font-weight: 700; color: var(--pc); letter-spacing: .05em; text-transform: uppercase;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tt.off .nm { color: var(--tx-mut); }
  .sel { margin-top: 6px; }
  .kv { font-size: 10px; color: var(--tx); display: flex; gap: 6px; line-height: 1.45; }
  .kv .k { color: var(--tx-dim); font-size: 7.5px; letter-spacing: .08em; width: 8px; flex: 0 0 auto; padding-top: 2px; }
  .kv .v { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .kv.dim { color: var(--tx-dim); }
  .sp { flex: 1; }
  .foot { display: flex; align-items: center; gap: 6px; }
  .rk { font-size: 7.5px; letter-spacing: .1em; color: var(--tx-dim); }
  .foot b { font-size: 11px; font-weight: 700; color: var(--tx); }
  .pb { font-size: 9.5px; color: var(--tx-dim); margin-top: 3px; display: flex; gap: 5px; align-items: center; }
  .pb span { font-size: 7.5px; letter-spacing: .1em; }
  .delta { font-weight: 600; }
  .delta.slow { color: var(--warn); }
  .delta.fast { color: var(--ok); }
  .prim.time { font-size: 20px; font-weight: 700; color: var(--tx); line-height: 1; margin-top: 2px; }
  /* Finished: green when the run beat the pre-race PB, neutral when it didn't. */
  .prim.time.fin { color: #cfe0f2; }
  .prim.time.fin.pb { color: var(--ok); }
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
