import { writable } from "svelte/store";

// Sync settings, persisted in localStorage (decoupled from the Python config).
// NOTE: cc is intentionally NOT a setting here — it isn't user-configurable. MKW
// only has 150cc today; the engine will detect and send cc per run if more classes
// are ever added, and the server defaults to 150 when it's absent.
const URL_KEY = "sync_server_url";
const TOKEN_KEY = "sync_auth_token";

// localStorage is absent under Node (tests) - and under Node's experimental Web Storage it can
// be present-but-broken (the global exists yet getItem isn't callable). Probe it; fall back to a
// no-op so the module imports cleanly either way.
function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}
const ls = safeStorage();

// The friend-group competition server (see docs/pi-deploy.md step 8). Pre-filled on
// first run only: an absent key gets the default, but a deliberately-cleared "" stays
// blank so "leave the URL blank to disable uploading" keeps working.
export const DEFAULT_SERVER_URL = "https://api.thekartoff.com";
export function resolveServerUrl(stored) {
  return stored === null ? DEFAULT_SERVER_URL : stored;
}

export const serverUrl = writable(resolveServerUrl(ls.getItem(URL_KEY)));
export const authToken = writable(ls.getItem(TOKEN_KEY) || "");

serverUrl.subscribe((v) => ls.setItem(URL_KEY, v || ""));
authToken.subscribe((v) => ls.setItem(TOKEN_KEY, v || ""));
