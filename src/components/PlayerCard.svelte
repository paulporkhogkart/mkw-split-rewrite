<script>
  import { viewModel } from "../lib/playerCard.js";
  import { figureFor } from "../lib/playerFigures.js";
  import { nowTick } from "../lib/clock.js";
  export let entry;
  // $nowTick drives recompute so "last seen" advances even when an offline player sends no frames.
  $: vm = viewModel(entry, $nowTick);
  $: fig = figureFor(vm.name, vm.online);
  // TEMP debug: raw projector completion %, for live-tuning the progress bar. Remove when validated.
  $: dbgPct = entry && entry.completion != null ? (entry.completion * 100).toFixed(1) : null;
</script>

<div class="tt" class:off={!vm.online} style="--pc:{vm.color}">
  <div class="spine"></div>
  {#if fig}<div class="fig" style="background-image:url({fig})"></div>{/if}
  <div class="data">
    <div class="nm">{vm.name}</div>
    <div class="sel">
      <div class="kv" class:dim={!vm.char}><span>CHR</span>{vm.char || "—"}</div>
      <div class="kv" class:dim={!vm.kart}><span>KRT</span>{vm.kart || "—"}</div>
      <div class="kv" class:dim={!vm.trk}><span>TRK</span>{vm.trk || "—"}</div>
    </div>
    <div class="sp"></div>
    {#if vm.resets != null}
      <div class="foot"><span class="rk">RESETS</span><b>{vm.resets}</b></div>
    {/if}
    {#if vm.pbStr}
      <div class="pb"><span>PB</span>{vm.pbStr}{#if vm.delta}<span class="delta {vm.delta.cls}">{vm.delta.text}</span>{/if}</div>
    {/if}
    {#if vm.primary.kind === "time"}
      <div class="prim time" class:fin={vm.state === "finished"}>{vm.primary.text}</div>
    {:else if vm.primary.kind === "activity"}
      <div class="prim act">{vm.primary.text}</div>
    {:else}
      <div class="prim seen">{vm.primary.text}</div>
    {/if}
    {#if vm.bar}
      <div class="barwrap">
        <div class="bar"><i style="width:{vm.bar.fill * 100}%"></i></div>
        {#each vm.bar.dividers as d}<span class="tick" style="left:{d * 100}%"></span>{/each}
        <span class="live" style="left:{vm.bar.fill * 100}%"></span>
      </div>
    {/if}
    <!-- TEMP debug: raw % progression. Remove when validated. -->
    {#if dbgPct != null}<div class="dbg">{dbgPct}%</div>{/if}
  </div>
</div>

<style>
  .tt { --pc: #888; position: relative; height: 100%; min-height: 146px; background: var(--panel);
        overflow: hidden; }
  .tt.off { background: var(--well); }
  .spine { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--pc); }
  .tt.off .spine { background: var(--idle); }
  .fig { position: absolute; left: 6px; bottom: 0; top: 11px; width: 33%; background-repeat: no-repeat;
         background-position: bottom center; background-size: auto 100%; }
  .tt.off .fig { filter: grayscale(1) brightness(.6); }
  .data { position: absolute; left: 39%; right: 0; top: 0; bottom: 0; padding: 9px 9px 8px;
          display: flex; flex-direction: column; }
  .nm { font-size: 12px; font-weight: 700; color: var(--pc); letter-spacing: .05em; text-transform: uppercase;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tt.off .nm { color: var(--tx-mut); }
  .sel { margin-top: 6px; }
  .kv { font-size: 10px; color: var(--tx); display: flex; gap: 7px; line-height: 1.45; }
  .kv span { color: var(--tx-dim); font-size: 7.5px; letter-spacing: .08em; width: 18px; flex: 0 0 auto; padding-top: 2px; }
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
  .prim.time.fin { color: #cfe0f2; }
  .prim.act { font-size: 11.5px; font-weight: 600; color: var(--tx-mut); margin-top: 4px; }
  .prim.seen { font-size: 10.5px; color: var(--tx-dim); margin-top: 4px; }
  .barwrap { position: relative; margin-top: 7px; }
  .bar { height: 4px; background: var(--track); overflow: hidden; border-radius: 1px; }
  .bar > i { display: block; height: 100%; background: var(--pc); }
  .tick { position: absolute; top: 0; width: 1.5px; height: 4px; margin-left: -0.75px; background: var(--panel);
          box-shadow: 0 0 0 0.5px rgba(0,0,0,.35); }
  .live { position: absolute; top: 2px; width: 7px; height: 7px; margin-left: -3.5px; border-radius: 50%;
          background: var(--pc); transform: translateY(-50%); box-shadow: 0 0 0 1.5px var(--panel); }
  .live::after { content: ""; position: absolute; inset: 0; border-radius: 50%; background: var(--pc);
                 animation: ppulse 1.7s ease-out infinite; }
  @keyframes ppulse { 0% { transform: scale(1); opacity: .55; } 100% { transform: scale(2.6); opacity: 0; } }
  /* TEMP debug: raw % progression. Remove when validated. */
  .dbg { margin-top: 3px; font-family: ui-monospace, "Cascadia Code", monospace; font-size: 9px;
         font-variant-numeric: tabular-nums; color: #ffd23f; letter-spacing: .03em; }
</style>
