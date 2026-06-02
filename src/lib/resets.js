// App-owned per-session reset counter. Counts transitions INTO the RESET screen
// (not GHOST_RESET / UNKNOWN_RESET). Resets to 0 when the course changes.
import { writable } from "svelte/store";
import { screen, selection } from "./stores.js";

export const resets = writable(0);

let prevScreen = null;
let prevCourse = null;

screen.subscribe((s) => {
  if (s === "RESET" && prevScreen !== "RESET") resets.update((n) => n + 1);
  prevScreen = s;
});

selection.subscribe((sel) => {
  if (sel.course !== prevCourse) { prevCourse = sel.course; resets.set(0); }
});
