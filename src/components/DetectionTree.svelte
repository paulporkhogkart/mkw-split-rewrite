<script>
  import { createEventDispatcher } from "svelte";
  import { C } from "../lib/palette.js";
  import { scoreColor, fmtScore } from "../lib/format.js";

  /**
   * The boolean-tree for the currently edited screen.
   * Shape: Array<Array<{ kind: string, roi: number[]|null, thresh?: number, ... }>>
   * Outer array = groups (ANDed); inner array = regions within a group (ORed).
   */
  export let groups = [];

  /**
   * The currently selected region: { group: number, region: number }
   */
  export let active = { group: 0, region: 0 };

  /**
   * Whether the reset-confirm gate is currently open (two-step confirm).
   */
  export let resetPending = false;

  /**
   * The match score for the active region, or null if not yet tested.
   * Shape: { score: number, threshold: number, matched: boolean } | null
   */
  export let currentScore = null;

  /**
   * The screen name — shown in the reset-confirm message.
   */
  export let screenName = "";

  const dispatch = createEventDispatcher();

  function dotColor(gi, ri) {
    const isActive = gi === active.group && ri === active.region;
    if (isActive) return C.accent;
    if (gi === active.group) return C.roiCtx;
    return C.warn;
  }

  function regionLabel(region, ri) {
    return region.kind === "dark_loading" ? "dark-loading" : `image ${ri + 1}`;
  }

  function isDeleteable() {
    if (!groups || groups.length === 0) return false;
    return groups.length > 1 || (groups[active.group]?.length ?? 0) > 1;
  }
</script>

