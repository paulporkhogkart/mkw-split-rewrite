<script>
  // LiveCard — the locked "KART-OFF print" live card, transcribed from the pixel-truth
  // mockups docs/design/site-redesign/live-card.html (LOCKED 2026-07-05) and
  // fire-live-card.html (fire r31). DOM/CSS values are verbatim from those files
  // (minus the mockup page chrome and the .kchip img ring filter — the ink ring is
  // baked inside ChipCanvas). Data wiring mirrors PlayerCard.svelte exactly.
  // No Tauri imports: site-adoptable.
  import { viewModel } from "../lib/playerCard.js";
  import { sampleAt, deltaTrendAt, DELAY_MS } from "../lib/raceTimerBuffer.js";
  import { deltaMode } from "../lib/cardSettings.js";
  import { updateFire } from "../lib/fireState.js";
  import { figureFor, onpaceFigure } from "../lib/playerFigures.js";
  import { digitSpans, zigzag, sessTags } from "../lib/liveCard.js";
  import { directorStep } from "../lib/chipDirector.js";
  import { silUrl } from "../lib/chipStream.js";
  import ChipCanvas from "./ChipCanvas.svelte";
  import { onDestroy } from "svelte";

  export let entry;
  export let now = Date.now();
  export let stale = false;
  export let manifest = null;      // shared chip-pack manifest, loaded by the panel
  export let bitmapCache = null;   // shared ImageBitmap LRU, owned by the panel

  // ── identical data wiring to PlayerCard.svelte (delay buffer, fire, forceFire) ──
  $: isRacing = !stale && !!entry && entry.online !== false && entry.screen === "RACING" && !entry.final_time;
  $: delayed = isRacing ? sampleAt(entry.player_id, now - DELAY_MS) : null;
  $: trend = isRacing && $deltaMode === "pace" ? deltaTrendAt(entry.player_id, now - DELAY_MS) : null;
  $: vm = viewModel(entry, now, delayed, { deltaMode: $deltaMode, trend, stale });
  $: forceFire = vm.state === "finished" && vm.finPb;
  $: aheadNow = isRacing && ($deltaMode === "laps"
      ? !!(entry.lap_delta && entry.lap_delta.delta_ms != null && entry.lap_delta.delta_ms < 0)
      : !!(delayed && delayed.pb_delta_ms != null && delayed.pb_delta_ms < 0));
  $: onFire = entry ? updateFire(entry.player_id, { ahead: aheadNow, racing: isRacing, now, mode: $deltaMode }) : false;
  $: fireOn = onFire || forceFire;
  $: fig = fireOn ? (onpaceFigure(vm.name) || figureFor(vm.name, true)) : figureFor(vm.name, vm.online);

  // ── chip choreography (prev-tracking: compute step, bump seq, THEN store prev) ──
  let prevEntry = null, actionSeq = 0, chip = { combo: null, action: null };
  $: {
    const step = directorStep(prevEntry, entry);
    if (step.action) { actionSeq += 1; chip = step; }
    else chip = { ...chip, combo: step.combo };
    prevEntry = entry;
  }
  $: selecting = ["CHARACTER_SELECT", "KART_SELECT", "COURSE_SELECT"].includes(entry?.screen);
  $: performing = isRacing || selecting;

  // tear mask k-cycle: performing cards step k0..3 @300ms, settled hold k0 (locked rule)
  let tearK = 0;
  const tearT = setInterval(() => (tearK = (tearK + 1) % 4), 300);
  // fire frame cycle @125ms (locked fire r31)
  let fireK = 0;
  const fireT = setInterval(() => (fireK = (fireK + 1) % 3), 125);
  onDestroy(() => { clearInterval(tearT); clearInterval(fireT); });
  $: k = performing ? tearK : 0;
  $: tearAnim = chip.action === "select" ? "spawn" : chip.action === "confirm" ? "flourish" : "idle";
  // A combo may not ship the mapped animation (e.g. no spawn): fall back like the
  // chip player does (spawn -> idle -> first available), so the sil URL always exists.
  $: tearAnimEff = (() => {
    const anims = manifest?.combos?.[chip.combo]?.anims;
    if (!anims || anims[tearAnim]) return tearAnim;
    return anims.idle ? "idle" : Object.keys(anims)[0];
  })();
  $: sess = sessTags(vm.activity, typeof now === "number" ? now : Date.now());
  $: zz = vm.bar ? zigzag(entry?.tot_lap ?? 3, vm.bar.fill) : null;

  // ── state routing (the five locked states + selection) ──
  // dnf/invalidated aren't in the locked line-up: render them like a settled (missed)
  // finish — janked "DNF"/"INVALID" text, no wave (noted simplification).
  $: mode = vm.state === "offline" ? "offline"
    : selecting && vm.online ? "selecting"
    : (vm.state === "racing" || vm.state === "held") ? "racing"
    : (vm.state === "finished" || vm.state === "dnf" || vm.state === "invalidated") ? "finished"
    : "idle";

  // Mockup delta tags are ok/bad/gold; fire forces gold ("gold delta marks the pace").
  $: deltaCls = !vm.delta ? null
    : fireOn ? "gold"
    : vm.delta.cls === "gold" ? "gold"
    : vm.delta.cls.startsWith("ahead") ? "ok" : "bad";

  $: courseLine = mode === "selecting"
      ? (vm.trk ?? (entry.screen === "KART_SELECT" ? "Choosing kart…"
          : entry.screen === "CHARACTER_SELECT" ? "Choosing character…" : "Choosing course…"))
    : (mode === "offline" || mode === "idle") ? vm.primary.text
    : (vm.trk ?? "");

  $: spans = mode === "finished" ? digitSpans(vm.primary.text) : [];

  // Chips + tear plies render only with a combo AND a manifest, on online cards
  // (the mockup idle/offline cards carry no chip).
  $: showChip = !!(vm.online && chip.combo && manifest);
  $: tearUrl = showChip ? silUrl(manifest, chip.combo, tearAnimEff, k) : null;

  // Card tilt: the mockup hand-assigns per-card --tilt from this set; derive stably
  // from player_id so a card keeps its lean across renders.
  const TILTS = [-1.3, 1.2, -1, 1.4, -1.2];
  function tiltIdx(id) {
    const n = +id;
    if (Number.isFinite(n)) return Math.abs(Math.trunc(n)) % 5;
    let h = 0;
    for (const c of String(id ?? "")) h += c.charCodeAt(0);
    return h % 5;
  }
  $: tilt = TILTS[tiltIdx(entry?.player_id)];

  // Per-instance unique fire path ids (three cards on fire at once must not collide).
  const uid = Math.random().toString(36).slice(2, 8);
  const FIRE_FRAMES = ["tA", "tB", "tC"];
