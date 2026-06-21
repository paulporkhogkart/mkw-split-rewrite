// Two views, selected by the location hash. Unknown hashes fall back to "live".
export function viewFromHash(hash) {
  return (hash || "").replace(/^#\/?/, "") === "territory" ? "territory" : "live";
}
