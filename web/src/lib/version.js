// Pure helpers for the unlisted #/version page. SITE_VERSION is the deployed site build, baked
// in by vite (web/vite.config.js define); the typeof guard keeps vitest (no define) from throwing.
export const SITE_VERSION = typeof __SITE_VERSION__ !== "undefined" ? __SITE_VERSION__ : "dev";

export function parseSemver(v) {
  if (typeof v !== "string") return null;
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(v.trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** -1/0/1, or null when either side is unparseable (caller renders "unknown"). */
export function compareSemver(a, b) {
  const pa = parseSemver(a), pb = parseSemver(b);
  if (!pa || !pb) return null;
  for (let i = 0; i < 3; i++) if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  return 0;
}

export function status(deployed, latest) {
  const cmp = compareSemver(deployed, latest);
  if (cmp === null) return "unknown";
  if (cmp < 0) return "behind";
  if (cmp > 0) return "ahead";
  return "current";
}

export function formatLastSeen(lastSeenAt, now) {
  if (!lastSeenAt) return "never";
  const d = now - lastSeenAt;
  if (d < 60_000) return "online";
  if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
  return `${Math.floor(d / 86_400_000)}d ago`;
}

/** Rows for the components table. The app row carries an "N/M on latest" summary instead of a
 *  single deployed version (it's per-player). server/bot/site compare deployed vs the latest tag. */
export function componentRows(payload, siteVersion) {
  const tag = payload?.latest?.tag ?? null;
  const app = payload?.latest?.app ?? null;
  const server = payload?.deployed?.server?.version ?? null;
  const bot = payload?.deployed?.bot?.version ?? null;
  const players = payload?.players ?? [];
  const reported = players.filter((p) => p.app_version);
  const onLatest = reported.filter((p) => compareSemver(p.app_version, app) === 0);
  return [
    { key: "app", label: "pbenguin app", latest: app, deployed: null,
      summary: app ? `${onLatest.length}/${reported.length} on latest` : "—", status: "na" },
    { key: "server", label: "server", latest: tag, deployed: server, status: status(server, tag) },
    { key: "bot", label: "bot", latest: tag, deployed: bot, status: status(bot, tag) },
    { key: "site", label: "site", latest: tag, deployed: siteVersion, status: status(siteVersion, tag) },
  ];
}

export function playerRows(payload, now) {
  const app = payload?.latest?.app ?? null;
  return (payload?.players ?? []).map((p) => ({
    player_id: p.player_id, name: p.name, color: p.color ?? null,
    app_version: p.app_version ?? null,
    last_seen: formatLastSeen(p.last_seen_at, now),
    status: p.app_version ? status(p.app_version, app) : "unknown",
  }));
}
