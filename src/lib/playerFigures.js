// Maps a player name -> { on, off } figure URLs, bundled by Vite from src/assets/players/.
// Filenames are <name>__on.png / <name>__off.png (see scripts/gen_player_figures.py).
const mods = import.meta.glob("../assets/players/*.png", { eager: true, query: "?url", import: "default" });

const map = {};
for (const [path, url] of Object.entries(mods)) {
  const m = /\/([a-z0-9]+)__(on|off)\.png$/.exec(path);
  if (m) (map[m[1]] ??= {})[m[2]] = url;
}

/** Figure URL for a player by display name + online state; null when none is bundled. */
export function figureFor(name, online) {
  const e = map[(name || "").toLowerCase()] || {};
  return (online ? e.on : e.off) || e.on || e.off || null;
}
