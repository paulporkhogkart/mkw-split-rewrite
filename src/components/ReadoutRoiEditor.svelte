<script>
  import { createEventDispatcher } from "svelte";
  import { C } from "../lib/palette.js";

  // ── Props ──────────────────────────────────────────────────────────────────
  /** Ordered ROI keys for the current screen's selection or HUD tab. */
  export let roiKeys = [];
  /** { [key]: { label, hint } } so the editor can render labels + hints. */
  export let roiMetas = {};
  /** Currently selected ROI key, or null. */
  export let activeRoiName = null;
  /** "selection" → "Text ROI" header · "hud" → "HUD ROI". */
  export let tabKind = "selection";
  /** Template category key for the active ROI (e.g. "characters"), or null. */
  export let templateCategory = null;
  /** Human-readable label for the template category column header. */
  export let categoryLabel = null;
  /** Items in the active template category - each { name, file }. */
  export let items = [];
  /** Index of the selected item within `items`. */
  export let activeItemIdx = 0;
  /** Contextual hint describing how to capture the active item's template. */
  export let activeItemHint = null;
  /** Stored template image data-URL for the active item, or null. */
  export let assetTemplate = null;
  /** Live camera crop data-URL (Canny edges when category is "costumes"), or null. */
  export let assetLiveCrop = null;
  /** Whether a template capture request is in flight (disables Capture). */
  export let capturing = false;
  /** Whether the two-step ROI reset confirm gate is open. */
  export let resetPending = false;

  // ── Events ─────────────────────────────────────────────────────────────────
  const dispatch = createEventDispatcher();
  const onSelectRoi    = (key) => dispatch("selectRoi", key);
  const onSelectItem   = (i)   => dispatch("selectItem", i);
  const onCapture      = ()    => dispatch("capture");
  const onRequestReset = ()    => dispatch("requestReset");
  const onCancelReset  = ()    => dispatch("cancelReset");
  const onResetRoi     = ()    => dispatch("resetRoi");

  // ── Derived helpers ──────────────────────────────────────────────────────────
  $: activeRoiLabel = activeRoiName ? (roiMetas[activeRoiName]?.label ?? activeRoiName) : null;
  $: activeRoiHint  = activeRoiName ? (roiMetas[activeRoiName]?.hint  ?? "")            : null;
  $: isCostume = templateCategory === "costumes";
  $: activeItem = items[activeItemIdx] ?? null;
</script>

