// Views selected by the URL path (History API — no more #/ hash). Unknown paths fall back
// to "live". `heat` and `version` are URL-only. `/players` and `/players/:slug` share the
// "players" view; the slug (via playerSlugFromPath) selects index vs profile.
export function viewFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  if (p === "turf") return "turf";
  if (p === "heat") return "heat";
  if (p === "version") return "version";
  if (p === "players" || p.startsWith("players/")) return "players";
  return "live";
}

/** The player slug from /players/:slug, or null on /players (index) and non-player paths. */
export function playerSlugFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  const m = /^players\/([^/]+)/.exec(p);
  return m ? m[1] : null;
}
