<script>
  // SyncSettings.svelte - the "Sync" settings block: server URL + token + a
  // "Test connection" button. Self-contained (heading/intro/note/styles) so it is
  // identical in the first-time setup view (App.svelte) and the returning-user
  // Settings modal; each parent supplies its own nav buttons. Persists to
  // syncSettings (localStorage), decoupled from the Python config.
  import { invoke } from "@tauri-apps/api/core";
  import { serverUrl, authToken } from "../lib/syncSettings.js";
  import { pushSyncConfig } from "../lib/sync.js";

  let syncTest = { state: "idle", msg: "" };   // idle | testing | ok | err
  async function testSyncConnection() {
    syncTest = { state: "testing", msg: "" };
    try {
      await pushSyncConfig();   // ensure the latest URL/token are in the uploader first
      const msg = await invoke("sync_test_connection");
      syncTest = { state: "ok", msg };
    } catch (e) {
      syncTest = { state: "err", msg: typeof e === "string" ? e : (e?.message ?? String(e)) };
    }
  }
</script>

<div class="sync">
  <h2>Sync</h2>
  <p>Upload your runs to the competition server so they appear on the leaderboard and broadcast. Get your token from whoever runs the server.</p>

  <div class="fields">
    <label class="label" for="sync-url">Server URL</label>
    <input id="sync-url" class="input" type="text" bind:value={$serverUrl}
      placeholder="https://your-server.example" />
    <label class="label" for="sync-token">Your token</label>
    <input id="sync-token" class="input" type="password" bind:value={$authToken}
      placeholder="paste your token" />
  </div>
  <p class="note">Runs queue locally and upload when the server is reachable, so a flaky connection is fine. Leave the URL blank to disable uploading.</p>

  <div class="test">
    <button class="btn-nav" on:click={testSyncConnection} disabled={syncTest.state === "testing"}>
      {syncTest.state === "testing" ? "Testing…" : "Test connection"}
    </button>
    {#if syncTest.state === "ok"}
      <p class="test-msg test-ok">{syncTest.msg}</p>
    {:else if syncTest.state === "err"}
      <p class="test-msg test-err">{syncTest.msg}</p>
    {/if}
  </div>
</div>

<style>
  .sync { display: flex; flex-direction: column; gap: .75rem; }
  .sync h2 { color: var(--tx); font-size: .95rem; font-weight: 600; letter-spacing: .01em; }
  .sync p  { font-size: .76rem; color: var(--tx-mut); line-height: 1.6; margin: 0; }

  .fields { display: flex; flex-direction: column; gap: .35rem; }
  .label {
    font-size: .7rem; color: var(--tx-dim); margin: .1rem 0 0;
  }
  .input {
    background: var(--panel); color: var(--tx); border: 1px solid var(--bd);
    border-radius: var(--r); padding: .22rem .45rem;
    font-family: inherit; font-size: .72rem; width: 100%; box-sizing: border-box;
    transition: border-color .12s;
  }
  .input:focus { outline: none; border-color: var(--accent); }
  .input::placeholder { color: var(--tx-dim); }
  .note { font-size: .66rem; color: var(--tx-dim); line-height: 1.5; }

  .btn-nav {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .24rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-nav:hover { background: var(--raised); color: var(--tx); }
  .btn-nav:disabled { opacity: .35; cursor: default; }

  .test { display: flex; flex-direction: column; gap: .4rem; margin-top: .2rem; align-items: flex-start; }
  .test-msg { font-size: .68rem; line-height: 1.5; margin: 0; }
  .test-ok  { color: var(--ok); }
  .test-err { color: var(--warn); }
</style>
