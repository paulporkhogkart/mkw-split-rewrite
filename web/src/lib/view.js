// Views selected by the URL path (History API — no more #/ hash). Unknown paths fall back
// to "live". `heat` and `version` are intentionally unlisted (no navbar tab) — reachable by
// URL only. The static server (serve.mjs) serves index.html for these extension-less paths.
export function viewFromPath(pathname) {
  const p = (pathname || "/").replace(/^\/+/, "").replace(/\/+$/, "");
  if (p === "turf") return "turf";
  if (p === "heat") return "heat";
  if (p === "version") return "version";
  return "live";
}
