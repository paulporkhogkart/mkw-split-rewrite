<script>
  // Hover-popup card for a course: a slim figure strip (the leader's GIF, with
  // Fire.svelte flames when on-fire) + the track leaderboard. Driven by a courseData
  // view-model; renders nothing until one is supplied.
  import Fire from "../../src/components/Fire.svelte";
  export let view = null;
  const fmt = (ms) => { if (ms == null) return "—"; const s = ms/1000, m = Math.floor(s/60); return `${m}:${(s-m*60 < 10 ? "0" : "")}${(s-m*60).toFixed(3)}`; };
  const gap = (ms) => (ms == null ? "-.---" : "+" + (ms/1000).toFixed(3));
</script>

{#if view}
<div class="card">
  <!-- strip: 3px spine + a 56px figure column at x[5..61]; Fire sits at that same
       column (its own left:5/width:56), behind (z1) + in front (z3) of the figure (z2). -->
  <div class="strip">
    <div class="spine" style="background:{view.leader?.color || '#888'}"></div>
    {#if view.onFire}<Fire color={view.leader?.color || '#888'} active={true} />{/if}
    <img class="fig" src={view.onFire ? view.fireGifUrl : view.gifUrl} alt="" draggable="false" />
  </div>
  <div class="lb">
    <div class="head"><span class="title">{view.name}</span>
      <span class="wr"><i>WR</i>{fmt(view.wr_ms)}</span></div>
    <div class="rule"></div>
    <div class="rows">
      <div class="hrow"><span class="bar"></span><span class="rk">#</span><span class="nm">Player</span><span class="tm">Time</span><span class="gp">Gap</span></div>
      {#each view.rows as r (r.rank)}
        <div class="row" class:lead={r.rank === 1}>
          <span class="bar" style="background:{r.color}"></span>
          <span class="rk">{r.rank}</span><span class="nm">{r.name}</span>
          <span class="tm">{r.time_str || fmt(r.time_ms)}</span>
          <span class="gp" class:none={r.rank === 1}>{gap(r.gap_ms)}</span>
        </div>
      {/each}
    </div>
  </div>
</div>
{/if}

<style>
  .card{ display:flex; width:344px; background:#121419; border:1px solid #2a2d33; border-radius:6px;
         box-shadow:0 18px 40px rgba(0,0,0,.6),0 0 0 1px rgba(61,124,194,.10); overflow:hidden; }
  .strip{ position:relative; width:64px; flex:0 0 64px; background:#0e1014; border-right:1px solid #23262b; overflow:hidden; }
  .spine{ position:absolute; left:0; top:0; bottom:0; width:3px; z-index:4; }
  /* height-locked, centred figure: it fills the strip height and crops at the sides
     (player-card style); transparent cut-out so the strip bg shows through. */
  .fig{ position:absolute; left:50%; bottom:0; transform:translateX(-50%); height:100%; width:auto; max-width:none; z-index:2; }
  .lb{ flex:1 1 auto; padding:10px 12px 7px; min-width:0; }
  .head{ display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
  .title{ color:#e8eaed; font-size:14px; font-weight:600; }
  .wr{ font-size:10.5px; color:#9aa3ad; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .wr i{ font-style:normal; color:#5f656e; letter-spacing:.08em; font-size:9px; margin-right:3px; }
  .rule{ height:1px; margin:8px 0 3px; background:linear-gradient(90deg,transparent,#2c313a 8%,#2c313a 92%,transparent); }
  .rows{ margin-top:2px; }
  .row{ display:flex; align-items:center; gap:11px; padding:3px 7px 3px 0; border-top:1px solid #1c1f24; }
  .hrow{ display:flex; align-items:center; gap:11px; padding:1px 7px 4px 0; }
  .bar{ flex:0 0 3px; width:3px; height:14px; border-radius:2px; }
  .rk{ flex:0 0 13px; text-align:right; font-size:12px; color:#6f7782; font-variant-numeric:tabular-nums; }
  .nm{ flex:1 1 auto; min-width:0; font-size:12.5px; color:#d4d8dd; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .tm{ flex:0 0 auto; font-family:ui-monospace,Menlo,monospace; font-size:12.5px; color:#d4d8dd; font-variant-numeric:tabular-nums; }
  .gp{ flex:0 0 58px; text-align:right; font-family:ui-monospace,Menlo,monospace; font-size:12.5px; color:#828791; font-variant-numeric:tabular-nums; }
  .gp.none{ color:#54595f; }
  .hrow .rk,.hrow .nm,.hrow .tm,.hrow .gp{ font-size:9.5px; letter-spacing:.08em; text-transform:uppercase; color:#5f656e; font-weight:500; }
  .row.lead{ background:rgba(255,255,255,.04); } .row.lead .nm,.row.lead .tm{ color:#fff; }
</style>
