<script>
  // TrailSettings.svelte - the "Trails" settings tab: per-player ghost-trail playback
  // (mode / count / colour) + the global fade toggle. Persists to trailSettings (localStorage).
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { trailSettings, roster, cacheRoster, playerCfg, TRAIL_PRESETS } from "../lib/trailSettings.js";

  let openPicker = null;   // playerId whose colour popover is open

  const MODES = [["none", "None"], ["pbs", "PBs only"], ["best", "Best N"], ["last", "Last N"], ["all", "All"]];

  onMount(async () => {
    try {
      const list = JSON.parse(await invoke("sync_roster"));
      if (Array.isArray(list) && list.length) cacheRoster(list);
    } catch (_) { /* offline: keep the cached roster */ }
  });

  const cfgOf = (pid, idx) => playerCfg($trailSettings, pid, idx);

  function setPlayer(pid, idx, patch) {
    trailSettings.update((s) => ({
      ...s,
      players: { ...s.players, [pid]: { ...playerCfg(s, pid, idx), ...patch } },
    }));
  }
  const setFade = (v) => trailSettings.update((s) => ({ ...s, fadeByRank: v }));
</script>

<div class="trk">
  <h2>Ghost trails</h2>
  <p>Pick whose past runs replay as moving ghost dots on the minimap during a race, how many, and their colour. Trails come from the server, so a friend's runs appear as soon as they upload.</p>

  <label class="fade">
    <input type="checkbox" checked={$trailSettings.fadeByRank} on:change={(e) => setFade(e.target.checked)} />
    <span class="fade-tx"><b>Fade by rank</b><i>Best / Last sets fade weaker runs - Last: newest brightest; Best: fastest brightest.</i></span>
  </label>

  {#if $roster.length === 0}
    <p class="empty">No roster loaded. Set your server URL + token in the Sync tab, then reopen this tab.</p>
  {:else}
    <div class="grid">
      <div class="row head"><span>Player</span><span>Show</span><span class="ralign">Count</span><span class="calign">Colour</span></div>
      {#each $roster as p, idx (p.player_id)}
        {@const cfg = cfgOf(p.player_id, idx)}
        <div class="row" class:off={cfg.mode === "none"}>
          <span class="pn">{p.display_name}{#if p.is_me}<i class="you">YOU</i>{/if}</span>
          <select value={cfg.mode} on:change={(e) => setPlayer(p.player_id, idx, { mode: e.target.value })}>
            {#each MODES as [v, l]}<option value={v}>{l}</option>{/each}
          </select>
          <input class="n" type="number" min="1" value={cfg.n}
            disabled={!(cfg.mode === "best" || cfg.mode === "last")}
            on:change={(e) => setPlayer(p.player_id, idx, { n: Math.max(1, Number(e.target.value) || 1) })} />
          <button class="chip" class:none={cfg.mode === "none"}
            style={cfg.mode === "none" ? "" : `background:${cfg.color}`}
            on:click={() => (openPicker = openPicker === p.player_id ? null : p.player_id)} aria-label="Colour"></button>
        </div>
        {#if openPicker === p.player_id}
          <div class="pop">
            <div class="swatches">
              {#each TRAIL_PRESETS as h}
                <button class="sb" class:sel={cfg.color === h} style="background:{h}"
                  on:click={() => { setPlayer(p.player_id, idx, { color: h }); openPicker = null; }} aria-label={h}></button>
              {/each}
            </div>
            <label class="custom">Custom
              <input type="text" value={cfg.color} spellcheck="false"
                on:change={(e) => setPlayer(p.player_id, idx, { color: e.target.value })} />
            </label>
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <p class="note">Large sets (e.g. everyone's Last 100) put many ghosts on the minimap at once - the fade keeps the standout readable. Default is PBs for everyone; tune from here.</p>
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
  .row { display: grid; grid-template-columns: 1.4fr 1.25fr 64px 60px; align-items: center; gap: .6rem;
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
    color: var(--tx); font-family: var(--mono); font-size: .74rem; text-align: right; padding: .26rem .4rem; }
  .n:disabled { opacity: .32; cursor: default; }
  .n:focus { outline: none; border-color: var(--accent); }

  .chip { width: 30px; height: 20px; border-radius: var(--r-sm); border: 1px solid rgba(255,255,255,.18);
    cursor: pointer; margin: 0 auto; display: block; padding: 0; }
  .chip.none { background: repeating-linear-gradient(45deg, #2a2b2f, #2a2b2f 3px, #222 3px, #222 6px); }

  .pop { background: var(--well); border: 1px solid var(--bd); border-bottom: 1px solid var(--bd-soft);
    padding: .55rem .7rem; display: flex; flex-direction: column; gap: .55rem; }
  .swatches { display: flex; gap: .5rem; flex-wrap: wrap; }
  .sb { width: 24px; height: 24px; border-radius: var(--r-sm); border: 2px solid transparent; cursor: pointer; padding: 0; }
  .sb.sel { border-color: #fff; }
  .custom { font-size: .68rem; color: var(--tx-mut); display: flex; align-items: center; gap: .5rem; }
  .custom input { width: 100px; background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r-sm);
    color: var(--tx); font-family: var(--mono); font-size: .72rem; padding: .24rem .45rem; }
  .custom input:focus { outline: none; border-color: var(--accent); }

  .empty { font-size: .72rem; color: var(--tx-dim); background: var(--panel-2); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .6rem .7rem; margin: 0; }
  .note { font-size: .66rem; color: var(--tx-dim); line-height: 1.55; margin: 0; }
</style>