<div class="readout">
  <div class="cols">
    <!-- ROI key list -->
    <div class="col">
      <p class="col-cap">{tabKind === "hud" ? "HUD ROI" : "Text ROI"}</p>
      <div class="list">
        {#each roiKeys as k (k)}
          <button
            type="button"
            class="reg"
            class:sel={activeRoiName === k}
            on:click={() => onSelectRoi(k)}
          >
            <span class="reg-dot" style="background:{activeRoiName === k ? C.accent : C.warn}"></span>
            <span class="reg-name">{roiMetas[k]?.label ?? k}</span>
          </button>
        {/each}
      </div>

      {#if activeRoiName}
        <div class="reset">
          {#if resetPending}
            <p class="reset-q">Reset <b>{activeRoiLabel}</b> to default?</p>
            <div class="reset-row">
              <button type="button" class="reset-yes" on:click={onResetRoi}>Reset</button>
              <button type="button" class="reset-no"  on:click={onCancelReset}>Cancel</button>
            </div>
          {:else}
            <button type="button" class="reset-btn" on:click={onRequestReset}>Reset ROI</button>
          {/if}
        </div>
      {/if}
    </div>

    <!-- Per-item template list -->
    {#if templateCategory}
      <div class="col">
        <p class="col-cap">{categoryLabel ?? templateCategory}</p>
        <div class="tpl-list">
          {#each items as item, i (item.file)}
            <button
              type="button"
              class="tpl"
              class:sel={activeItemIdx === i}
              on:click={() => onSelectItem(i)}
            >{item.name}</button>
          {/each}
        </div>
      </div>
    {/if}
  </div>

  <!-- Capture (template category + active item) -->
  {#if templateCategory && activeItem}
    <div class="cap">
      {#if activeItemHint}<p class="hint">{activeItemHint}</p>{/if}
      <div class="thumbs">
        <figure class="thumb">
          {#if assetLiveCrop}<img src={assetLiveCrop} alt="live" />{:else}<div class="thumb-empty"></div>{/if}
          <figcaption>{isCostume ? "live · edges" : "live"}</figcaption>
        </figure>
        <figure class="thumb">
          {#if assetTemplate}<img src={assetTemplate} alt="template" />{:else}<div class="thumb-empty"></div>{/if}
          <figcaption>template</figcaption>
        </figure>
      </div>
      <button type="button" class="capture" on:click={onCapture} disabled={capturing}>
        {capturing ? "Capturing…" : `Capture ${activeItem.name}`}
      </button>
    </div>
  {:else if activeRoiName}
    <p class="hint hint-roi">{activeRoiHint}</p>
  {/if}
</div>

<style>
  .readout { display: flex; flex-direction: column; gap: 9px; }

  .cols { display: flex; gap: 10px; align-items: flex-start; }
  .col { flex: 1; min-width: 0; }
  .col-cap { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--tx-mut); margin: 0 0 5px; }

  /* ROI key list - shares the Detection tab's region-row language */
  .list { border: 1px solid var(--bd); border-radius: var(--r); padding: 6px; background: var(--panel-2); }
  .reg {
    display: flex; align-items: center; gap: 7px; width: 100%; text-align: left;
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r-sm);
    padding: 5px 8px; margin-bottom: 4px; color: var(--tx-mut);
    font-family: inherit; font-size: 12px; cursor: pointer;
    transition: border-color .12s, background .12s, color .12s;
  }
  .reg:last-child { margin-bottom: 0; }
  .reg:hover { border-color: var(--accent-soft); color: var(--tx); }
  .reg.sel { border-color: var(--accent); background: var(--accent-bg); color: var(--tx); }
  .reg-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .reg-name { flex: 1; }

  /* Per-item template list */
  .tpl-list {
    max-height: 168px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 2px;
    border: 1px solid var(--bd); border-radius: var(--r); padding: 4px; background: var(--panel-2);
  }
  .tpl {
    text-align: left; background: none; border: none; color: var(--tx-mut);
    font-family: inherit; font-size: 12px; padding: 4px 8px; border-radius: var(--r-sm); cursor: pointer;
  }
  .tpl:hover { background: var(--raised); color: var(--tx); }
  .tpl.sel { background: var(--accent-bg); color: var(--tx); }

  /* Reset ROI */
  .reset { border-top: 1px solid var(--bd); margin-top: 8px; padding-top: 8px; }
  .reset-btn {
    width: 100%; background: none; border: 1px solid var(--bd); border-radius: var(--r-sm);
    color: var(--tx-dim); font-family: inherit; font-size: 11.5px; padding: 5px; cursor: pointer;
    transition: color .12s, border-color .12s;
  }
  .reset-btn:hover { border-color: var(--err); color: var(--err); }
  .reset-q { font-size: 11.5px; color: var(--err); margin: 0 0 7px; }
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

  /* Capture section */
  .cap { border-top: 1px solid var(--bd); padding-top: 9px; display: flex; flex-direction: column; gap: 8px; }
  .thumbs { display: flex; gap: 8px; }
  .thumb { flex: 1; margin: 0; }
  .thumb img, .thumb-empty {
    display: block; width: 100%; height: 42px; object-fit: contain;
    background: var(--feed-bg); border: 1px solid var(--bd); border-radius: var(--r-sm); image-rendering: pixelated;
  }
  .thumb figcaption { margin-top: 3px; font-size: 10px; color: var(--tx-dim); text-align: center; }
  .capture {
    align-self: flex-start; padding: 6px 12px; font-size: 12px; font-family: inherit;
    color: var(--tx-mut); background: var(--panel-2); border: 1px solid var(--bd);
    border-radius: var(--r); cursor: pointer; white-space: nowrap;
    transition: background .12s, color .12s, border-color .12s;
  }
  .capture:hover:not(:disabled) { background: var(--raised); color: var(--tx); border-color: var(--accent-soft); }
  .capture:disabled { opacity: .45; cursor: default; }

  .hint { font-size: 11px; color: var(--tx-dim); margin: 0; line-height: 1.55; }
  .hint-roi { margin-top: 2px; }
</style>
