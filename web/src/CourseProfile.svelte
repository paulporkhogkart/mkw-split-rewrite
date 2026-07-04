<!-- web/src/CourseProfile.svelte -->
<script>
  import { courseSummaryUrl } from "./lib/api.js";
  import { fmtTime } from "./lib/activityFormat.js";
  import { fireTargetMs } from "./lib/fireTarget.js";
  import { withTheoretical } from "./lib/courseSplits.js";
  import { isOnFire } from "./lib/fireModel.js";
  import { chipUrl, slugify } from "./lib/chips.js";

  export let slug;

  let s = null;      // the summary
  let error = null;

  $: load(slug);
  async function load(sl) {
    s = null; error = null;
    try {
      const res = await fetch(courseSummaryUrl(sl));
      if (res.status === 404) { error = "No such track."; return; }
      if (!res.ok) { error = "Couldn't load this track."; return; }
      s = await res.json();
    } catch (e) { error = String(e); }
  }

  const date = (t) => (t == null ? "—" : new Date(t).toISOString().slice(0, 10));
  const days = (ms) => (ms == null ? "current" : `${Math.max(1, Math.round(ms / 86400000))}d`);
  const pctOffWr = (ms, wr) => (wr == null || ms == null ? "—" : `${(((ms - wr) / wr) * 100).toFixed(2)}%`);

  $: wrMs = s?.wr?.record_ms ?? null;
  $: leaderMs = s?.leaderboard?.[0]?.total_time_ms ?? null;
  $: lit = s ? isOnFire({ t1: leaderMs, t2: s.leaderboard?.[1]?.total_time_ms ?? null, wr: wrMs }) : false;
  $: fire = s ? fireTargetMs({ leaderMs, wr: wrMs }) : { ms: null, reason: "no-wr" };
  $: splits = s ? withTheoretical(s.splits) : null;
</script>

{#if error}
  <section class="wrap"><p class="err">{error}</p></section>
{:else if !s}
  <section class="wrap"><p>Loading…</p></section>
{:else}
<section class="wrap">
  <header class="head">
    <h1>{s.profile.display_name}</h1>
    {#if s.wr}
      <div class="wr">
        <span class="lbl">WR</span>
        <span class="tm">{fmtTime(s.wr.record_ms)}</span>
        <span class="holder">{s.wr.holder_name ?? "—"}</span>
        {#if s.wr.character}<img class="chip" src={chipUrl("combos", `${slugify(s.wr.character)}__base`)} alt="" on:error={(e) => (e.target.style.display = "none")} /><span class="lo">{s.wr.character}</span>{/if}
        {#if s.wr.vehicle}<img class="chip" src={chipUrl("karts", slugify(s.wr.vehicle))} alt="" on:error={(e) => (e.target.style.display = "none")} /><span class="lo">{s.wr.vehicle}</span>{/if}
        {#if s.wr.video_url}<a class="vid" href={s.wr.video_url} target="_blank" rel="noopener">video ↗</a>{/if}
      </div>
    {/if}
  </header>

  <!-- On-fire target -->
  <div class="fireline">
    {#if lit}🔥 This track is on fire — {s.leaderboard[0].display_name} leads by enough to burn.
    {:else if fire.ms != null}🔥 Run <b>{fmtTime(fire.ms)}</b> or faster to seize #1 and light this track.
    {:else if fire.reason === "wr-tight"}The leader is too close to the WR to be out-lit.
    {:else}Needs a WR and a second time before a track can catch fire.{/if}
  </div>

  <!-- Leaderboard -->
  <table class="board">
    <thead><tr><th>#</th><th>Player</th><th>Time</th><th>Gap</th><th>Δ WR</th></tr></thead>
    <tbody>
      {#each s.leaderboard as r (r.player_id)}
        <tr>
          <td class="num">{r.rank}</td>
          <td><span class="dot" style="background:{r.color || '#888'}"></span>{r.display_name}</td>
          <td class="num">{fmtTime(r.total_time_ms)}</td>
          <td class="num">{r.rank === 1 ? "—" : "+" + ((r.total_time_ms - leaderMs) / 1000).toFixed(3)}</td>
          <td class="num">{pctOffWr(r.total_time_ms, wrMs)}</td>
        </tr>
      {/each}
    </tbody>
  </table>

  <!-- Lap splits -->
  {#if splits && splits.laps > 0}
    <h2>Lap splits</h2>
    <table class="splits">
      <thead><tr><th>Player</th>{#each Array(splits.laps) as _, i}<th>Lap {i + 1}</th>{/each}<th>Theoretical</th></tr></thead>
      <tbody>
        {#each splits.perPlayer as p (p.player_id)}
          <tr>
            <td><span class="dot" style="background:{p.color || '#888'}"></span>{p.display_name}</td>
            {#each p.best as b}<td class="num">{b == null ? "—" : fmtTime(b)}</td>{/each}
            <td class="num strong">{p.theoretical == null ? "—" : fmtTime(p.theoretical)}</td>
          </tr>
        {/each}
        <tr class="ideal">
          <td>Field ideal</td>
          {#each splits.fieldIdeal as b}<td class="num">{b == null ? "—" : fmtTime(b)}</td>{/each}
          <td class="num strong">{splits.fieldIdealTotal == null ? "—" : fmtTime(splits.fieldIdealTotal)}</td>
        </tr>
      </tbody>
    </table>
  {/if}

  <!-- History -->
  <div class="hist">
    <div class="col">
      <h2>Record progression</h2>
      {#each s.history.recordProgression as e}<div class="hrow"><span>{date(e.t)}</span><span>{e.player}</span><span class="num">{fmtTime(e.ms)}</span></div>{/each}
    </div>
    <div class="col">
      <h2>#1 reigns</h2>
      {#each s.history.reigns as r}<div class="hrow"><span>{r.player}</span><span>{date(r.from)} → {r.to == null ? "now" : date(r.to)}</span><span>{days(r.ms)}</span></div>{/each}
    </div>
    <div class="col">
      <h2>World record history</h2>
      {#each s.history.wrHistory as w}<div class="hrow"><span>{date(w.t)}</span><span>{w.holder_name ?? "—"}</span><span class="num">{fmtTime(w.record_ms)}</span>{#if w.video_url}<a href={w.video_url} target="_blank" rel="noopener">↗</a>{/if}</div>{/each}
    </div>
  </div>
</section>
{/if}

<style>
  .wrap { padding: 16px; color: #e8eaed; max-width: 960px; }
  .err { color: #f77; }
  .head h1 { margin: 0 0 6px; }
  .wr { display: flex; align-items: center; gap: 8px; color: #9aa3ad; font-variant-numeric: tabular-nums; flex-wrap: wrap; }
  .wr .lbl { font-size: 10px; letter-spacing: .08em; color: #5f656e; }
  .wr .tm { color: #e8eaed; } .wr .chip { height: 20px; width: auto; } .wr .lo { font-size: 12px; }
  .wr .vid { color: #5f9bd6; text-decoration: none; }
  .fireline { margin: 12px 0; padding: 8px 10px; background: #17140e; border: 1px solid #3a2f18; border-radius: 6px; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid #1c1f24; font-size: 13px; }
  th { color: #5f656e; font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
  .num { text-align: right; font-family: ui-monospace, Menlo, monospace; }
  .strong { color: #fff; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 7px; vertical-align: middle; }
  .ideal td { color: #cdd3da; border-top: 1px solid #2c313a; }
  .hist { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }
  .hist h2 { font-size: 13px; color: #cdd3da; }
  .hrow { display: flex; gap: 10px; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #1c1f24; font-size: 12.5px; }
</style>
