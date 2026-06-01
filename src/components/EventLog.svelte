<script>
  import { afterUpdate } from "svelte";
  import { logs } from "../lib/stores.js";

  /** Maximum number of log entries to render (keeps DOM small). */
  const MAX_ENTRIES = 150;

  let container;

  afterUpdate(() => {
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  });

  $: entries = $logs.length > MAX_ENTRIES ? $logs.slice(-MAX_ENTRIES) : $logs;
</script>

<div class="log-body" bind:this={container}>
  {#each entries as line}
    <div class="log-row">{line}</div>
  {/each}
</div>

<style>
  /* Fills the (growable) Event-log section body and scrolls internally, so the
     log occupies all remaining rail height instead of a fixed 140px sliver.
     min-height keeps a usable strip if the rail is very short. */
  .log-body {
    flex: 1 1 0;
    min-height: 64px;
    overflow: auto;   /* scroll both ways: vertical for entries, horizontal for long lines */
  }

  .log-row {
    font-size: 11.5px;
    font-family: var(--ui);
    color: var(--tx-mut);
    padding: 2px 12px;
    white-space: nowrap;      /* never wrap a log line */
    width: max-content;       /* grow past the body width so the h-scrollbar engages */
    min-width: 100%;          /* short lines still span the full width */
    box-sizing: border-box;
    line-height: 1.45;
  }
</style>
