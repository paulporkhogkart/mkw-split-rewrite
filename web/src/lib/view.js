// Views selected by the URL path (History API — no more #/ hash). Unknown paths fall back
// to "live". `heat`, `version`, and `wr-jobs` are URL-only. `/players` and `/players/:slug` share the
// "players" view; the slug (via playerSlugFromPath) selects index vs profile. Likewise
// `/tracks` and `/tracks/:slug` share the "courses" view; the slug (via courseSlugFromPath)
// selects index vs per-track profile.
export function viewFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  if (p === "turf") return "turf";
  if (p === "heat") return "heat";
  if (p === "version") return "version";
  if (p === "wr-jobs") return "wr-jobs";
  if (p === "players" || p.startsWith("players/")) return "players";
  if (p === "tracks" || p.startsWith("tracks/")) return "courses";
  return "live";
}

/** The player slug from /players/:slug, or null on /players (index) and non-player paths. */
export function playerSlugFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  const m = /^players\/([^/]+)/.exec(p);
  return m ? m[1] : null;
}

/** The course slug from /tracks/:slug, or null on /tracks (index) and non-track paths. */
export function courseSlugFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  const m = /^tracks\/([^/]+)/.exec(p);
  return m ? m[1] : null;
}
