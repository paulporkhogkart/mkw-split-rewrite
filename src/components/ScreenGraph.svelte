<script>
  import { createEventDispatcher } from "svelte";
  import { C } from "../lib/palette.js";
  import {
    GRAPH_NODES, GRAPH_EDGES, GRAPH_NODE_MAP,
    NW, NH, NIMG, GRAPH_W,
    fitToWrapper, zoomAt, edgePoint,
  } from "../lib/graph.js";

  /** Per-screen reference thumbnails: { SCREEN_NAME: dataURL }. Empty until fetched. */
  export let thumbs = {};

  /**
   * The live backend screen name (highlighted with accent border/fill).
   * Pass "-" or null when unknown.
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
    aria-label="Screen transition graph - click a node to select it for editing"
    tabindex="0"
    on:wheel|preventDefault={onWheel}
    on:mousedown={onDown}
    on:mousemove={onMove}
    on:mouseup={onUp}
    on:mouseleave={onUp}
    on:keydown={onKeyDown}
  >
    <defs>
      <!-- Directional arrowhead; sits on the target node's border. -->
      <marker id="sg-arrow" viewBox="0 0 8 8" refX="7" refY="4"
              markerWidth="5" markerHeight="5" orient="auto-start-reverse">
        <path d="M0 0 L8 4 L0 8 z" fill={C.txDim} />
      </marker>
      <!-- Clip the screenshot to the rounded top area of every card (local coords). -->
      <clipPath id="sg-card-clip" clipPathUnits="userSpaceOnUse">
        <rect x="1.5" y="1.5" width={NW - 3} height={NIMG} rx="3" ry="3" />
      </clipPath>
    </defs>

    <g transform="translate({panX} {panY}) scale({zoom})">
      <!-- Directed edges - clipped to node borders so the arrowhead reads.
           Dimmed HOME-cluster edges stay as faint lines (no arrowhead clutter). -->
      {#each GRAPH_EDGES as [from, to]}
        {@const a = GRAPH_NODE_MAP[from]}
        {@const b = GRAPH_NODE_MAP[to]}
        {#if a && b}
          {@const dim = edgeDimmed(from, to)}
          {@const p1 = edgePoint(a, b.x + NW / 2, b.y + NH / 2)}
          {@const p2 = edgePoint(b, a.x + NW / 2, a.y + NH / 2)}
          <line
            x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
            stroke={C.bd} stroke-width="1"
            opacity={dim ? 0.1 : 0.65}
            marker-end={dim ? null : "url(#sg-arrow)"}
          />
        {/if}
      {/each}

      <!-- Nodes - image cards: reference screenshot + label strip beneath. -->
      {#each GRAPH_NODES as node}
        {@const isActive  = node.id === currentScreen}
        {@const isSel     = node.id === selected}
        {@const isUnknown = node.id === "UNKNOWN"}
        {@const thumb     = thumbs[node.id]}
        <g
          class="sg-node"
          class:active={isActive}
          transform="translate({node.x},{node.y})"
          role="button"
          tabindex="0"
          on:click={() => nodeClick(node.id)}
          on:keydown={(e) => nodeKeyDown(e, node.id)}
        >
          <rect
            width={NW} height={NH} rx="4" ry="4"
            fill={isActive ? C.accentBg : C.panel2}
            stroke={isSel ? C.tx : (isActive ? C.accent : C.bdSoft)}
            stroke-width={isSel || isActive ? 1.6 : 1}
            opacity={isUnknown ? 0.5 : 1}
          />
          {#if thumb}
            <image
              href={thumb} x="1.5" y="1.5" width={NW - 3} height={NIMG}
              preserveAspectRatio="xMidYMid slice" clip-path="url(#sg-card-clip)"
            />
            <line x1="0.5" y1={NIMG + 1.5} x2={NW - 0.5} y2={NIMG + 1.5}
                  stroke={C.bd} stroke-width="1" opacity="0.8" />
            <text
              x={NW / 2} y={NIMG + (NH - NIMG) / 2 + 1}
              text-anchor="middle" dominant-baseline="central"
              font-size="11" font-family="var(--ui)"
              font-weight={isActive || isSel ? 600 : 400}
              fill={isActive || isSel ? C.tx : C.txMut}
            >{node.label}</text>
          {:else}
            <text
              x={NW / 2} y={NH / 2}
              text-anchor="middle" dominant-baseline="central"
              font-size="11" font-family="var(--ui)"
              font-weight={isActive || isSel ? 600 : 400}
              fill={isActive || isSel ? C.tx : (isUnknown ? C.txDim : C.txMut)}
              opacity={isUnknown ? 0.65 : 1}
            >{node.label}</text>
          {/if}
        </g>
      {/each}
    </g>
  </svg>
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

  /* Node <g> elements. Hover brightens the whole chip; the live (active) node
     gets a soft accent glow. Filters live on different elements (group vs rect)
     so they compose instead of overriding each other. */
  :global(.sg-node) { cursor: pointer; }
  :global(.sg-node rect) { transition: fill .12s, stroke .12s; }
  :global(.sg-node:hover) { filter: brightness(1.15); }
  :global(.sg-node.active rect) { filter: drop-shadow(0 0 3px rgba(61,124,194,.4)); }
</style>
