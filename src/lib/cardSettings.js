import { writable } from "svelte/store";

// Player-card display preferences, persisted in localStorage (client-side only -
// the server always broadcasts both delta flavours; the card picks one).
const MODE_KEY = "card_delta_mode";

function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}
const ls = safeStorage();

/** "pace" = fluid estimate from track position (default) | "laps" = LiveSplit-style,
 *  updates only at lap lines from the read lap times. */
export const deltaMode = writable(ls.getItem(MODE_KEY) === "laps" ? "laps" : "pace");
deltaMode.subscribe((v) => ls.setItem(MODE_KEY, v === "laps" ? "laps" : "pace"));
