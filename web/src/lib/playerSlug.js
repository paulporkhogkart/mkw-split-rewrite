// Client mirror of the Pi's slugify (pi/src/db/slug.ts): a player's display name -> route slug.
// Players have no slug column, so /players/:slug is resolved by slugified display name on both ends.
export function playerSlug(name) {
  return (name || "")
    .toLowerCase()
    .replace(/[‘’']/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}
