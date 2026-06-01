<script>
  import { createEventDispatcher } from "svelte";

  /** Section heading text */
  export let title = "";
  /**
   * Suppress the top border on the very first section. Pass true when this
   * RailSection is rendered first in its container - :first-child cannot work
   * across separate component instances.
   */
  export let first = false;
  /** Whether the section can be collapsed by clicking the header */
  export let collapsible = false;
  /** Current open/collapsed state (only meaningful when collapsible=true) */
  export let open = true;
  /**
   * When true (and open), the section flexes to fill the rail's remaining
   * vertical space and its body scrolls internally. Used by the Event log.
   */
  export let grow = false;

  const dispatch = createEventDispatcher();
</script>

<section class="rail-section" class:grow={grow && open}>
  {#if collapsible}
    <button
      type="button"
      class="sh"
      class:sh-first={first}
      on:click={() => dispatch("toggle")}
      aria-expanded={open}
    >
      <span>{title}</span>
      <span class="caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
    </button>
  {:else}
    <div class="sh" class:sh-first={first}>
      <span>{title}</span>
    </div>
  {/if}

  {#if open}
    <div class="sh-body"><slot /></div>
  {/if}
</section>

<style>
  /* A section is a flex column so a `grow` section can hand its body the
     remaining height; non-grow sections collapse to their content height. */
  .rail-section { display: flex; flex-direction: column; min-height: 0; }
  .rail-section.grow { flex: 1 1 0; }
  .sh-body { min-height: 0; }
  .rail-section.grow > .sh-body {
    flex: 1 1 0; display: flex; flex-direction: column; overflow: hidden;
  }

  .sh {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--panel);
    padding: 6px 12px;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--tx);
    border-top: 1px solid var(--bd);
    /* reset button defaults */
    border-left: none;
    border-right: none;
    border-bottom: none;
    border-radius: 0;
    font-family: inherit;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum";
    cursor: default;
    text-align: left;
    line-height: 1.4;
    box-sizing: border-box;
  }
  .sh.sh-first {
    border-top: none;
  }
  button.sh {
    cursor: pointer;
  }
  button.sh:hover {
    background: var(--raised);
  }
  /* Triangles (▾ / ▸) - one glyph family, so size & baseline stay constant
     across open/closed (the old ⌄ / › pair jumped in size and position). */
  .caret {
    color: var(--tx-mut);
    font-weight: 400;
    font-size: 9px;
    line-height: 1;
    width: 10px;
    text-align: center;
    flex: none;
  }
</style>
