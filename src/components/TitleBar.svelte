<script>
  /** App version string (without the "v" prefix) */
  export let version = "";
  /** Called when the ⚙ settings button is clicked */
  export let onSettings = () => {};
  /** Called when the Minimize window control is clicked */
  export let onMinimize = () => {};
  /** Called when the Maximize/restore window control is clicked */
  export let onToggleMaximize = () => {};
  /** Called when the Close window control is clicked */
  export let onClose = () => {};
</script>

<header class="titlebar" data-tauri-drag-region>
  <div class="tb-brand" data-tauri-drag-region>
    <span class="brand-name">MKW Tracker</span>
    {#if version}<span class="brand-ver">v{version}</span>{/if}
  </div>

  <div class="tb-actions" data-tauri-drag-region>
    <!-- Update strip: App.svelte injects its existing update markup here -->
    <slot name="update" />

    <!-- Settings button slot: App.svelte injects the view-conditional button -->
    <slot name="settings">
      <button class="btn-hdr btn-setup" on:click={onSettings}>⚙ Settings</button>
    </slot>
  </div>

  <div class="win-controls">
    <button class="win-btn" on:click={onMinimize} title="Minimize">&#x2013;</button>
    <button class="win-btn" on:click={onToggleMaximize} title="Maximize">&#x25a1;</button>
    <button class="win-btn win-btn-close" on:click={onClose} title="Close">&#x2715;</button>
  </div>
</header>

<style>
  .titlebar {
    display: flex; align-items: center; height: 40px; flex-shrink: 0;
    background: var(--panel); border-bottom: 1px solid var(--bd);
    padding: 0 0 0 12px; gap: 8px;
    -webkit-app-region: drag; user-select: none;
  }
  .tb-brand { display: flex; align-items: baseline; gap: 5px; flex-shrink: 0; }
  .brand-name { font-size: .85rem; font-weight: bold; color: var(--tx); letter-spacing: .02em; }
  .brand-ver  { font-size: .65rem; color: var(--tx-dim); }

  .tb-actions {
    display: flex; align-items: center; gap: 6px; flex-shrink: 0;
    -webkit-app-region: no-drag; margin-left: auto;
  }

  .btn-hdr {
    background: var(--panel); border-radius: var(--r); padding: 3px 9px;
    font-family: inherit; font-size: .68rem; cursor: pointer; white-space: nowrap;
    transition: background .12s; -webkit-app-region: no-drag;
  }
  .btn-setup       { color: var(--tx-mut); border: 1px solid var(--bd); }
  .btn-setup:hover { background: var(--raised); }

  .win-controls { display: flex; flex-shrink: 0; margin-left: 0; }
  .win-btn {
    background: transparent; border: none; color: var(--tx-dim);
    width: 46px; height: 40px; font-size: .78rem; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .1s, color .1s; flex-shrink: 0;
    -webkit-app-region: no-drag;
  }
  .win-btn:hover { background: var(--bd); color: var(--tx); }
  .win-btn-close:hover { background: var(--close); color: #fff; }
</style>
