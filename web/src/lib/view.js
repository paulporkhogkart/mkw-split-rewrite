// Views selected by the location hash. Unknown hashes fall back to "live".
// `heat` is intentionally unlisted (no navbar tab) — reachable by URL only.
export function viewFromHash(hash) {
  const h = (hash || "").replace(/^#\/?/, "");
  if (h === "territory") return "territory";
  if (h === "heat") return "heat";
  return "live";
}
