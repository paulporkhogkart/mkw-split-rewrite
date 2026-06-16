<script>
  // GhostImportWarning.svelte — confirm before arming ghost import.
  //   events: enable · cancel
  import { createEventDispatcher } from "svelte";
  import { fade, scale } from "svelte/transition";
  import { quintOut } from "svelte/easing";
  const dispatch = createEventDispatcher();
</script>

<div class="gi-backdrop" transition:fade={{ duration: 120 }}>
  <div class="gi-dialog" role="dialog" aria-modal="true" aria-labelledby="gi-title"
       in:scale={{ duration: 170, start: 0.97, opacity: 0, easing: quintOut }}>
    <header class="gi-head"><h2 id="gi-title" class="gi-title">Import PB from ghost</h2></header>
    <div class="gi-body">
      <p>When this is turned on, the <strong>next ghost you watch</strong> will be added as one
         of your runs.</p>
      <p class="gi-warn">This is very hard to undo on the database end, so please don't misuse it.</p>
    </div>
    <footer class="gi-foot">
      <button class="gi-btn gi-btn-ghost" on:click={() => dispatch("cancel")}>Cancel</button>
      <button class="gi-btn gi-btn-primary" on:click={() => dispatch("enable")}>OK, enable</button>
    </footer>
  </div>
</div>

<style>
  .gi-backdrop { position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,.62);
    display: flex; align-items: center; justify-content: center; padding: 24px; }
  .gi-dialog { width: 100%; max-width: 360px; display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    box-shadow: 0 16px 44px rgba(0,0,0,.5); overflow: hidden; }
  .gi-head { padding: .6rem .85rem; border-bottom: 1px solid var(--bd-soft); }
  .gi-title { font-size: .82rem; font-weight: 600; color: var(--tx); }
  .gi-body { padding: .7rem .85rem; display: flex; flex-direction: column; gap: .5rem;
    font-size: .74rem; color: var(--tx-mut); line-height: 1.45; }
  .gi-warn { color: var(--warn); }
  .gi-foot { display: flex; align-items: center; justify-content: flex-end; gap: .55rem;
    padding: .55rem .85rem; border-top: 1px solid var(--bd-soft); }
  .gi-btn { font-family: inherit; font-size: .72rem; cursor: pointer; padding: .26rem .8rem;
    border-radius: var(--r); border: 1px solid var(--bd); background: var(--panel-2); color: var(--tx-mut);
    transition: background-color .12s, border-color .12s, color .12s; }
  .gi-btn-ghost:hover { background: var(--raised); color: var(--tx); }
  .gi-btn-primary { background: var(--accent-bg); border-color: var(--accent); color: var(--tx); }
  .gi-btn-primary:hover { background: var(--raised); }
</style>
