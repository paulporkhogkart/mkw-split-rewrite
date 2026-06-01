<script>
  /**
   * EditMode - the dedicated screen-editing view.
   *
   * Layout: a ScreenGraph top strip, then a row with the RoiCanvas (center, flex)
   * and the ToolsPanel (right). Purely presentational - every interaction is
   * forwarded to the parent (App.svelte), which holds the editor state + IPC.
   *
   * Props:
   *   currentScreen   {string|null}  live backend screen (graph highlight)
   *   selected        {string|null}  screen currently being edited (graph selection)
   *   stream          {MediaStream|null}  live browser camera stream - preferred canvas background
   *   frame           {string|null}  engine-frame data-URL - fallback background when no stream
   *   rois            {Array}         [{ box:[x1,y1,x2,y2], role }] drawable ROIs
   *   activeBox       {Array|null}    [x1,y1,x2,y2] of the editable ROI (gets handles)
   *   frameW          {number}        logical frame width  (default 1920)
   *   frameH          {number}        logical frame height (default 1080)
   *   activeTab       {string}        ToolsPanel tab ("detection" | "readout")
   *   readoutEnabled  {boolean}       whether the Readout tab is available
   *   detection       {object}        ToolsPanel detection bundle { tree, inspector }
   *   readout         {object}        ToolsPanel readout bundle
   *
   * Events (all bubbled to the parent):
   *   selectScreen                   graph node clicked (payload = screen name)
   *   change                         RoiCanvas ROI drag/resize commit (payload = new box)
   *   selectBox                      RoiCanvas inactive-box click (payload = roi entry)
   *   tabChange                      ToolsPanel tab switch
   *   selectRegion, addRegion, addGroup, removeRegion, kindChange,
   *   requestReset, cancelReset, resetDetection, capture,
   *   selectRoi, selectItem, resetRoi   (ToolsPanel forwards)
   */
  import { createEventDispatcher } from "svelte";
  import ScreenGraph from "./ScreenGraph.svelte";
  import RoiCanvas   from "./RoiCanvas.svelte";
  import ToolsPanel  from "./ToolsPanel.svelte";

  export let currentScreen  = null;
  export let selected       = null;
  export let stream         = null;
  export let frame          = null;
  export let thumbs         = {};
  export let rois           = [];
  export let activeBox      = null;
  export let frameW         = 1920;
  export let frameH         = 1080;
  export let activeTab      = "detection";
  export let readoutEnabled = false;
  export let detection      = { tree: {}, inspector: {} };
  export let readout        = {};

  const dispatch = createEventDispatcher();

  // Expose the graph + canvas methods so the parent can re-fit / reset on enter.
  let graphEl = null, canvasEl = null;
  export function fitGraph()   { graphEl?.fit(); }
  export function resetCanvas() { canvasEl?.resetView(); }
</script>

<div class="edit-mode">
  <!-- Top strip: screen-transition graph navigator -->
  <div class="em-graph">
    <ScreenGraph
      bind:this={graphEl}
      {currentScreen}
      {selected}
      {thumbs}
      on:select={(e)=>dispatch("selectScreen", e.detail)}
    />
  </div>

  <!-- Center: ROI canvas · Right: tools panel -->
  <div class="em-body">
    <div class="em-canvas">
      <RoiCanvas
        bind:this={canvasEl}
        {stream}
        {frame}
        {rois}
        {activeBox}
        {frameW}
        {frameH}
        on:change
        on:select={(e)=>dispatch("selectBox", e.detail)}
      />
    </div>
    <div class="em-tools">
      <ToolsPanel
        {activeTab}
        {readoutEnabled}
        {detection}
        {readout}
        on:tabChange
        on:selectRegion
        on:addRegion
        on:addGroup
        on:removeRegion
        on:kindChange
        on:requestReset
        on:cancelReset
        on:resetDetection
        on:capture
        on:selectRoi
        on:selectItem
        on:resetRoi
      />
    </div>
  </div>
</div>

<style>
  .edit-mode {
    flex: 1; min-height: 0;
    display: flex; flex-direction: column;
    background: var(--bg);
    overflow: hidden;
  }

  /* Graph strip - taller now that nodes are image cards (screenshots need room). */
  .em-graph {
    flex: none; height: 332px;
    padding: 8px 8px 0;
    box-sizing: border-box;
  }

  /* Canvas + tools row fills the rest */
  .em-body {
    flex: 1; min-height: 0;
    display: flex;
    gap: 8px;
    padding: 8px;
    box-sizing: border-box;
  }

  /* Center canvas pane - flexes to fill, keeps the ROI overlay positioned */
  .em-canvas {
    flex: 1; min-width: 0; position: relative;
    background: var(--feed-bg);
    border: 1px solid var(--bd);
    border-radius: var(--r);
    overflow: hidden;
  }

  /* Right tools column - fixed width */
  .em-tools {
    flex: none; width: 320px; min-height: 0;
    border: 1px solid var(--bd);
    border-radius: var(--r);
    overflow: hidden;
  }
</style>
