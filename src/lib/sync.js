// Pushes the sync config to the Rust uploader whenever it changes. Reads only.
import { invoke } from "@tauri-apps/api/core";
import { get } from "svelte/store";
import { serverUrl, authToken, cc } from "./syncSettings.js";

function push() {
  invoke("sync_set_config", {
    server_url: get(serverUrl),
    token: get(authToken),
    cc: get(cc),
  }).catch(() => {});
}

export function initSync() {
  [serverUrl, authToken, cc].forEach((s) => s.subscribe(() => push()));
}
