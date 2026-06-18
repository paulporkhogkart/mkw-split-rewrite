// Assembles + fetches the hover-popup view-model for a course: leaderboard rows
// (with per-row colour + gap-to-#1), WR, on-fire flag, and the leader's GIF urls.
import { isOnFire } from "./fireModel.js";

const NEUTRAL = "#888";
const gifBase = (name) => `/players/${(name || "").toLowerCase()}`;

/** Pure: raw rows + wr + colour map -> popup view-model. */
export function buildCourseView({ rows, wr, colorById, courseName }) {
  const sorted = [...rows].sort((a, b) => a.total_time_ms - b.total_time_ms);
  const leadMs = sorted.length ? sorted[0].total_time_ms : null;
  const viewRows = sorted.map((r, i) => ({
    rank: i + 1,
    name: r.display_name,
    color: colorById[r.player_id] || NEUTRAL,
    time_ms: r.total_time_ms,
    time_str: r.total_time_str,
    gap_ms: i === 0 ? null : r.total_time_ms - leadMs,
  }));
  const leader = sorted[0];
  const wrMs = wr && wr.record_ms != null ? wr.record_ms : null;
  const onFire = isOnFire({ t1: leadMs, t2: sorted[1] ? sorted[1].total_time_ms : null, wr: wrMs });
  return {
    name: courseName,
    wr_ms: wrMs,
    leader: leader ? { name: leader.display_name, color: colorById[leader.player_id] || NEUTRAL } : null,
    onFire,
    gifUrl: leader ? `${gifBase(leader.display_name)}.gif` : null,
    fireGifUrl: leader ? `${gifBase(leader.display_name)}__fire.gif` : null,
    rows: viewRows,
  };
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
