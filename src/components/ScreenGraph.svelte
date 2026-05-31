<script>
  import { createEventDispatcher } from "svelte";
  import { C } from "../lib/palette.js";
  import {
    GRAPH_NODES, GRAPH_EDGES, GRAPH_NODE_MAP,
    NW, NH, GRAPH_W,
    fitToWrapper, zoomAt,
  } from "../lib/graph.js";

  /**
   * The live backend screen name (highlighted with accent border/fill).
   * Pass "—" or null when unknown.
   */
  export let currentScreen = null;

  /**
   * The screen name currently being edited (selected in the navigator).
   * Highlighted with a distinct treatment (white border, full-brightness text).
   */
  export let selected = null;

  const dispatch = createEventDispatcher();

  // ── Pan/zoom state ─────────────────────────────────────────────────────────
  let zoom = 1, panX = 0, panY = 0;
  let wrapW = 0, wrapH = 0;
  let fitted = false;

  // Auto-fit once dimensions are known, and re-fit whenever `fitted` is cleared.
  $: if (wrapW && wrapH && !fitted) {
    ({ zoom, panX, panY } = fitToWrapper(wrapW, wrapH));
    fitted = true;
  }

  /** Re-fit the graph to the current wrapper dimensions (call from parent if needed). */
  export function fit() { fitted = false; }

  // ── Drag-pan tracking ──────────────────────────────────────────────────────
  let _panning = false, _moved = false, _start = null;

  function onWheel(e) {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    ({ zoom, panX, panY } = zoomAt({ zoom, panX, panY }, e.deltaY, cx, cy));
  }

  function onDown(e) {
    _panning = true;
    _moved = false;
    _start = { x: e.clientX, y: e.clientY, px: panX, py: panY };
  }

  function onMove(e) {
    if (!_panning) return;
    const dx = e.clientX - _start.x, dy = e.clientY - _start.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) _moved = true;
    panX = _start.px + dx;
    panY = _start.py + dy;
  }

  function onUp() { _panning = false; }

  function onKeyDown(e) {
    // Allow keyboard activation of the SVG pan/zoom area (scroll zoom with
    // +/- keys so keyboard users can navigate).
    if (e.key === "+" || e.key === "=") {
      ({ zoom, panX, panY } = zoomAt({ zoom, panX, panY }, -1, wrapW / 2, wrapH / 2));
    } else if (e.key === "-") {
      ({ zoom, panX, panY } = zoomAt({ zoom, panX, panY }, 1, wrapW / 2, wrapH / 2));
    }
  }

  /** Emit `select` with the screen id on click, but ignore clicks that end a drag. */
  function nodeClick(id) {
    if (_moved) return;
    dispatch("select", id);
  }

  function nodeKeyDown(e, id) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      dispatch("select", id);
    }
  }

  // ── Edge dim logic (mirrors App.svelte) ───────────────────────────────────
  // HOME-cluster edges that aren't constant HOME↔TITLE/GALLERY are dimmed.
  function edgeDimmed(from, to) {
    const involvesHome = from === "HOME" || to === "HOME";
    if (!involvesHome) return false;
    const isConstant = involvesHome && (
      from === "TITLE" || to === "TITLE" ||
      from === "GALLERY" || to === "GALLERY"
    );
    return !isConstant;
  }
</script>

<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
<div
  class="sg-wrap"
  bind:clientWidth={wrapW}
  bind:clientHeight={wrapH}
>
  <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
  <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
  <svg
    class="sg-svg"
    class:panning={_panning}
    xmlns="http://www.w3.org/2000/svg"
    role="img"
    aria-label="Screen transition graph — click a node to select it for editing"
    tabindex="0"
    on:wheel|preventDefault={onWheel}
    on:mousedown={onDown}
    on:mousemove={onMove}
    on:mouseup={onUp}
    on:mouseleave={onUp}
    on:keydown={onKeyDown}
  >
    <g transform="translate({panX} {panY}) scale({zoom})">
      <!-- Edges -->
      {#each GRAPH_EDGES as [from, to]}
        {@const a = GRAPH_NODE_MAP[from]}
        {@const b = GRAPH_NODE_MAP[to]}
        {#if a && b}
          <line
            x1={a.x + NW / 2} y1={a.y + NH / 2}
            x2={b.x + NW / 2} y2={b.y + NH / 2}
            stroke={C.bd} stroke-width="1"
            opacity={edgeDimmed(from, to) ? 0.12 : 1}
          />
        {/if}
      {/each}

      <!-- Nodes -->
      {#each GRAPH_NODES as node}
        {@const isActive  = node.id === currentScreen}
        {@const isSel     = node.id === selected}
        {@const isUnknown = node.id === "UNKNOWN"}
        <g
          class="sg-node"
          transform="translate({node.x},{node.y})"
          role="button"
          tabindex="0"
          on:click={() => nodeClick(node.id)}
          on:keydown={(e) => nodeKeyDown(e, node.id)}
        >
          <rect
            width={NW} height={NH} rx="3" ry="3"
            fill={isActive ? C.accentBg : C.panel2}
            stroke={isSel ? C.tx : (isActive ? C.accent : C.bdSoft)}
            stroke-width={isSel || isActive ? 1.5 : 1}
            opacity={isUnknown ? 0.45 : 1}
          />
          <text
            x={NW / 2} y={NH / 2}
            text-anchor="middle" dominant-baseline="central"
            font-size="10" font-family="var(--mono)"
            fill={isSel ? C.tx : (isActive ? C.accent : (isUnknown ? C.txDim : C.txMut))}
            opacity={isUnknown ? 0.6 : 1}
          >{node.label}</text>
        </g>
      {/each}
    </g>
  </svg>
  <div class="sg-hint">scroll = zoom · drag = pan · click to select</div>
</div>

<style>
  .sg-wrap {
    width: 100%; height: 100%;
    display: flex; flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--bd);
    border-radius: var(--r);
    overflow: hidden;
  }

  .sg-svg {
    flex: 1; min-height: 0;
    width: 100%; display: block;
    cursor: grab; touch-action: none;
    outline: none;
  }
  .sg-svg.panning { cursor: grabbing; }
  .sg-svg:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }

  .sg-hint {
    flex: none;
    border-top: 1px solid var(--bd);
    padding: 3px 8px;
    font-size: .58rem;
    color: var(--tx-mut);
    text-align: right;
  }

  /* Node <g> elements — pointer so the SVG surface hint is visible */
  :global(.sg-node) { cursor: pointer; }
</style>
