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
  {#each entries as line (line)}
    <div class="log-row">{line}</div>
  {/each}
</div>

<style>
  .log-body {
    max-height: 140px;
    overflow-y: auto;
  }

  .log-row {
    font-size: 11.5px;
    font-family: var(--ui);
    color: var(--tx-mut);
    padding: 2px 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.45;
  }
</style>
