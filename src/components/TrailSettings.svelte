<script>
  // TrailSettings.svelte - the "Trails" settings tab: per-player ghost-trail playback
  // (mode + count) + the global fade toggle. Persists to trailSettings (localStorage).
  // Colours are locked + auto-assigned per player (same on every client), shown read-only.
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { trailSettings, roster, cacheRoster, playerCfg, playerColor } from "../lib/trailSettings.js";

  const MODES = [
    ["none", "None"], ["pbs", "PBs only"], ["best", "Best N"],
    ["last", "Last N"], ["last_pb", "PB + Last N"], ["all", "All"],
  ];
  const usesN = (m) => m === "best" || m === "last" || m === "last_pb";

  onMount(async () => {
    try {
      const list = JSON.parse(await invoke("sync_roster"));
      if (Array.isArray(list) && list.length) cacheRoster(list);
    } catch (_) { /* offline: keep the cached roster */ }
  });

  function setPlayer(p, patch) {
    trailSettings.update((s) => ({
      ...s,
      players: { ...s.players, [p.player_id]: { ...playerCfg(s, p), ...patch } },
    }));
  }
  const setFade = (v) => trailSettings.update((s) => ({ ...s, fadeByRank: v }));
</script>

<div class="trk">
  <h2>Ghost trails</h2>
  <p>Pick whose past runs replay as moving ghost dots on the minimap during a race, and how many. Trails come from the server, so a friend's runs appear as soon as they upload. Each player's colour is fixed and the same for everyone.</p>

  <label class="fade">
    <input type="checkbox" checked={$trailSettings.fadeByRank} on:change={(e) => setFade(e.target.checked)} />
    <span class="fade-tx"><b>Fade older runs by rank</b><i>Off by default. When on, Best / Last sets dim weaker runs - Last: newest brightest; Best: fastest brightest. The PB always stays bright.</i></span>
  </label>

  {#if $roster.length === 0}
    <p class="empty">No roster loaded. Set your server URL + token in the Sync tab, then reopen this tab.</p>
  {:else}
    <div class="grid">
      <div class="row head"><span>Player</span><span>Show</span><span class="ralign">Count</span><span class="calign">Colour</span></div>
      {#each $roster as p (p.player_id)}
        {@const cfg = playerCfg($trailSettings, p)}
        <div class="row" class:off={cfg.mode === "none"}>
          <span class="pn">{p.display_name}{#if p.is_me}<i class="you">YOU</i>{/if}</span>
          <select value={cfg.mode} on:change={(e) => setPlayer(p, { mode: e.target.value })}>
            {#each MODES as [v, l]}<option value={v}>{l}</option>{/each}
          </select>
          <input class="n" type="text" inputmode="numeric" value={cfg.n}
            disabled={!usesN(cfg.mode)}
            on:change={(e) => setPlayer(p, { n: Math.max(1, parseInt(e.target.value, 10) || 1) })} />
          <span class="chip" class:off={cfg.mode === "none"}
            style={cfg.mode === "none" ? "" : `background:${playerColor(p.player_id)}`} title="Locked colour"></span>
        </div>
      {/each}
    </div>
  {/if}

  <p class="note">Default is <b>PB + Last 49</b> for you and <b>PB + Last 24</b> for everyone else (the PB shows even when it's older than that). Large sets put many ghosts on the minimap at once.</p>
</div>

<style>
  .trk { max-width: 600px; margin: 0 auto; display: flex; flex-direction: column; gap: .7rem; padding: .25rem 0; }
  .trk h2 { color: var(--tx); font-size: .95rem; font-weight: 600; letter-spacing: .01em; }
  .trk > p { font-size: .76rem; color: var(--tx-mut); line-height: 1.6; margin: 0; }

  .fade { display: flex; align-items: flex-start; gap: .5rem; background: var(--panel-2);
    border: 1px solid var(--bd); border-radius: var(--r); padding: .5rem .6rem; cursor: pointer; }
  .fade input { accent-color: var(--accent); cursor: pointer; margin-top: .15rem; }
  .fade-tx b { font-size: .74rem; font-weight: 600; color: var(--tx); }
  .fade-tx i { display: block; font-style: normal; font-size: .66rem; color: var(--tx-dim); margin-top: .15rem; line-height: 1.5; }

  .grid { border: 1px solid var(--bd); border-radius: var(--r); overflow: hidden; }
  .row { display: grid; grid-template-columns: 1.4fr 1.3fr 64px 52px; align-items: center; gap: .6rem;
    padding: .5rem .7rem; border-bottom: 1px solid var(--bd-soft); }
  .row:last-child { border-bottom: 0; }
  .row.head { background: var(--panel-2); }
  .row.head span { font-size: .6rem; text-transform: uppercase; letter-spacing: .06em; color: var(--tx-dim); }
  .ralign { text-align: right; } .calign { text-align: center; }
  .pn { font-size: .78rem; color: var(--tx); display: flex; align-items: center; gap: .4rem; }
  .row.off .pn { color: var(--tx-dim); }
  .you { font-style: normal; font-size: .56rem; color: var(--accent); border: 1px solid var(--accent);
    border-radius: var(--r-sm); padding: 0 .3rem; letter-spacing: .04em; }

  .n { width: 100%; background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r-sm);
    color: var(--tx); font-family: var(--mono); font-size: .74rem; text-align: right; padding: .26rem .45rem;
    -webkit-appearance: none; appearance: none; }
  .n:disabled { opacity: .32; cursor: default; }
  .n:focus { outline: none; border-color: var(--accent); }

  .chip { width: 26px; height: 18px; border-radius: var(--r-sm); border: 1px solid rgba(255,255,255,.18);
    margin: 0 auto; display: block; }
  .chip.off { background: repeating-linear-gradient(45deg, #2a2b2f, #2a2b2f 3px, #222 3px, #222 6px); }

  .empty { font-size: .72rem; color: var(--tx-dim); background: var(--panel-2); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .6rem .7rem; margin: 0; }
  .note { font-size: .66rem; color: var(--tx-dim); line-height: 1.55; margin: 0; }
  .note b { color: var(--tx-mut); }
</style>
