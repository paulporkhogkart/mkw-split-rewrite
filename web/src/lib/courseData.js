// Assembles the hover-popup view-model for a course: leaderboard rows (with per-row colour +
// gap-to-#1), WR, on-fire flag, and the leader's GIF urls. Plus GIF preloading helpers. The
// territory page derives every board from the in-memory timeline event stream (see WorldMap),
// so there are no per-hover leaderboard/WR fetches here.
import { isOnFire } from "./fireModel.js";

const NEUTRAL = "#888";
const gifBase = (name) => `/players/${(name || "").toLowerCase()}`;

/** Internal: normalized entries [{name, color, time_ms, time_str?}] + wr (record_ms number|null)
 *  -> popup view-model. Sorts by time, ranks, computes gap-to-#1, on-fire, leader gif urls. */
function assembleCourseView({ entries, wr, courseName }) {
  const sorted = [...entries].sort((a, b) => a.time_ms - b.time_ms);
  const leadMs = sorted.length ? sorted[0].time_ms : null;
  const rows = sorted.map((e, i) => ({
    rank: i + 1,
    name: e.name,
    color: e.color || NEUTRAL,
    time_ms: e.time_ms,
    time_str: e.time_str,
    gap_ms: i === 0 ? null : e.time_ms - leadMs,
  }));
  const leader = sorted[0] || null;
  const wrMs = wr ?? null;
  const onFire = isOnFire({ t1: leadMs, t2: sorted[1] ? sorted[1].time_ms : null, wr: wrMs });
  return {
    name: courseName,
    wr_ms: wrMs,
    leader: leader ? { name: leader.name, color: leader.color || NEUTRAL } : null,
    onFire,
    gifUrl: leader ? `${gifBase(leader.name)}.gif` : null,
    fireGifUrl: leader ? `${gifBase(leader.name)}__fire.gif` : null,
    rows,
  };
}

/** Pure: standings ([{player, ms}] from leaderboardAt) + name-keyed colours + wr (record_ms
 *  number|null) -> popup view-model. CoursePopup formats time_ms itself when time_str is absent. */
export function buildCourseView({ standings, colorByName, courseName, wr }) {
  const entries = standings.map((s) => ({
    name: s.player,
    color: colorByName[s.player] || NEUTRAL,
    time_ms: s.ms,
  }));
  return assembleCourseView({ entries, wr, courseName });
}

const j = async (fetchImpl, url) => { const r = await fetchImpl(url); return r.ok ? r.json() : null; };

const gifBlobs = new Map();   // /players/<name>.gif -> Blob, preloaded once and reused across opens

/** Preload every roster player's popup GIFs as in-memory blobs. A blob lets each open spin up
 *  a fresh object URL, which restarts the GIF from frame 1 - a cached <img> with the same src
 *  stays frozen on its last decoded frame. Done once; fire-and-forget. */
export async function preloadPlayerGifs(apiBase, { fetchImpl = fetch } = {}) {
  const roster = (await j(fetchImpl, `${apiBase}/v1/roster`)) || [];
  const urls = [];
  for (const p of roster) { const b = gifBase(p.display_name); urls.push(`${b}.gif`, `${b}__fire.gif`); }
  await Promise.all(urls.map(async (u) => {
    if (gifBlobs.has(u)) return;
    try { const r = await fetchImpl(u); if (r.ok) gifBlobs.set(u, await r.blob()); } catch { /* ignore */ }
  }));
}

/** A fresh object URL for a (preloaded) GIF so it replays from the start; falls back to the
 *  plain path when it isn't preloaded yet. The caller revokes the previous object URL. */
export function freshGifUrl(path) {
  const blob = gifBlobs.get(path);
  return blob && typeof URL !== "undefined" && URL.createObjectURL ? URL.createObjectURL(blob) : path;
}
