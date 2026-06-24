// Maps a player name -> { on, off, onpace } figure URLs, bundled by Vite from
// src/assets/players/. Filenames are <name>__on.png / <name>__off.png /
// <name>__onpace.png (see scripts/gen_player_figures.py + pick_player_figures.py).
import { playerKey } from "./playerKey.js";

const mods = import.meta.glob("../assets/players/*.png", { eager: true, query: "?url", import: "default" });

const map = {};
for (const [path, url] of Object.entries(mods)) {
  const m = /\/([a-z0-9]+)__(on|off|onpace)\.png$/.exec(path);
  if (m) (map[m[1]] ??= {})[m[2]] = url;
}

/** Figure URL for a player by display name + online state; null when none is bundled. */
export function figureFor(name, online) {
  const e = map[playerKey(name)] || {};
  return (online ? e.on : e.off) || e.on || e.off || null;
}

/** The "on fire" (PB pace) figure for a player, or null when none is set - callers
 *  fall back to the online figure. */
export function onpaceFigure(name) {
  return (map[playerKey(name)] || {}).onpace || null;
}