<div class="det-tree">
  {#if groups && groups.length > 0}
    <div class="tree-label">Detected when ALL groups match:</div>

    {#each groups as group, gi}
      {#if gi > 0}
        <div class="tree-and">— AND —</div>
      {/if}
      <div class="tree-group">
        <div class="tree-group-hd">Group {gi + 1} · any of</div>
        {#each group as region, ri}
          <button
            type="button"
            class="tree-region"
            class:sel={active.group === gi && active.region === ri}
            on:click={() => dispatch("selectRegion", { group: gi, region: ri })}
          >
            <span class="treg-dot" style="background:{dotColor(gi, ri)}"></span>
            <span class="treg-name">{regionLabel(region, ri)}</span>
            {#if active.group === gi && active.region === ri && currentScore}
              <span class="treg-score" style="color:{scoreColor(currentScore.score)}">{fmtScore(currentScore.score)}</span>
            {/if}
          </button>
        {/each}
        <button type="button" class="tree-add" on:click={() => dispatch("addRegion", gi)}>+ OR alternative image</button>
      </div>
    {/each}

    <button type="button" class="tree-add tree-add-and" on:click={() => dispatch("addGroup")}>+ AND condition group</button>

    <!-- Active-region kind selector + delete control -->
    {#if groups[active.group]?.[active.region]}
      {@const activeRegionObj = groups[active.group][active.region]}
      <div class="reg-controls">
        <div class="reg-row">
          <label class="reg-kind">Kind
            <select
              value={activeRegionObj.kind}
              on:change={(e) => dispatch("kindChange", e.target.value)}
            >
              <option value="template">Template image</option>
              <option value="dark_loading">Dark-loading</option>
            </select>
          </label>
          {#if isDeleteable()}
            <button type="button" class="reg-del" on:click={() => dispatch("removeRegion")}>
              🗑 Delete region
            </button>
          {/if}
        </div>
        {#if activeRegionObj.kind === "dark_loading"}
          <p class="hint">Dark-loading detects a near-black region plus a bright icon. Drag the main ROI on the feed; the icon ROI uses its default position.</p>
        {/if}
      </div>
    {/if}

    <!-- Reset detection to defaults (two-step confirm) -->
    <div class="det-reset">
      {#if resetPending}
        <p class="det-reset-q">
          Reset <b>{screenName}</b>'s detection ROIs &amp; groups to defaults? This discards your custom regions for this screen.
        </p>
        <div class="det-reset-row">
          <button type="button" class="btn-reset-confirm" on:click={() => dispatch("resetDetection")}>Yes, reset</button>
          <button type="button" class="btn-nav" on:click={() => dispatch("cancelReset")}>Cancel</button>
        </div>
      {:else}
        <button type="button" class="det-reset-btn" on:click={() => dispatch("requestReset")}>↺ Reset detection to defaults</button>
      {/if}
    </div>
  {:else}
    <p class="hint">Loading detection config…</p>
  {/if}
</div>

<style>
  .det-tree { display: flex; flex-direction: column; gap: 6px; }
  .tree-label { font-size: .66rem; text-transform: uppercase; letter-spacing: .08em; color: var(--tx-mut); }
  .tree-and { text-align: center; font-size: .62rem; letter-spacing: .2em; color: var(--accent-soft); margin: 1px 0; }
  .tree-group { border: 1px solid var(--bd); border-radius: var(--r); padding: 6px; background: var(--panel-2); }
  .tree-group-hd { font-size: .58rem; text-transform: uppercase; letter-spacing: .06em; color: var(--accent-soft); margin-bottom: 4px; }

  .tree-region {
    display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
    background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r);
    padding: 4px 7px; margin-bottom: 3px; color: var(--tx-mut);
    font-family: inherit; font-size: .72rem; cursor: pointer;
  }
  .tree-region:hover { border-color: var(--accent-soft); }
  .tree-region.sel { border-color: var(--accent); background: var(--accent-bg); color: var(--tx); }

  .treg-dot { width: 9px; height: 9px; border-radius: var(--r-sm); flex: none; }
  .treg-name { flex: 1; }
  .treg-score { font-family: var(--mono); font-size: .68rem; }

  .tree-add {
    width: 100%; background: none; border: 1px dashed var(--tx-dim); border-radius: var(--r);
    color: var(--tx-mut); font-family: inherit; font-size: .64rem; padding: 3px; cursor: pointer;
  }
  .tree-add:hover { color: var(--tx-mut); border-color: var(--tx-dim); }
  .tree-add-and { border-color: var(--bd); margin-top: 2px; }

  .reg-controls { border-top: 1px solid var(--bd); margin-top: 4px; padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
  .reg-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .reg-kind { font-size: .66rem; color: var(--tx-mut); display: flex; align-items: center; gap: 5px; }
  .reg-kind select { background: var(--panel-2); color: var(--tx); border: 1px solid var(--bd); border-radius: var(--r); font-family: inherit; font-size: .7rem; padding: 2px 4px; }
  .reg-del { background: none; border: 1px solid var(--err); color: var(--err); border-radius: var(--r); font-family: inherit; font-size: .64rem; padding: 3px 7px; cursor: pointer; }
  .reg-del:hover { background: var(--err); color: #fff; }

  .hint { font-size: .66rem; color: var(--tx-dim); line-height: 1.5; }

  .det-reset { border-top: 1px solid var(--bd); margin-top: 4px; padding-top: 8px; }
  .det-reset-btn {
    width: 100%; background: none; border: 1px solid var(--bd); border-radius: var(--r);
    color: var(--tx-mut); font-family: inherit; font-size: .66rem; padding: 5px; cursor: pointer;
  }
  .det-reset-btn:hover { border-color: var(--err); color: var(--err); }
  .det-reset-q { font-size: .68rem; color: var(--err); margin: 0 0 6px; }
  .det-reset-row { display: flex; gap: 8px; }

  /* btn-reset-confirm and btn-nav mirror App.svelte's global definitions */
  .btn-reset-confirm {
    background: rgba(207,91,78,.12); border: 1px solid rgba(207,91,78,.35);
    color: var(--err); font-size: .72rem; padding: .3rem .75rem;
    border-radius: var(--r); cursor: pointer; font-family: inherit;
  }
  .btn-reset-confirm:hover { background: rgba(207,91,78,.2); }
  .btn-nav {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .24rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; flex-shrink: 0;
  }
  .btn-nav:hover { background: var(--raised); color: var(--tx); }
</style>
