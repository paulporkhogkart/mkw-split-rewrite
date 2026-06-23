<script>
  import { onMount } from "svelte";
  import { versionUrl } from "./lib/api.js";
  import { SITE_VERSION, componentRows, playerRows } from "./lib/version.js";

  let payload = null, loaded = false, error = false;
  let comps = [], players = [];

  onMount(async () => {
    try {
      const res = await fetch(versionUrl(), { cache: "no-store" });
      if (!res.ok) throw new Error(`version ${res.status}`);
      payload = await res.json();
      comps = componentRows(payload, SITE_VERSION);
      players = playerRows(payload, Date.now());
      loaded = true;
    } catch (e) {
      console.error("version load failed", e);
      error = true;
    }
  });

  const dot = (s) => (s === "current" ? "✓" : s === "behind" ? "⚠" : s === "ahead" ? "dev" : "?");
</script>

<section class="ver">
  <h2>versions</h2>
  {#if error}
    <p class="msg">Couldn't load version data.</p>
  {:else if !loaded}
    <p class="msg">Loading…</p>
  {:else}
    <table>
      <thead><tr><th>Component</th><th>Latest</th><th>Deployed</th><th class="sth"></th></tr></thead>
      <tbody>
        {#each comps as c}
          <tr>
            <td>{c.label}</td>
            <td class="mono">{c.latest ?? "?"}</td>
            <td class="mono">{c.key === "app" ? c.summary : (c.deployed ?? "?")}</td>
            <td class="st {c.status}">{c.key === "app" ? "" : dot(c.status)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    <h3>players — installed app (last ran)</h3>
    <table>
      <thead><tr><th>Player</th><th>Installed</th><th>Last seen</th><th class="sth"></th></tr></thead>
      <tbody>
        {#each players as p}
          <tr>
            <td><span class="swatch" style="background:{p.color || '#555'}"></span>{p.name}</td>
            <td class="mono">{p.app_version ?? "—"}</td>
            <td class="seen">{p.last_seen}</td>
            <td class="st {p.status}">{dot(p.status)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if payload?.latest?.errors?.length}
      <p class="errs">latest-version lookup issues: {payload.latest.errors.join("; ")}</p>
    {/if}
  {/if}
</section>

<style>
  .ver { max-width: 760px; margin: 0 auto; padding: 22px 24px; color: #c7ccd2;
         font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif; }
  h2 { color: #e8eaed; font-size: 18px; margin: 0 0 12px; }
  h3 { color: #cfd3d8; font-size: 13px; margin: 22px 0 6px; font-weight: 600; }
  .msg { color: #8a8f98; font-size: 13px; padding: 24px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #7a818b; font-weight: 600; font-size: 11px; text-transform: uppercase;
       letter-spacing: .08em; padding: 4px 10px; border-bottom: 1px solid #23262b; }
  th.sth { width: 34px; }
  td { padding: 6px 10px; border-bottom: 1px solid #181b1f; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-variant-numeric: tabular-nums; color: #e8eaed; }
  .seen { color: #8a8f98; }
  .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; vertical-align: middle; }
  .st { text-align: center; font-weight: 700; }
  .st.current { color: #4ade80; }
  .st.behind { color: #fbbf24; }
  .st.ahead { color: #7a818b; font-weight: 600; font-size: 11px; }
  .st.unknown { color: #6f7782; }
  .errs { margin-top: 14px; font-size: 11px; color: #7a818b; }
</style>
