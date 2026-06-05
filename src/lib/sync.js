// Pushes the sync config to the Rust uploader whenever it changes, and exposes a
// one-shot push used before a manual "test connection". Reads only.
import { invoke } from "@tauri-apps/api/core";
import { get } from "svelte/store";
import { serverUrl, authToken } from "./syncSettings.js";

// IMPORTANT: Tauri v2 maps camelCase JS arg keys to the snake_case Rust params
// (default rename_all = "camelCase"). The key MUST be `serverUrl` (not `server_url`)
// or the whole command is rejected and the uploader never gets its config.
export function pushSyncConfig() {
  return invoke("sync_set_config", {
    serverUrl: get(serverUrl),
    token: get(authToken),
  });
}

export function initSync() {
  [serverUrl, authToken].forEach((s) => s.subscribe(() => pushSyncConfig().catch(() => {})));
}
