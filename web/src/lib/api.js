// The season server origin. In dev (vite) it defaults to the local season server
// (`npm --prefix pi run dev` on :8787); a production build uses api.thekartoff.com.
// Override either with VITE_API_BASE.
const env = (typeof import.meta !== "undefined" && import.meta.env) || {};
export const API_BASE =
  env.VITE_API_BASE ||
  (env.DEV ? "http://127.0.0.1:8787" : "https://api.thekartoff.com");

export const territoryUrl = (cc = 150) => `${API_BASE}/v1/territory?cc=${cc}`;
export const territoryTimelineUrl = (cc = 150) => `${API_BASE}/v1/territory/timeline?cc=${cc}`;
export const versionUrl = () => `${API_BASE}/v1/version`;
export const activityUrl = (before, limit = 100) =>
  `${API_BASE}/v1/activity?limit=${limit}${before != null ? `&before=${before}` : ""}`;
export const activityStreamWsUrl = () =>
  `${API_BASE.replace(/^http/, "ws")}/v1/activity/stream`;
export const rosterUrl = () => `${API_BASE}/v1/roster`;
export const playerSummaryUrl = (slug, cc = 150) => `${API_BASE}/v1/players/${encodeURIComponent(slug)}?cc=${cc}`;
