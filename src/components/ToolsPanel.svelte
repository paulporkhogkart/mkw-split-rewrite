<script>
  import { createEventDispatcher } from "svelte";
  import DetectionTree    from "./DetectionTree.svelte";
  import RegionInspector  from "./RegionInspector.svelte";
  import ReadoutRoiEditor from "./ReadoutRoiEditor.svelte";

  // ── Props ──────────────────────────────────────────────────────────────────

  /** Which tab is currently active. */
  export let activeTab = "detection";

  /**
   * Whether the Readout tab is available for the current screen.
   * When false the Readout button is rendered disabled.
   */
  export let readoutEnabled = false;

  /**
   * Props bundle for the Detection tab.
   * Shape:
   *   {
   *     tree: {
   *       groups: Array<Array<object>>,
   *       active: { group: number, region: number },
   *       resetPending: boolean,
   *       currentScore: { score, threshold, matched } | null,
   *       screenName: string
   *     },
   *     inspector: {
   *       liveCrop: string | null,
   *       template: string | null,
   *       score: { score, threshold, matched } | null,
   *       isCostume: boolean,
   *       capturing: boolean
   *     }
   *   }
   */
  export let detection = { tree: {}, inspector: {} };

  /**
   * Props bundle for the Readout tab - passed directly to ReadoutRoiEditor.
   * Shape: {
   *   roiKeys, roiMetas, activeRoiName, tabKind, templateCategory, categoryLabel,
   *   items, activeItemIdx, activeItemHint, assetTemplate, assetLiveCrop,
   *   capturing, resetPending
   * }
   */
  export let readout = {};

  // ── Events ─────────────────────────────────────────────────────────────────
  const dispatch = createEventDispatcher();

  function onTabClick(tab) {
    if (tab === "readout" && !readoutEnabled) return;
    if (tab !== activeTab) dispatch("tabChange", tab);
  }
</script>

<div class="tools-panel">
  <!-- Tab chrome -->
  <div class="tabs" role="tablist">
    <button
      type="button"
      role="tab"
      aria-selected={activeTab === "detection"}
      class="tab"
      class:on={activeTab === "detection"}
      on:click={() => onTabClick("detection")}
    >Detection</button>

    <button
      type="button"
      role="tab"
      aria-selected={activeTab === "readout"}
      class="tab"
      class:on={activeTab === "readout"}
      disabled={!readoutEnabled}
      on:click={() => onTabClick("readout")}
    >Readout</button>
  </div>

  <!-- Tab body -->
  <div class="tab-body">
    {#if activeTab === "detection"}
      <DetectionTree
        {...detection.tree}
        on:selectRegion
        on:addRegion
        on:addGroup
        on:removeRegion
        on:kindChange
        on:requestReset
        on:cancelReset
        on:resetDetection
      />
      <div class="inspector-sep"></div>
      <RegionInspector
        {...detection.inspector}
        on:capture
      />
    {:else}
      <ReadoutRoiEditor
        {...readout}
        on:selectRoi
        on:selectItem
        on:capture
        on:requestReset
        on:cancelReset
        on:resetRoi
      />
    {/if}
  </div>
</div>

<style>
  .tools-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--panel);
    overflow: hidden;
  }

  /* Tab chrome - inset-underline style from mockup */
  .tabs {
    display: flex;
    border-bottom: 1px solid var(--bd);
    flex-shrink: 0;
  }

  .tab {
    flex: 1;
    text-align: center;
    padding: 7px 0;
    font-size: .72rem;
    font-family: var(--ui);
    color: var(--tx-mut);
    background: none;
    border: none;
    cursor: pointer;
    border-bottom: 2px solid transparent;   /* reserve space; overridden by box-shadow */
    box-shadow: none;
    transition: color .12s;
  }

  .tab:hover:not(:disabled) { color: var(--tx); }

  .tab.on {
    color: var(--tx);
    box-shadow: inset 0 -2px 0 var(--accent);
  }

  .tab:disabled {
    color: var(--tx-dim);
    cursor: default;
  }

  /* Scrollable body */
  .tab-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  /* Thin separator between DetectionTree and RegionInspector */
  .inspector-sep {
    border-top: 1px solid var(--bd);
    margin: 0 -10px;   /* bleed to panel edges */
  }
</style>
