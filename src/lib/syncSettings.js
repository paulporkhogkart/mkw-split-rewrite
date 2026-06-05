import { writable } from "svelte/store";

// Sync settings, persisted in localStorage (decoupled from the Python config).
// NOTE: cc is intentionally NOT a setting here — it isn't user-configurable. MKW
// only has 150cc today; the engine will detect and send cc per run if more classes
// are ever added, and the server defaults to 150 when it's absent.
const URL_KEY = "sync_server_url";
const TOKEN_KEY = "sync_auth_token";

export const serverUrl = writable(localStorage.getItem(URL_KEY) || "");
export const authToken = writable(localStorage.getItem(TOKEN_KEY) || "");

serverUrl.subscribe((v) => localStorage.setItem(URL_KEY, v || ""));
authToken.subscribe((v) => localStorage.setItem(TOKEN_KEY, v || ""));
