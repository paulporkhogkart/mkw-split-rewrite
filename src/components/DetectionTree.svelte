<script>
  import { createEventDispatcher } from "svelte";
  import { C } from "../lib/palette.js";
  import { scoreColor, fmtScore } from "../lib/format.js";
  import { nosignalBadgeLabel } from "../lib/nosignal.js";

  /** Boolean tree for the edited screen: groups (ANDed) of regions (ORed). */
  export let groups = [];
  /** Currently selected region: { group, region }. */
  export let active = { group: 0, region: 0 };
  /** Whether the two-step reset-confirm gate is open. */
  export let resetPending = false;
  /** Live match score for the active region: { score, threshold, matched } | null. */
  export let currentScore = null;
  /** Screen name - shown in the reset-confirm message. */
  export let screenName = "";
  /** NO_SIGNAL mode bundle: { auto, brand }. */
  export let nosignalMode = { auto: true, brand: null };

  const dispatch = createEventDispatcher();

  function dotColor(gi, ri) {
    if (gi === active.group && ri === active.region) return C.accent;
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

<div class="det">
  {#if screenName === "NO_SIGNAL"}
    <div class="ns-badge">{nosignalBadgeLabel(nosignalMode)}</div>
  {/if}
  {#if groups && groups.length > 0}
    <p class="det-cap">Detected when every group matches</p>

    {#each groups as group, gi}
      {#if gi > 0}<div class="det-and"><span>AND</span></div>{/if}
      <div class="grp">
        <div class="grp-hd">Group {gi + 1} <span class="grp-any">· match any</span></div>
        {#each group as region, ri}
          <button
            type="button"
            class="reg"
            class:sel={active.group === gi && active.region === ri}
            on:click={() => dispatch("selectRegion", { group: gi, region: ri })}
          >
            <span class="reg-dot" style="background:{dotColor(gi, ri)}"></span>
            <span class="reg-name">{regionLabel(region, ri)}</span>
            {#if active.group === gi && active.region === ri && currentScore}
              <span class="reg-score" style="color:{scoreColor(currentScore.score)}">{fmtScore(currentScore.score)}</span>
            {/if}
          </button>
        {/each}
        <button type="button" class="add" on:click={() => dispatch("addRegion", gi)}>+ Image</button>
      </div>
    {/each}

    <button type="button" class="add add-grp" on:click={() => dispatch("addGroup")}>+ Group (AND)</button>

    <!-- Active-region kind + delete -->
    {#if groups[active.group]?.[active.region]}
      {@const activeRegionObj = groups[active.group][active.region]}
      <div class="reg-ctl">
        <div class="reg-ctl-row">
          <label class="kind">Kind
            <select
              value={activeRegionObj.kind}
              on:change={(e) => dispatch("kindChange", e.target.value)}
            >
              <option value="template">Template image</option>
              <option value="dark_loading">Dark-loading</option>
            </select>
          </label>
          {#if isDeleteable()}
            <button type="button" class="remove" on:click={() => dispatch("removeRegion")}>Remove</button>
          {/if}
        </div>
        {#if activeRegionObj.kind === "dark_loading"}
          <p class="hint">Detects a near-black region; if the region has an icon ROI, a bright colourful item must also be present there. Drag the main ROI on the feed; the icon ROI uses its default position.</p>
        {/if}
      </div>
    {/if}

    <!-- Reset detection to defaults (two-step confirm) -->
    <div class="reset">
      {#if resetPending}
        <p class="reset-q">Reset <b>{screenName}</b> detection to defaults? Your custom regions for this screen are discarded.</p>
        <div class="reset-row">
          <button type="button" class="reset-yes" on:click={() => dispatch("resetDetection")}>Reset</button>
          <button type="button" class="reset-no"  on:click={() => dispatch("cancelReset")}>Cancel</button>
        </div>
      {:else}
        <button type="button" class="reset-btn" on:click={() => dispatch("requestReset")}>{screenName === "NO_SIGNAL" ? "Revert to auto" : "Reset to defaults"}</button>
      {/if}
    </div>
  {:else}
    <p class="hint">Loading detection config…</p>
  {/if}
</div>

<style>
  .det { display: flex; flex-direction: column; gap: 7px; }

  .ns-badge {
    font-size: .64rem; color: var(--tx-mut);
    padding: .2rem .4rem; margin-bottom: .4rem;
    border: 1px solid var(--bd); border-radius: var(--r);
    background: var(--panel-2); text-align: center;
  }

  .det-cap { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--tx-mut); margin: 0; }

  /* AND divider between groups */
  .det-and { display: flex; align-items: center; gap: 8px; margin: 1px 0; }
  .det-and::before, .det-and::after { content: ""; flex: 1; height: 1px; background: var(--bd); }
  .det-and span { font-size: 10px; letter-spacing: .14em; color: var(--tx-dim); }

  /* Group card */
  .grp { border: 1px solid var(--bd); border-radius: var(--r); padding: 7px; background: var(--panel-2); }
  .grp-hd { font-size: 10.5px; font-weight: 600; color: var(--tx-mut); margin-bottom: 5px; }
  .grp-any { font-weight: 400; color: var(--tx-dim); }

  /* Region row */
  .reg {
    display: flex; align-items: center; gap: 7px; width: 100%; text-align: left;
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r-sm);
    padding: 5px 8px; margin-bottom: 4px; color: var(--tx-mut);
    font-family: inherit; font-size: 12px; cursor: pointer;
    transition: border-color .12s, background .12s, color .12s;
  }
  .reg:last-of-type { margin-bottom: 5px; }
  .reg:hover { border-color: var(--accent-soft); color: var(--tx); }
  .reg.sel { border-color: var(--accent); background: var(--accent-bg); color: var(--tx); }
  .reg-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .reg-name { flex: 1; }
  .reg-score { font-variant-numeric: tabular-nums; font-size: 11.5px; }

  /* Add buttons - solid subtle, not dashed */
  .add {
    width: 100%; background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r-sm);
    color: var(--tx-dim); font-family: inherit; font-size: 11.5px; padding: 5px; cursor: pointer;
    transition: background .12s, color .12s, border-color .12s;
  }
  .add:hover { color: var(--tx-mut); border-color: var(--accent-soft); background: var(--raised); }
  .add-grp { margin-top: 1px; }

  /* Active-region controls */
  .reg-ctl { border-top: 1px solid var(--bd); margin-top: 3px; padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
  .reg-ctl-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .kind { font-size: 11px; color: var(--tx-mut); display: flex; align-items: center; gap: 6px; }
  /* <select> styling is global (theme.css) so it matches the device/language pickers. */
  .remove {
    background: none; border: 1px solid var(--bd); color: var(--tx-mut);
    border-radius: var(--r-sm); font-family: inherit; font-size: 11px; padding: 4px 9px; cursor: pointer;
    transition: background .12s, color .12s, border-color .12s;
  }
  .remove:hover { border-color: var(--err); color: var(--err); }

  .hint { font-size: 11px; color: var(--tx-dim); line-height: 1.5; margin: 0; }

  /* Reset */
  .reset { border-top: 1px solid var(--bd); margin-top: 3px; padding-top: 8px; }
  .reset-btn {
    width: 100%; background: none; border: 1px solid var(--bd); border-radius: var(--r-sm);
    color: var(--tx-dim); font-family: inherit; font-size: 11.5px; padding: 5px; cursor: pointer;
    transition: color .12s, border-color .12s;
  }
  .reset-btn:hover { border-color: var(--err); color: var(--err); }
  .reset-q { font-size: 11.5px; color: var(--err); margin: 0 0 7px; line-height: 1.5; }
  .reset-row { display: flex; gap: 8px; }
  .reset-yes {
    background: rgba(207,91,78,.12); border: 1px solid rgba(207,91,78,.4); color: var(--err);
    font-family: inherit; font-size: 11.5px; padding: 4px 12px; border-radius: var(--r-sm); cursor: pointer;
  }
  .reset-yes:hover { background: rgba(207,91,78,.22); }
  .reset-no {
    background: var(--panel-2); border: 1px solid var(--bd); color: var(--tx-mut);
    font-family: inherit; font-size: 11.5px; padding: 4px 12px; border-radius: var(--r-sm); cursor: pointer;
  }
  .reset-no:hover { background: var(--raised); color: var(--tx); }
</style>
