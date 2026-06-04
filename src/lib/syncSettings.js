import { writable } from "svelte/store";

// Sync settings, persisted in localStorage (decoupled from the Python config).
const URL_KEY = "sync_server_url";
const TOKEN_KEY = "sync_auth_token";
const CC_KEY = "sync_cc";

export const serverUrl = writable(localStorage.getItem(URL_KEY) || "");
export const authToken = writable(localStorage.getItem(TOKEN_KEY) || "");
export const cc = writable(Number(localStorage.getItem(CC_KEY)) || 150);

serverUrl.subscribe((v) => localStorage.setItem(URL_KEY, v || ""));
authToken.subscribe((v) => localStorage.setItem(TOKEN_KEY, v || ""));
cc.subscribe((v) => localStorage.setItem(CC_KEY, String(v || 150)));
