<!-- src/components/KeybindRecorder.svelte
     Click to record a hotkey. Captures the next keydown on the button; a bare
     modifier keeps it waiting, Escape cancels. Emits the canonical combo via bind:value. -->
<script>
  import { formatKeybind, prettyKeybind } from "../lib/keybind.js";

  export let value = "";
  let recording = false;

  function start() { recording = true; }

  function onKeydown(e) {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") { recording = false; return; }
    const combo = formatKeybind(e);
    if (!combo) return;               // modifier-only — keep waiting
    value = combo;
    recording = false;
  }

  function onBlur() { recording = false; }
</script>

<button type="button" class="kb" class:recording on:click={start} on:keydown={onKeydown} on:blur={onBlur}>
  {recording ? "Press a key…" : (prettyKeybind(value) || "Set hotkey")}
</button>

<style>
  .kb {
    background: var(--panel); color: var(--tx); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .22rem .6rem;
    font-family: inherit; font-size: .72rem; min-width: 8rem; cursor: pointer;
    transition: border-color .12s, background .12s;
  }
  .kb:hover { background: var(--panel-2); }
  .kb.recording { border-color: var(--accent); color: var(--accent); }
</style>
