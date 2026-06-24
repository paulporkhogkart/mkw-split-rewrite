// A player's display name -> the stable image key: their first name, lowercased. Card figures
// (src/assets/players/<key>__*.png) and territory popup GIFs (web/public/players/<key>.gif) are
// bundled under this single-token key, so a display-name change like "paul" -> "paul pork" still
// resolves to the existing "paul" assets.
export const playerKey = (name) => (name || "").toLowerCase().match(/[a-z0-9]+/)?.[0] ?? "";
