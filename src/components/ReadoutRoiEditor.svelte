<script>
  import { createEventDispatcher } from "svelte";
  import { C } from "../lib/palette.js";

  // ── Props ──────────────────────────────────────────────────────────────────
  /**
   * Ordered list of ROI keys for the current screen's selection or HUD tab
   * (e.g. ["char_name","costume"] for CHARACTER_SELECT, or
   * ["lap_current","lap_total","coin_left","coin_right","mushroom"] for RACING).
   */
  export let roiKeys = [];

  /**
   * Record of { label, hint } for each ROI key so the component can render labels
   * and the no-template hint text without knowing the full ROI metadata table.
   * Shape: { [key: string]: { label: string, hint: string } }
   */
  export let roiMetas = {};

  /** Currently selected ROI key, or null if none. */
  export let activeRoiName = null;

  /**
   * "selection" → column header reads "Text ROI"
   * "hud"       → column header reads "HUD ROI"
   */
  export let tabKind = "selection";

  /**
   * Template category key for the active ROI (e.g. "characters", "costumes"), or
   * null when the active ROI has no per-item template library.
   */
  export let templateCategory = null;

  /**
   * Human-readable label for the template category column header
   * (e.g. "Characters", "Costumes"). Only shown when templateCategory is non-null.
   */
  export let categoryLabel = null;

  /**
   * Items in the active template category.
   * Each element is { name: string, file: string }.
   */
  export let items = [];

  /** Index of the selected item within `items`. */
  export let activeItemIdx = 0;

  /**
   * Contextual hint text describing how to capture the active item's template
   * (e.g. "Navigate to character select in-game and choose Mario.").
   */
  export let activeItemHint = null;

  /** Stored template image data-URL for the active item, or null if not captured. */
  export let assetTemplate = null;

  /** Live camera crop data-URL for the active item, or null if not available.
   *  When templateCategory === "costumes" this is a Canny-edge image. */
  export let assetLiveCrop = null;

  /** Whether a template capture request is in flight. Disables the Capture button. */
  export let capturing = false;

  /** Whether the two-step ROI reset confirm gate is open. */
  export let resetPending = false;

  // ── Events ─────────────────────────────────────────────────────────────────
  const dispatch = createEventDispatcher();

  /** User clicked an ROI key row. Payload: { key: string } */
  function onSelectRoi(key) { dispatch("selectRoi", key); }

  /** User clicked an item in the template list. Payload: { index: number } */
  function onSelectItem(i) { dispatch("selectItem", i); }

  /** User clicked the Capture button for the active item. No payload. */
  function onCapture() { dispatch("capture"); }

  /** User clicked "↺ Reset ROI" — open the confirm gate. No payload. */
  function onRequestReset() { dispatch("requestReset"); }

  /** User clicked "Cancel" on the reset confirm gate. No payload. */
  function onCancelReset() { dispatch("cancelReset"); }

  /** User confirmed the ROI reset. No payload. */
  function onResetRoi() { dispatch("resetRoi"); }

  // ── Derived helpers ────────────────────────────────────────────────────────
  $: activeRoiLabel = activeRoiName ? (roiMetas[activeRoiName]?.label ?? activeRoiName) : null;
  $: activeRoiHint  = activeRoiName ? (roiMetas[activeRoiName]?.hint  ?? "")            : null;
  $: isCostume = templateCategory === "costumes";
  $: activeItem = items[activeItemIdx] ?? null;
</script>

