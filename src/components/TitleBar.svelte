<script>
  /** App version string (without the "v" prefix) */
  export let version = "";
  /** Current top-level view: "monitor" | "edit" */
  export let view = "monitor";
  /** Screen name being edited (shown in edit view), or null */
  export let editingScreen = null;
  /** Called to flip monitor↔edit (Edit screens / ← Monitor buttons) */
  export let onToggleView = () => {};
  /** Called when the Minimize window control is clicked */
  export let onMinimize = () => {};
  /** Called when the Maximize/restore window control is clicked */
  export let onToggleMaximize = () => {};
  /** Called when the Close window control is clicked */
  export let onClose = () => {};
</script>

<header class="titlebar" data-tauri-drag-region>
  {#if view === "edit"}
    <button class="tb-back" on:click={onToggleView}>← Monitor</button>
    <div class="tb-editing" data-tauri-drag-region>
      <span class="editing-label">Editing</span>
      <span class="editing-sep">·</span>
      <span class="editing-screen">{editingScreen ?? "-"}</span>
    </div>
  {:else}
    <div class="tb-brand" data-tauri-drag-region>
      <span class="brand-name">MKW Tracker</span>
      {#if version}<span class="brand-ver">v{version}</span>{/if}
    </div>
  {/if}

  <div class="tb-actions" data-tauri-drag-region>
    <!-- Update strip: App.svelte injects its existing update markup here (monitor view) -->
    {#if view !== "edit"}
      <slot name="update" />
      <button class="btn-hdr btn-edit" on:click={onToggleView}>Edit screens</button>
    {/if}

    <!-- Settings button slot: App.svelte injects the view-conditional button -->
    <slot name="settings" />
  </div>

  <div class="win-controls">
    <button class="win-btn" on:click={onMinimize} title="Minimize">&#x2013;</button>
    <button class="win-btn" on:click={onToggleMaximize} title="Maximize">&#x25a1;</button>
    <button class="win-btn win-btn-close" on:click={onClose} title="Close">&#x2715;</button>
  </div>
</header>

<style>
  .titlebar {
    display: flex; align-items: center; height: 32px; flex-shrink: 0;
    background: var(--panel); border-bottom: 1px solid var(--bd);
    padding: 0 0 0 12px; gap: 8px;
    -webkit-app-region: drag; user-select: none;
  }
  .tb-brand { display: flex; align-items: baseline; gap: 5px; flex-shrink: 0; }
  .brand-name { font-size: .82rem; font-weight: 600; color: var(--tx); letter-spacing: .01em; }
  .brand-ver  { font-size: .68rem; color: var(--tx-dim); }

  /* Edit-view left side: back button + "Editing - SCREEN" */
  .tb-back {
    -webkit-app-region: no-drag; flex-shrink: 0;
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    color: var(--tx-mut); font-family: inherit; font-size: .74rem;
    padding: 3px 10px; cursor: pointer; transition: background .12s, color .12s;
  }
  .tb-back:hover { background: var(--raised); color: var(--tx); }
  .tb-editing { display: flex; align-items: baseline; gap: 6px; flex-shrink: 0; }
  .editing-label  { font-size: .76rem; color: var(--tx-mut); }
  .editing-sep    { font-size: .76rem; color: var(--tx-dim); }
  .editing-screen { font-size: .76rem; color: var(--tx); font-family: var(--mono); }

  .tb-actions {
    display: flex; align-items: center; gap: 6px; flex-shrink: 0;
    -webkit-app-region: no-drag; margin-left: auto;
  }

  /* "Edit screens" button (monitor view) */
  .btn-hdr {
    background: var(--panel); border-radius: var(--r); padding: 3px 10px;
    font-family: inherit; font-size: .74rem; cursor: pointer; white-space: nowrap;
    transition: background .12s; -webkit-app-region: no-drag;
  }
  .btn-edit       { color: var(--tx-mut); border: 1px solid var(--bd); }
  .btn-edit:hover { background: var(--raised); color: var(--tx); }

  .win-controls { display: flex; flex-shrink: 0; margin-left: 0; }
  .win-btn {
    background: transparent; border: none; color: var(--tx-dim);
    width: 46px; height: 32px; font-size: .82rem; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .1s, color .1s; flex-shrink: 0;
    -webkit-app-region: no-drag;
  }
  .win-btn:hover { background: var(--bd); color: var(--tx); }
  .win-btn-close:hover { background: var(--close); color: #fff; }
</style>
