// Views selected by the location hash. Unknown hashes fall back to "live".
// `heat` is intentionally unlisted (no navbar tab) — reachable by URL only.
export function viewFromHash(hash) {
  const h = (hash || "").replace(/^#\/?/, "");
  if (h === "turf" || h === "territory") return "turf";   // "territory" kept as an old-hash alias
  if (h === "heat") return "heat";
  if (h === "version") return "version";   // unlisted, URL-only (no navbar tab)
  return "live";
}