</script>

<div class="cardScale" style="--pc:{vm.color};--tilt:{tilt}deg">
  <div class="card" class:mutd={mode === "idle"} class:offl={mode === "offline"} class:onfire={fireOn}>
    <div class="bd"></div><div class="face"></div>
    {#if fireOn}
      <!-- fire r31 defs + frames (fire-live-card.html) — body var(--c), core 45% white mix -->
      <svg width="0" height="0" style="position:absolute" aria-hidden="true">
        <defs>
          <path id="tA_body_{uid}" d="M 14 126 L 6 108 L 12 91 L 7 74 L 12 43 L 20 55 L 27 69 L 33 35 L 39 16 L 45 36 L 52 57 L 60 44 L 66 55 L 80 37 L 78 58 L 86 73 L 82 93 L 90 110 L 84 126 L 72 120 L 59 127 L 45 121 L 31 127 L 22 121 Z"/>
          <path id="tA_core_{uid}" d="M 23 120 L 17 112 L 21 100 L 18 88 L 21 64 L 27 74 L 32 84 L 37 59 L 41 45 L 46 60 L 51 75 L 59 69 L 72 60 L 70 75 L 76 87 L 73 101 L 79 114 L 74 118 L 62 115 L 50 121 L 37 116 L 29 121 Z"/>
          <path id="tB_body_{uid}" d="M 14 126 L 7 105 L 13 89 L 6 69 L 10 35 L 19 51 L 27 67 L 32 39 L 38 23 L 44 42 L 51 59 L 59 47 L 65 57 L 78 45 L 77 63 L 84 76 L 80 95 L 88 110 L 83 126 L 70 121 L 56 127 L 43 120 L 29 126 L 21 121 Z"/>
          <path id="tB_core_{uid}" d="M 23 120 L 18 111 L 22 99 L 17 84 L 20 59 L 26 71 L 32 82 L 36 61 L 41 50 L 45 63 L 50 76 L 58 71 L 70 67 L 69 80 L 75 89 L 72 103 L 78 114 L 73 117 L 60 115 L 48 120 L 36 115 L 28 120 Z"/>
          <path id="tC_body_{uid}" d="M 14 126 L 6 110 L 13 93 L 8 76 L 11 49 L 19 61 L 26 71 L 32 37 L 37 19 L 43 39 L 50 58 L 58 44 L 64 56 L 82 31 L 79 55 L 87 71 L 83 91 L 89 108 L 84 126 L 73 120 L 60 127 L 46 120 L 32 127 L 21 121 Z"/>
          <path id="tC_core_{uid}" d="M 23 120 L 17 114 L 22 101 L 18 89 L 21 69 L 26 78 L 32 85 L 36 60 L 40 47 L 44 61 L 50 75 L 58 70 L 73 56 L 71 73 L 77 85 L 74 100 L 78 112 L 74 117 L 61 115 L 47 118 L 34 116 L 28 121 Z"/>
        </defs>
      </svg>
      <div class="firewin"><div class="tfx">
        {#each FIRE_FRAMES as f, i}
          <svg class="fr" class:on={fireK === i} viewBox="0 0 100 142" width="178" height="253">
            <use href="#{f}_body_{uid}" style="fill:#101114" transform="translate(52,66) scale(1.13) translate(-49,-62) translate(3,3)"/>
            <use href="#{f}_body_{uid}" style="fill:var(--c)"/>
            <use href="#{f}_core_{uid}" style="fill:color-mix(in srgb, var(--c) 45%, #fff)"/>
          </svg>
        {/each}
      </div></div>
    {/if}
    {#if fig}<div class="figmask"><img src={fig} alt=""></div>{/if}
    {#if fireOn}
      <div class="embwin">
        <span class="deb" style="right:122px;top:119px;width:5px;height:4px;--dur:1.5s;--sw:7px"></span>
        <span class="deb dk" style="right:96px;top:121px;width:4px;height:3px;--dur:1.8s;--dl:.5s;--sw:-6px"></span>
        <span class="deb" style="right:74px;top:119px;width:4px;height:3px;--dur:1.3s;--dl:.8s;--sw:4px"></span>
        <span class="deb dk" style="right:51px;top:121px;width:3px;height:3px;--dur:1.6s;--dl:1.1s;--sw:5px"></span>
        <span class="deb" style="right:30px;top:119px;width:3px;height:3px;--dur:1.45s;--dl:.3s;--sw:-4px"></span>
        <span class="deb" style="right:10px;top:121px;width:4px;height:3px;--dur:1.7s;--dl:.9s;--sw:5px"></span>
      </div>
    {/if}
    {#if showChip}
      <div class="tearW ply2" class:bigT={selecting} class:front={selecting}>
        <div class="tearS" style="-webkit-mask-image:url({tearUrl});mask-image:url({tearUrl})"></div>
      </div>
      <div class="tearW plyC" class:bigT={selecting} class:front={selecting}>
        <div class="tearS" style="-webkit-mask-image:url({tearUrl});mask-image:url({tearUrl})"></div>
      </div>
      <div class="kchip" class:big={selecting} class:front={selecting}>
        <ChipCanvas {manifest} {bitmapCache} combo={chip.combo} action={chip.action} {actionSeq}
          height={selecting ? 112 : 92} />
      </div>
    {/if}
    <div class="in">
      <span class="ntag">{vm.name}</span>
      <div class="course">{courseLine}</div>
      {#if mode === "racing"}
        <div class="ssw"><div class="timer">{vm.primary.text}</div></div>
      {:else if mode === "finished"}
        <div class="ssw"><div class="timer" class:wave={vm.finPb}>{#each spans as s}<span class="d" style="--wd:{s.wd}s;--tj:{s.tj}">{s.ch}</span>{/each}</div></div>
      {/if}
      {#if (mode === "racing" || mode === "finished") && vm.activity}
        <div class="sess">
          {#if sess.att != null}<span class="mt"><b>{sess.att}</b> ATT</span>{/if}
          {#if sess.racing}<span class="mt"><b>{sess.racing}</b> RACING</span>{/if}
        </div>
      {/if}
      {#if mode === "selecting"}
        <div class="stags">
          {#if vm.char}<span class="ntag">{vm.char.toUpperCase()}</span>{/if}
          {#if vm.kart}<span class="ntag k2">{vm.kart.toUpperCase()}</span>{/if}
        </div>
      {/if}
      {#if (mode === "idle" || mode === "offline") && vm.stats}
        <div class="stats3">
          {#if vm.stats.firsts != null}<div class="ms"><div class="n">{vm.stats.firsts}</div><div class="k">FIRSTS</div></div>{/if}
          {#if vm.stats.runs_7d != null}<div class="ms"><div class="n">{vm.stats.runs_7d}</div><div class="k">RUNS·7D</div></div>{/if}
          {#if vm.stats.pbs_30d != null}<div class="ms"><div class="n">{vm.stats.pbs_30d}</div><div class="k">PBS·30D</div></div>{/if}
        </div>
      {/if}
      {#if (mode === "racing" || mode === "finished") && vm.pbStr}
        <div class="pbrow"><span class="k">PB</span><span class="v">{vm.pbStr}</span>{#if vm.delta}<span class="delta {deltaCls}">{vm.delta.text}</span>{/if}</div>
      {/if}
      {#if (mode === "racing" || mode === "finished") && zz}
        <div class="twell"><svg width="128" height="16" viewBox="0 0 128 16">
          {#each zz.done as d}<path class="run" d={d}/>{/each}
          {#if zz.current}
            <path class="ghost" d={zz.current.d}/>
            <path class="run" d={zz.current.d} pathLength="100" stroke-dasharray="100" stroke-dashoffset={zz.current.offset}/>
          {/if}
          {#each zz.future as d}<path class="ghost" d={d}/>{/each}
        </svg></div>
      {/if}
    </div>
  </div>
</div>

<style>
  /* Inter is not otherwise bundled in the desktop app: ship the three weights the
     print language uses (copied from web/public/fonts, Vite bundles the woff2s). */
  @font-face{font-family:Inter;src:url("../assets/fonts/inter-700.woff2") format("woff2");font-weight:700}
  @font-face{font-family:Inter;src:url("../assets/fonts/inter-800.woff2") format("woff2");font-weight:800}
  @font-face{font-family:Inter;src:url("../assets/fonts/inter-900.woff2") format("woff2");font-weight:900}

  /* Wrapper: 250x150 design space, parent scales via --s (B8 wires measurement). Carries
     the mockup :root palette + body typography the card rules assume. */
  .cardScale{
    --ink:#101114;--ink-2:#191a1d;--paper:#f3f4f6;--mut:#9a9ca1;--dim:#6b6d73;
    --ok:#30c161;--bad:#e5484d;--gold:#e3b341;
    position:relative;width:250px;height:150px;
    transform:scale(var(--s,1));transform-origin:top left;
    font-family:Inter,system-ui,sans-serif;font-size:13px;font-weight:700;color:var(--paper);
  }
  .cardScale *{box-sizing:border-box;margin:0}

  /* ===== transcribed verbatim from live-card.html:54-137 (page chrome dropped) ===== */
  .card{position:relative;width:250px;height:150px;transform:rotate(var(--tilt,-1.3deg));--c:var(--pc)}
  .card .bd,.card .face{position:absolute;inset:0;
       clip-path:polygon(2% 3%,32% 0,68% 4%,100% 1%,97% 48%,100% 86%,66% 97%,38% 88%,12% 99%,0 80%,3% 42%)}
  .card .bd{background:var(--c);opacity:.92;transform:translate(6px,5px)}
  .card .face{background:var(--ink-2)}
  .card .face::after{content:"";position:absolute;inset:0;opacity:.14;pointer-events:none;
       background-image:radial-gradient(circle,var(--c) 1px,transparent 1.5px);background-size:7px 7px}
  .figmask{position:absolute;inset:0;z-index:3;pointer-events:none;
       clip-path:polygon(0 -40%,100% -40%,97% 48%,100% 86%,66% 97%,38% 88%,12% 99%,0 80%,3% 42%)}
  .figmask img{position:absolute;bottom:-4px;right:var(--fx,-6px);height:140px;
       filter:drop-shadow(-1px 3px 4px rgba(0,0,0,.45))}

  /* TEAROUT v3: two-ply boxy paper cut; masks = sil k0..3 (performing cycles, settled k0) */
  .tearW{position:absolute;z-index:4;pointer-events:none;
       right:var(--kx,40px);bottom:0;height:92px;aspect-ratio:494/540}
  .tearW.ply2{transform:translate(2px,3px) scale(1.055) skewX(-1.5deg) rotate(-1deg);
       animation:jitP 1.3s steps(1,end) infinite}
  .tearW.plyC{transform:translate(-2px,2px) rotate(.8deg);animation:jitC 1.3s steps(1,end) infinite}
  .ply2 .tearS{background:#101114}
  @keyframes jitP{0%,100%{transform:translate(2px,3px) scale(1.055) skewX(-1.5deg) rotate(-1deg)}
    50%{transform:translate(3px,3px) scale(1.05) skewX(-1deg) rotate(-.6deg)}}
  @keyframes jitC{0%,100%{transform:translate(-2px,2px) rotate(.8deg)}50%{transform:translate(-1px,3px) rotate(.3deg)}}
  .tearS{position:absolute;inset:0;background:var(--c);
       -webkit-mask-size:100% 100%;-webkit-mask-repeat:no-repeat;
       mask-size:100% 100%;mask-repeat:no-repeat}
  .tearW.bigT{height:112px;right:var(--kx,44px)}
  /* .kchip: positional rules only — the img drop-shadow ring filter is NOT copied,
     the 1px ink cel ring is baked in ChipCanvas (spec). */
  .kchip{position:absolute;right:var(--kx,40px);bottom:0;z-index:5;height:92px;pointer-events:none}
  .kchip.big{height:112px;right:var(--kx,44px)}

  .in{position:absolute;inset:0;padding:11px 12px;pointer-events:none}
  .in > *{position:relative;z-index:7}
  .in > .pbrow,.in > .twell{position:absolute}
  .ntag{display:inline-block;background:var(--c);color:var(--ink);font-weight:900;font-style:italic;
       padding:2px 7px 3px;font-size:11px;box-shadow:1.5px 1.5px 0 rgba(0,0,0,.45);white-space:nowrap}
  .course{margin-top:5px;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut)}
  .ssw{position:relative;height:25px;margin-top:8px}
  .ssw .timer{position:absolute;left:0;top:0;transform:scale(.5);transform-origin:top left;
       font-weight:900;font-style:italic;font-size:50px;line-height:1;letter-spacing:-.02em;
       color:#fff;-webkit-text-stroke:3.6px var(--ink);paint-order:stroke fill;
       text-shadow:5px 5px 0 var(--c),2px 4px 0 rgba(0,0,0,.5);font-variant-numeric:tabular-nums;white-space:nowrap}
  .timer .d{display:inline-block;transform:var(--tj,none)}
  @keyframes wavehop{0%,100%{transform:var(--tj,none)}30%{transform:translateY(-14px) var(--tj,none)}}
  .wave .d{animation:wavehop .62s cubic-bezier(.3,1.6,.4,1) var(--wd,0s) 3}
  .sess{margin-top:6px;display:flex;gap:5px}
  .mt{background:var(--ink);color:#cfd2d7;font-size:8.5px;letter-spacing:.1em;padding:2px 6px 3px;
      box-shadow:0 0 0 1.5px rgba(255,255,255,.10);font-variant-numeric:tabular-nums}
  .mt b{color:var(--paper);font-style:italic}
  .pbrow{position:absolute;left:12px;bottom:32px;display:flex;gap:6px;align-items:center;font-size:10px}
  .pbrow .k{color:var(--dim);letter-spacing:.1em}
  .pbrow .v{color:#cfd2d7;font-variant-numeric:tabular-nums}
  .delta{display:inline-block;color:var(--ink);font-weight:900;font-style:italic;font-size:9.5px;
       padding:1px 5px 2px;box-shadow:1px 1px 0 rgba(0,0,0,.45)}
  .delta.ok{background:var(--ok)} .delta.bad{background:var(--bad)} .delta.gold{background:var(--gold)}

  .twell{position:absolute;left:12px;bottom:10px;background:var(--ink);z-index:3;
       box-shadow:0 0 0 1.5px rgba(255,255,255,.14),2px 2px 0 rgba(0,0,0,.4);
       padding:3px 5px;transform:rotate(-1deg)}
  .twell svg{display:block}
  .twell .ghost{stroke:#3a3c42;stroke-width:3.2;fill:none;stroke-linecap:butt;stroke-linejoin:miter}
  .twell .run{stroke:var(--c);stroke-width:3.2;fill:none;stroke-linecap:butt;stroke-linejoin:miter}

  .card.mutd{filter:saturate(.32) brightness(.72)}
  .card.offl{filter:saturate(.12) brightness(.55)}
  .card.offl .figmask img{filter:grayscale(1) brightness(.8) drop-shadow(-1px 3px 4px rgba(0,0,0,.45))}
  .stats3{margin-top:10px;display:grid;grid-template-columns:repeat(2,66px);gap:6px}
  .ms{background:var(--ink);padding:4px 7px 5px;box-shadow:0 0 0 1.5px rgba(255,255,255,.10)}
  .ms .n{font-weight:900;font-style:italic;font-size:15px;color:#e6e8ec}
  .ms .k{font-size:7.5px;letter-spacing:.12em;color:var(--dim);margin-top:2px}

  /* SELECTION (S-C): stacked character + kart tags; chip + tear ride ABOVE the tags */
  .stags{margin-top:10px;display:flex;flex-direction:column;align-items:flex-start;gap:5px}
  .stags .ntag{font-size:14px}
  .stags .ntag.k2{transform:rotate(1.4deg) translateX(7px);background:var(--paper)}
  .tearW.front{z-index:8}
  .kchip.front{z-index:9}

  /* ===== FIRE r31 (fire-live-card.html:45-60) ===== */
  .firewin,.embwin{position:absolute;inset:0;pointer-events:none;
       clip-path:polygon(0 -70%,100% -70%,97% 48%,100% 86%,66% 97%,38% 88%,12% 99%,0 80%,3% 42%)}
  .firewin{z-index:2}
  .embwin{z-index:4}
  .tfx{position:absolute;right:-32px;bottom:-26px;width:178px}
  .tfx .fr{display:none}
  .tfx .fr.on{display:block}
  .onfire .figmask img{filter:drop-shadow(1px 0 0 #101114) drop-shadow(-1px 0 0 #101114)
              drop-shadow(0 1px 0 #101114) drop-shadow(0 -1px 0 #101114)}
  .deb{position:absolute;border-radius:2px;background:var(--c);
       animation:dfloat var(--dur,1.5s) linear var(--dl,0s) infinite}
  .deb.dk{background:color-mix(in srgb,var(--c) 50%,#000)}
  @keyframes dfloat{0%{opacity:0;transform:translate(0,7px) rotate(0)}10%{opacity:1}
    50%{transform:translate(var(--sw,4px),-35px) rotate(14deg)}90%{opacity:1}
    100%{opacity:0;transform:translate(calc(var(--sw,4px) * -.6),-73px) rotate(32deg)}}
</style>
