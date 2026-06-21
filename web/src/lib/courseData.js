// Assembles + fetches the hover-popup view-model for a course: leaderboard rows
// (with per-row colour + gap-to-#1), WR, on-fire flag, and the leader's GIF urls.
import { isOnFire } from "./fireModel.js";

const NEUTRAL = "#888";
const gifBase = (name) => `/players/${(name || "").toLowerCase()}`;

/** Internal: normalized entries [{name, color, time_ms, time_str?}] -> popup view-model.
 *  Sorts by time, ranks, computes gap-to-#1, on-fire, and the leader's gif urls. */
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
  const wrMs = wr && wr.record_ms != null ? wr.record_ms : null;
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

/** Pure: raw leaderboard rows (player_id-keyed) + wr + colour map -> popup view-model. */
export function buildCourseView({ rows, wr, colorById, courseName }) {
  const entries = rows.map((r) => ({
    name: r.display_name,
    color: colorById[r.player_id] || NEUTRAL,
    time_ms: r.total_time_ms,
    time_str: r.total_time_str,
  }));
  return assembleCourseView({ entries, wr, courseName });
}

/** Pure: historical standings ([{player, ms}] from leaderboardAt) + name-keyed colours + wr
 *  -> popup view-model. Same shape as buildCourseView; CoursePopup formats time_ms itself. */
export function buildHistoricalCourseView({ standings, colorByName, courseName, wr }) {
  const entries = standings.map((s) => ({
    name: s.player,
    color: colorByName[s.player] || NEUTRAL,
    time_ms: s.ms,
  }));
  return assembleCourseView({ entries, wr, courseName });
}

const j = async (fetchImpl, url) => { const r = await fetchImpl(url); return r.ok ? r.json() : null; };

/** Roster colour map {player_id: color}, fetched once and cached on the returned fn. */
export async function fetchColorById(apiBase, { fetchImpl = fetch } = {}) {
  if (fetchColorById._cache) return fetchColorById._cache;
  const roster = (await j(fetchImpl, `${apiBase}/v1/roster`)) || [];
  const map = {};
  for (const p of roster) if (p.color) map[p.player_id] = p.color;
  fetchColorById._cache = map;
  return map;
}

const wrCache = new Map();

/** Current WR for a course (the raw { record_ms } row, or null), cached per slug. The historical
 *  hover popup has no historical WR yet, so it shows the current WR. Swap this for a time-indexed
 *  lookup once historical WRs are scraped. */
export async function fetchCourseWr(apiBase, course, { fetchImpl = fetch } = {}) {
  if (wrCache.has(course.slug)) return wrCache.get(course.slug);
  const wr = await j(fetchImpl, `${apiBase}/v1/world-records?course=${encodeURIComponent(course.slug)}&cc=150`);
  wrCache.set(course.slug, wr);
  return wr;
}

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

const viewCache = new Map();

/** Fetch + assemble a course's view-model (cached per slug for the session). */
export async function fetchCourseView(apiBase, course, { fetchImpl = fetch } = {}) {
  if (viewCache.has(course.slug)) return viewCache.get(course.slug);
  const q = `course=${encodeURIComponent(course.slug)}&cc=150`;
  const [rows, wr, colorById] = await Promise.all([
    j(fetchImpl, `${apiBase}/v1/leaderboard?${q}`),
    j(fetchImpl, `${apiBase}/v1/world-records?${q}`),
    fetchColorById(apiBase, { fetchImpl }),
  ]);
  const view = buildCourseView({ rows: Array.isArray(rows) ? rows : [], wr, colorById, courseName: course.name });
  viewCache.set(course.slug, view);
  return view;
}