<div class="readout-roi-editor">
  <!-- Two-column upper section: ROI list + (optionally) item list -->
  <div class="sel-cols">
    <!-- Left column: ROI key list + reset control -->
    <div class="sel-col sel-col-roi">
      <div class="tree-label">{tabKind === "hud" ? "HUD ROI" : "Text ROI"}</div>
      <div class="tree-group">
        {#each roiKeys as k (k)}
          <button
            type="button"
            class="tree-region"
            class:sel={activeRoiName === k}
            on:click={() => onSelectRoi(k)}
          >
            <span
              class="treg-dot"
              style="background:{activeRoiName === k ? C.accent : C.warn}"
            ></span>
            <span class="treg-name">{roiMetas[k]?.label ?? k}</span>
          </button>
        {/each}
      </div>

      {#if activeRoiName}
        <div class="det-reset">
          {#if resetPending}
            <p class="det-reset-q">Reset <b>{activeRoiLabel}</b> to default?</p>
            <div class="det-reset-row">
              <button type="button" class="btn-reset-confirm" on:click={onResetRoi}>Yes, reset</button>
              <button type="button" class="btn-nav" on:click={onCancelReset}>Cancel</button>
            </div>
          {:else}
            <button type="button" class="det-reset-btn" on:click={onRequestReset}>↺ Reset ROI</button>
          {/if}
        </div>
      {/if}
    </div>

    <!-- Right column: per-item template list (only when the ROI has a template category) -->
    {#if templateCategory}
      <div class="sel-col sel-col-list">
        <div class="tree-label">{categoryLabel ?? templateCategory}</div>
        <div class="tpl-list sel-tpl-list">
          {#each items as item, i (item.file)}
            <button
              type="button"
              class="tpl-item"
              class:sel={activeItemIdx === i}
              on:click={() => onSelectItem(i)}
            >{item.name}</button>
          {/each}
        </div>
      </div>
    {/if}
  </div>

  <!-- Lower section: thumbnails + capture (only when a template category + active item) -->
  {#if templateCategory && activeItem}
    <div class="reg-controls">
      {#if activeItemHint}
        <p class="hint">{activeItemHint}</p>
      {/if}
      <div class="reg-thumbs">
        <div class="reg-thumb">
          <span>live{isCostume ? " (edges)" : ""}</span>
          {#if assetLiveCrop}
            <img src={assetLiveCrop} alt={isCostume ? "live edges" : "live crop"} />
          {:else}
            <div class="reg-thumb-empty"></div>
          {/if}
        </div>
        <div class="reg-thumb">
          <span>template</span>
          {#if assetTemplate}
            <img src={assetTemplate} alt="template" />
          {:else}
            <div class="reg-thumb-empty"></div>
          {/if}
        </div>
      </div>
      <button
        type="button"
        class="btn-secondary reg-recap"
        on:click={onCapture}
        disabled={capturing}
      >
        {capturing ? "Capturing…" : `Capture ${activeItem.name}`}
      </button>
    </div>
  {:else if activeRoiName}
    <!-- No template category: show the ROI's contextual hint text -->
    <p class="hint" style="margin-top:6px">{activeRoiHint}</p>
  {/if}
</div>

<style>
  /* Layout */
  .readout-roi-editor { display: flex; flex-direction: column; gap: 0; }

  .sel-cols { display: flex; gap: 10px; align-items: flex-start; }
  .sel-col { min-width: 0; }
  .sel-col-roi { flex: 1; }
  .sel-col-list { flex: 1; }

  /* Column header */
  .tree-label { font-size: .66rem; text-transform: uppercase; letter-spacing: .08em; color: var(--tx-mut); margin-bottom: 4px; }

  /* ROI key list */
  .tree-group { border: 1px solid var(--bd); border-radius: var(--r); padding: 6px; background: var(--panel-2); }
  .tree-region {
    display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
    background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r);
    padding: 4px 7px; margin-bottom: 3px; color: var(--tx-mut);
    font-family: inherit; font-size: .72rem; cursor: pointer;
  }
  .tree-region:last-child { margin-bottom: 0; }
  .tree-region:hover { border-color: var(--accent-soft); }
  .tree-region.sel { border-color: var(--accent); background: var(--accent-bg); color: var(--tx); }
  .treg-dot { width: 9px; height: 9px; border-radius: var(--r-sm); flex: none; }
  .treg-name { flex: 1; }

  /* Per-item template list */
  .tpl-list {
    flex: 1; max-height: 360px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 2px;
    border: 1px solid var(--bd); border-radius: var(--r);
    padding: 4px; background: var(--panel-2);
  }
  .sel-tpl-list { max-height: 168px; }
  .tpl-item {
    text-align: left; background: none; border: none; color: var(--tx-mut);
    font-family: inherit; font-size: .72rem; padding: 4px 7px;
    border-radius: var(--r); cursor: pointer;
  }
  .tpl-item:hover { background: var(--raised); }
  .tpl-item.sel { background: var(--accent-bg); color: var(--tx); }

  /* ROI reset control */
  .det-reset { border-top: 1px solid var(--bd); margin-top: 4px; padding-top: 8px; }
  .det-reset-btn {
    width: 100%; background: none; border: 1px solid var(--bd); border-radius: var(--r);
    color: var(--tx-mut); font-family: inherit; font-size: .66rem; padding: 5px; cursor: pointer;
  }
  .det-reset-btn:hover { border-color: var(--err); color: var(--err); }
  .det-reset-q { font-size: .68rem; color: var(--err); margin: 0 0 6px; }
  .det-reset-row { display: flex; gap: 8px; }
  .btn-reset-confirm {
    background: rgba(207,91,78,.12); border: 1px solid rgba(207,91,78,.35);
    color: var(--err); font-size: .72rem; padding: .3rem .75rem;
    border-radius: var(--r); cursor: pointer;
  }
  .btn-reset-confirm:hover { background: rgba(207,91,78,.2); }
  .btn-nav {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .24rem .7rem; font-family: inherit;
    font-size: .72rem; cursor: pointer; flex-shrink: 0;
    transition: background .12s, color .12s;
  }
  .btn-nav:hover { background: var(--raised); color: var(--tx); }

  /* Capture section */
  .reg-controls {
    border-top: 1px solid var(--bd); margin-top: 4px; padding-top: 8px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .reg-thumbs { display: flex; gap: 8px; }
  .reg-thumb { flex: 1; font-size: .58rem; color: var(--tx-mut); text-align: center; }
  .reg-thumb img,
  .reg-thumb-empty {
    display: block; width: 100%; height: 40px; object-fit: contain;
    background: var(--panel-2); border: 1px solid var(--bd); border-radius: var(--r);
    margin-top: 2px; image-rendering: pixelated;
  }
  .reg-recap { font-size: .7rem; align-self: flex-start; }

  /* Capture button (btn-secondary) */
  .btn-secondary {
    background: var(--panel); color: var(--tx-dim); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .28rem .7rem; font-family: inherit;
    font-size: .72rem; cursor: pointer; white-space: nowrap; transition: background .12s;
  }
  .btn-secondary:hover:not(:disabled) { background: var(--raised); color: var(--tx-mut); }
  .btn-secondary:disabled { opacity: .4; cursor: default; }

  /* Hint text */
  .hint { font-size: .7rem; color: var(--tx-dim); margin: 0; line-height: 1.55; }
</style>
