<!-- web/src/CoursesIndex.svelte -->
<script>
  import { onMount, onDestroy } from "svelte";
  import { territoryUrl, territoryTimelineUrl, API_BASE } from "./lib/api.js";
  import { leaderboardAt, wrAsOf } from "./lib/timeline.js";
  import { buildCourseView, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
  import { overallBoard } from "./lib/overallBoard.js";
  import CoursePopup from "./CoursePopup.svelte";

  let cards = [];        // [{ slug, name, view, figUrl }]
  let overall = [];      // [{ player, total_ms, tracks, rank }]
  let query = "";
  let error = null;
  let mintedUrls = [];   // blob: object URLs minted for card figures, revoked on destroy

  const fmt = (ms) => { if (ms == null) return "—"; const s = ms/1000, m = Math.floor(s/60); return `${m}:${(s-m*60<10?"0":"")}${(s-m*60).toFixed(3)}`; };

  const fetchJson = async (url) => {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url} -> ${r.status}`);
    return r.json();
  };

  onMount(async () => {
    try {
      const [courses, tl] = await Promise.all([fetchJson(territoryUrl()), fetchJson(territoryTimelineUrl())]);
      const { events, colors, wrHistory } = tl;
      const ordered = [...courses].sort((a, b) => a.course_id - b.course_id);
      const boards = ordered.map((c) => ({ slug: c.slug, name: c.display_name, standings: leaderboardAt(events, c.slug, Infinity) }));
      cards = boards.map((b) => ({
        slug: b.slug, name: b.name,
        view: buildCourseView({ standings: b.standings, colorByName: colors, courseName: b.name, wr: wrAsOf(wrHistory, b.slug, Infinity) }),
        figUrl: "",
      }));
      overall = overallBoard(boards).map((o, i) => ({ ...o, rank: i + 1, color: colors[o.player] || "#888" }));
      await preloadPlayerGifs(API_BASE);
      cards = cards.map((c) => {
        const figUrl = c.view.gifUrl ? freshGifUrl(c.view.gifUrl) : "";
        if (figUrl.startsWith("blob:")) mintedUrls.push(figUrl);
        return { ...c, figUrl };
      });
    } catch (e) { error = String(e); }
  });

  onDestroy(() => { for (const u of mintedUrls) URL.revokeObjectURL(u); });

  $: filtered = cards.filter((c) => c.name.toLowerCase().includes(query.trim().toLowerCase()));
</script>

<section class="page">
  {#if error}<p class="err">Couldn't load tracks: {error}</p>{/if}

  <div class="overall">
    <div class="head">Overall — Total Time</div>
    {#each overall as o (o.player)}
      <div class="orow"><span class="bar" style="background:{o.color}"></span><span class="rk">{o.rank}</span><span class="nm">{o.player}</span><span class="tm">{fmt(o.total_ms)}</span><span class="tk">{o.tracks} tracks</span></div>
    {/each}
  </div>

  <input class="search" placeholder="Search tracks…" bind:value={query} />

  <div class="grid">
    {#each filtered as c (c.slug)}
      <a class="card" href={`/tracks/${c.slug}`}><CoursePopup view={c.view} figUrl={c.figUrl} /></a>
    {/each}
  </div>
</section>

<style>
  .page { padding: 16px; color: #e8eaed; }
  .err { color: #f77; }
  .overall { max-width: 420px; margin: 0 0 16px; background:#121419; border:1px solid #2a2d33; border-radius:6px; padding:10px 12px; }
  .overall .head { font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#5f656e; margin-bottom:6px; }
  .orow { display:flex; align-items:center; gap:10px; padding:2px 0; border-top:1px solid #1c1f24; }
  .orow .bar { flex:0 0 3px; width:3px; height:14px; border-radius:2px; }
  .orow .rk { flex:0 0 16px; text-align:right; color:#6f7782; font-variant-numeric:tabular-nums; }
  .orow .nm { flex:1 1 auto; }
  .orow .tm, .orow .tk { font-variant-numeric:tabular-nums; color:#9aa3ad; }
  .orow .tk { flex:0 0 auto; font-size:11px; }
  .search { width:100%; max-width:420px; margin-bottom:16px; padding:8px 10px; background:#0e1014; border:1px solid #2a2d33; border-radius:6px; color:#e8eaed; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(344px,1fr)); gap:14px; }
  .card { display:block; text-decoration:none; }
</style>
