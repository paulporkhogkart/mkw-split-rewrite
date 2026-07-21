<script>
  import { onMount, onDestroy } from "svelte";
  import { wrJobsUrl } from "./lib/api.js";
  import { STATUS_META, splitRows, summary, detailOf, relTime, parseUtc } from "./lib/wrJobs.js";

  let loaded = false, error = false;
  let current = [], superseded = [], sum = null;

  async function refresh() {
    try {
      const res = await fetch(wrJobsUrl(), { cache: "no-store" });
      if (!res.ok) throw new Error(`wr-jobs ${res.status}`);
      const payload = await res.json();
      const jobs = payload.jobs ?? [];
      ({ current, superseded } = splitRows(jobs));
      sum = summary(jobs);
      loaded = true;
      error = false;
    } catch (e) {
      console.error("wr-jobs load failed", e);
      if (!loaded) error = true;   // a failed poll keeps the last good table
    }
  }

  let timer;
  onMount(() => { refresh(); timer = setInterval(refresh, 30_000); });
  onDestroy(() => clearInterval(timer));

  const absTitle = (s) => parseUtc(s)?.toLocaleString() ?? "";
</script>

<section class="jobs">
  <h2>wr trail jobs</h2>
  {#if error}
    <p class="msg">Couldn't load job data.</p>
  {:else if !loaded}
    <p class="msg">Loading…</p>
  {:else}
    <p class="sum">
      <b class="ok">{sum.done}</b> done ·
      <b>{sum.queued}</b> queued ·
      <b class="warn" class:zero={sum.stuck === 0}>{sum.stuck}</b> stuck ·
      {sum.coverage} current WRs trailed
    </p>

    <table>
      <thead><tr>
        <th>Course</th><th>cc</th><th>Holder</th><th>Record</th>
        <th>Status</th><th class="num">Att</th><th>Detail</th><th>Updated</th>
      </tr></thead>
      <tbody>
        {#each current as j (j.wr_id)}
          <tr>
            <td>{j.course}</td>
            <td class="mono">{j.cc}</td>
            <td>{j.holder_name ?? "—"}</td>
            <td class="mono">{j.record_str}</td>
            <td><span class="dot" style="background:{STATUS_META[j.status]?.color ?? '#666'}"></span>{STATUS_META[j.status]?.label ?? j.status}</td>
            <td class="mono num">{j.attempts}</td>
            <td class="detail">{detailOf(j)}</td>
            <td class="seen" title={absTitle(j.updated_at)}>{relTime(j.updated_at)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if superseded.length}
      <h3>superseded — processed history</h3>
      <table>
        <thead><tr>
          <th>Course</th><th>cc</th><th>Holder</th><th>Record</th>
          <th>Status</th><th class="num">Att</th><th>Detail</th><th>Updated</th>
        </tr></thead>
        <tbody>
          {#each superseded as j (j.wr_id)}
            <tr>
              <td>{j.course}</td>
              <td class="mono">{j.cc}</td>
              <td>{j.holder_name ?? "—"}</td>
              <td class="mono">{j.record_str}</td>
              <td><span class="dot" style="background:{STATUS_META[j.status]?.color ?? '#666'}"></span>{STATUS_META[j.status]?.label ?? j.status}</td>
              <td class="mono num">{j.attempts}</td>
              <td class="detail">{detailOf(j)}</td>
              <td class="seen" title={absTitle(j.updated_at)}>{relTime(j.updated_at)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</section>

<style>
  .jobs { max-width: 980px; margin: 0 auto; padding: 22px 24px; color: #c7ccd2;
          font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif; }
  h2 { color: #e8eaed; font-size: 18px; margin: 0 0 12px; }
  h3 { color: #cfd3d8; font-size: 13px; margin: 22px 0 6px; font-weight: 600; }
  .msg { color: #8a8f98; font-size: 13px; padding: 24px 0; }
  .sum { font-size: 13px; color: #8a8f98; margin: 0 0 12px; }
  .sum b { color: #e8eaed; font-weight: 600; }
  .sum b.ok { color: #4ade80; }
  .sum b.warn { color: #fbbf24; }
  .sum b.warn.zero { color: #8a8f98; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #7a818b; font-weight: 600; font-size: 11px; text-transform: uppercase;
       letter-spacing: .08em; padding: 4px 10px; border-bottom: 1px solid #23262b; }
  td { padding: 6px 10px; border-bottom: 1px solid #181b1f; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-variant-numeric: tabular-nums; color: #e8eaed; }
  .num { text-align: right; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; vertical-align: middle; }
  .detail { color: #8a8f98; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .seen { color: #8a8f98; white-space: nowrap; }
</style>
