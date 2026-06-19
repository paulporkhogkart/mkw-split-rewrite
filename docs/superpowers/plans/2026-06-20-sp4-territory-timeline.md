# SP4 — Territory Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scrubbable, event-stepped *timeline* to the World Map territory view (`web/`) so you can replay how course ownership shifted across the whole competition history — Aliias's June–July 2025 reign giving way to Gub's takeover — painted on the real map.

**Architecture:** A new public server read returns the merged finished-run stream (all seasons, `provenance != 'carryover'`, cc150, ordered by `ended_at`) plus per-player colours. A pure client module replays that stream (running-min per player per course → leader per course) into the sequence of **distinct ownership snapshots** (one per moment the map actually changes). `WorldMap.svelte` gains a timeline mode: it pre-renders each snapshot's territory once via the existing SP2 worker (`buildTerritory`), caches the `ImageBitmap`s, and a self-contained `TimelineScrubber.svelte` (minimal strip below the frame) swaps cached layers — with a subtle crossfade — stepping snapshot-by-snapshot. Default view = present (last snapshot), matching the live `/v1/territory`.

**Tech Stack:** pi server (TS, hono, `node:sqlite`), web SPA (Svelte + Vite, vitest), the SP2 territory pipeline (`web/src/lib/territory.js` + `territoryWorker.js`), headless-Edge visual verification.

## Global Constraints

- **Data is already in the DB** via the Season-0 recovery migration (`pi/src/db/season0Recovery.ts`): S0 holds the real per-PB progression (`source='discord'`), S1 holds carryover + live. The timeline spans both, **excluding `provenance='carryover'`** (those duplicate prior bests at the original time → phantom events).
- **Public reads only:** the timeline endpoint must be **token-free + CORS** like `/v1/territory` (`pi/src/api/app.ts` scopes `hono/cors` + the open-read allowlist). Writes/stats stay gated.
- **cc = 150** only (the sole cc with data).
- **AA always:** territory renders hi-res (asset native 2200px) → high-quality downscale to 1100 (never CSS-upscale a low-res canvas). Reuse the SP2 worker exactly.
- **Verify visuals in headless Edge, never OpenCV** (fresh `--user-data-dir`, `--screenshot`, then Read the PNG). Vite dev serves on **:1430**; pi dev on **:8787** (`MKW_DB=pi/mkw.db`).
- **Player colours** come from the server (`players.color`); do not hardcode. Current roster: Paul `#a78bfa`, Gub `#2dd4bf`, Alex `#fbbf24`, Aliias `#4ade80`, Luke `#f87171`.
- **Scrubber is a self-contained, layout-flexible component** — placement (minimal strip below the frame) is provisional; the final page composition may move it, so it must not assume the map frame.
- **No decorative animation** of the territory fill (the SP2 rule). Timeline *playback* (territory changing as you scrub/play) is the feature; the only motion is the scrub itself + an optional short crossfade between snapshots. No pulses/particles.

---

## File Structure

- `pi/src/db/reads.ts` — **modify**: add `territoryTimeline(db, cc)` (merged finished-run stream + colours).
- `pi/src/db/reads.test.ts` — **modify**: add tests for `territoryTimeline`.
- `pi/src/api/reads.ts` — **modify**: add the `/v1/territory/timeline` handler.
- `pi/src/api/app.ts` — **modify**: register the route in the public/open-read + CORS allowlist.
- `pi/src/api/app.test.ts` — **modify**: assert the endpoint is open (no token) + returns the stream shape.
- `web/src/lib/api.js` — **modify**: add `territoryTimelineUrl(cc = 150)`.
- `web/src/lib/timeline.js` — **create**: pure `buildSnapshots(events)` → distinct ownership snapshots; no DOM.
- `web/src/lib/timeline.test.js` — **create**: unit tests for `buildSnapshots`.
- `web/src/TimelineScrubber.svelte` — **create**: the self-contained minimal-strip scrubber (play/pause, ticked track, knob, entry/date readout, LIVE marker). Props in, events out; no data logic.
- `web/src/WorldMap.svelte` — **modify**: timeline mode — fetch timeline, build snapshots, pre-render+cache each via the worker, mount the scrubber, crossfade-swap on scrub/play.

---

### Task 1: Server — `territoryTimeline` read + public endpoint

**Files:**
- Modify: `pi/src/db/reads.ts` (add `territoryTimeline`)
- Test: `pi/src/db/reads.test.ts`
- Modify: `pi/src/api/reads.ts`, `pi/src/api/app.ts`
- Test: `pi/src/api/app.test.ts`

**Interfaces:**
- Produces: `territoryTimeline(db: DatabaseSync, cc: number): { events: { t: number; player: string; slug: string; ms: number }[]; colors: Record<string, string> }` — `events` = every finished run across all seasons with `provenance != 'carryover'` and `total_time_ms` not null at `cc`, `t` = `Date.parse(ended_at)` (epoch ms), `player` = `players.display_name`, `slug` = `courses.slug`, ordered by `t` ascending; `colors` = `display_name → players.color` for players with a non-null colour. Endpoint `GET /v1/territory/timeline?cc=150` returns this JSON, **token-free + CORS**.

- [ ] **Step 1: Write the failing test** in `pi/src/db/reads.test.ts`

```typescript
import { territoryTimeline } from './reads';
// ...existing imports/harness...

describe('territoryTimeline', () => {
  it('returns finished runs across seasons ordered by time, excluding carryover, with colours', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 0',0),(2,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Aliias','#4ade80'),(2,'Gub','#2dd4bf')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms) VALUES (1,1,1,150,'finished','legacy_import','2025-06-26T00:00:00Z',83000)");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms) VALUES (2,2,1,150,'finished','live','2026-06-10T00:00:00Z',70000)");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms) VALUES (2,1,1,150,'finished','carryover','2025-07-01T00:00:00Z',84000)"); // excluded
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms) VALUES (1,2,1,150,'reset','live','2025-06-27T00:00:00Z',NULL)");       // excluded (not finished)
    const r = territoryTimeline(db, 150);
    expect(r.events).toEqual([
      { t: Date.parse('2025-06-26T00:00:00Z'), player: 'Aliias', slug: 'mario_circuit', ms: 83000 },
      { t: Date.parse('2026-06-10T00:00:00Z'), player: 'Gub', slug: 'mario_circuit', ms: 70000 },
    ]);
    expect(r.colors).toEqual({ Aliias: '#4ade80', Gub: '#2dd4bf' });
  });
});
```

- [ ] **Step 2: Run it; verify it fails** — `cd pi && npx vitest run src/db/reads.test.ts` → FAIL (`territoryTimeline` not exported).

- [ ] **Step 3: Implement `territoryTimeline`** in `pi/src/db/reads.ts`

```typescript
export type TimelineEvent = { t: number; player: string; slug: string; ms: number };
export function territoryTimeline(db: DatabaseSync, cc: number):
    { events: TimelineEvent[]; colors: Record<string, string> } {
  const rows = db.prepare(
    `SELECT p.display_name AS player, c.slug AS slug, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id=r.player_id JOIN courses c ON c.id=r.course_id
     WHERE r.cc=? AND r.status='finished' AND r.provenance!='carryover' AND r.total_time_ms IS NOT NULL
       AND r.ended_at IS NOT NULL
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(cc) as { player: string; slug: string; ms: number; ended_at: string }[];
  const events = rows
    .map((r) => ({ t: Date.parse(r.ended_at), player: r.player, slug: r.slug, ms: r.ms }))
    .filter((e) => Number.isFinite(e.t))
    .sort((a, b) => a.t - b.t);
  const colors: Record<string, string> = {};
  for (const p of db.prepare('SELECT display_name, color FROM players WHERE color IS NOT NULL').all() as any[])
    colors[p.display_name] = p.color;
  return { events, colors };
}
```

- [ ] **Step 4: Run it; verify it passes** — same command → PASS.

- [ ] **Step 5: Add the endpoint** in `pi/src/api/reads.ts` (match the existing `/v1/territory` handler style) and register it open+CORS in `pi/src/api/app.ts` (add `'/v1/territory/timeline'` to the same public-read allowlist + CORS scope as `/v1/territory`). Handler:

```typescript
// in the reads router, mirroring the existing territory handler
app.get('/v1/territory/timeline', (c) => {
  const cc = Number(c.req.query('cc') ?? 150);
  return c.json(territoryTimeline(c.get('db'), cc));   // use the same db accessor the other reads use
});
```

- [ ] **Step 6: Failing test for openness** in `pi/src/api/app.test.ts` (copy the `/v1/territory` open-read assertion): no-token GET → 200 + `access-control-allow-origin: *`. Run → it passes once the route is in the allowlist; if it 401s, the route isn't in the open set — fix the allowlist. (Match exactly how `/v1/territory` is allowlisted.)

- [ ] **Step 7: Run the full pi suite** — `cd pi && npx vitest run` → all green.

- [ ] **Step 8: Commit** — `git add pi/src/db/reads.ts pi/src/db/reads.test.ts pi/src/api/reads.ts pi/src/api/app.ts pi/src/api/app.test.ts && git commit -m "feat(pi): /v1/territory/timeline public read (merged run stream + colours)"`

---

### Task 2: Web — `buildSnapshots` (pure ownership-over-time)

**Files:**
- Create: `web/src/lib/timeline.js`
- Test: `web/src/lib/timeline.test.js`

**Interfaces:**
- Consumes: the endpoint payload `{ events: {t,player,slug,ms}[], colors }`.
- Produces: `buildSnapshots(events, colors) → Snapshot[]` where `Snapshot = { t: number, date: string (YYYY-MM-DD), owners: Record<slug, {player, color}> }`. A snapshot is emitted only when the **leader of some course changes** (the map actually changes); consecutive identical owner-maps are deduped. Owner per course = the player with the running-min `ms` up to and including `t`. Also `buildSnapshots` returns, on each snapshot, `gainColor` (the colour of the course that changed at this `t`, for tick colouring) — when multiple change at one `t`, the last one.

- [ ] **Step 1: Write the failing test** `web/src/lib/timeline.test.js`

```javascript
import { describe, it, expect } from 'vitest';
import { buildSnapshots } from './timeline';

const C = { Aliias: '#4ade80', Gub: '#2dd4bf' };
it('emits a snapshot only when a course leader changes; owner = running-min', () => {
  const events = [
    { t: 1000, player: 'Aliias', slug: 'mc', ms: 90000 },   // Aliias leads mc
    { t: 2000, player: 'Aliias', slug: 'mc', ms: 88000 },   // still Aliias (no leader change) -> no new snapshot
    { t: 3000, player: 'Gub', slug: 'mc', ms: 80000 },      // Gub takes mc
  ];
  const s = buildSnapshots(events, C);
  expect(s.map((x) => x.owners.mc.player)).toEqual(['Aliias', 'Gub']);
  expect(s.map((x) => x.t)).toEqual([1000, 3000]);
  expect(s[1].owners.mc.color).toBe('#2dd4bf');
});
```

- [ ] **Step 2: Run it; verify it fails** — `cd web && npx vitest run src/lib/timeline.test.js` → FAIL (module missing).

- [ ] **Step 3: Implement** `web/src/lib/timeline.js`

```javascript
// Replay the finished-run stream into the sequence of DISTINCT ownership snapshots.
// Owner of a course = the player with the running-minimum time up to that moment.
export function buildSnapshots(events, colors) {
  const best = {};            // slug -> { player -> ms }
  const owner = {};           // slug -> current leader player
  const snaps = [];
  let i = 0;
  while (i < events.length) {
    const t = events[i].t;
    let changed = false, gainColor = null;
    while (i < events.length && events[i].t === t) {
      const e = events[i++];
      const bm = (best[e.slug] = best[e.slug] || {});
      if (bm[e.player] == null || e.ms < bm[e.player]) bm[e.player] = e.ms;
      let lead = null, lo = Infinity;
      for (const p in bm) if (bm[p] < lo) { lo = bm[p]; lead = p; }
      if (lead !== owner[e.slug]) { owner[e.slug] = lead; changed = true; gainColor = colors[lead] || null; }
    }
    if (!changed) continue;
    const owners = {};
    for (const slug in owner) if (owner[slug]) owners[slug] = { player: owner[slug], color: colors[owner[slug]] || null };
    snaps.push({ t, date: new Date(t).toISOString().slice(0, 10), owners, gainColor });
  }
  return snaps;
}
```

- [ ] **Step 4: Run it; verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add web/src/lib/timeline.js web/src/lib/timeline.test.js && git commit -m "feat(web): buildSnapshots — ownership-over-time from the run stream"`

---

### Task 3: Web — `TimelineScrubber.svelte` (self-contained minimal strip)

**Files:**
- Create: `web/src/TimelineScrubber.svelte`

**Interfaces:**
- Consumes: props `snapshots: Snapshot[]`, `index: number`, `playing: boolean`. Events: `on:scrub` (detail `{ index }`), `on:toggle` (play/pause). No data logic, no map knowledge — purely presentational, so it can be placed anywhere.
- Produces: the rendered strip — a play/pause button, a track with one faint tick per snapshot (coloured by `snapshots[i].gainColor` when "owner-colored", per the brainstorm; expose a `coloredTicks` prop default true), a draggable knob at `index`, a readout (`snapshots[index].date` + `LIVE` pill when `index === snapshots.length-1`), and span end-labels (first/last date). Graphite tokens from `src/theme.css` (accent `#3d7cc2`, `--feed-bg`, 4px radii); tabular-mono for the date.

- [ ] **Step 1: Build the component** — model the markup/CSS on the validated review-tool strip (`temp/history_review.html` scrubber section) and the SP1 frame tokens. Slider `min=0 max={snapshots.length-1} value={index}`; `on:input` → `dispatch('scrub', { index: +e.target.value })`; play button → `dispatch('toggle')`. Ticks: render `snapshots.map((s,i) => <div class="tick" style="left:{i/(n-1)*100}%;--c:{s.gainColor}">)`. Keep all colour/size literals from the design.

- [ ] **Step 2: Verify in headless Edge** — temporarily mount it in `WorldMap.svelte` (or a scratch route) with fake snapshots, `npm --prefix web run dev`, screenshot `http://localhost:1430/#/map`, Read the PNG: confirm the strip renders (play button, ticks, knob, date, LIVE) in the graphite style. Revert the scratch mount.

- [ ] **Step 3: Commit** — `git add web/src/TimelineScrubber.svelte && git commit -m "feat(web): TimelineScrubber — self-contained minimal-strip scrubber"`

---

### Task 4: Web — WorldMap timeline mode (fetch → build → pre-render/cache)

**Files:**
- Modify: `web/src/lib/api.js` (add `territoryTimelineUrl`)
- Modify: `web/src/WorldMap.svelte`

**Interfaces:**
- Consumes: `territoryTimelineUrl()`, `buildSnapshots`, the existing `territoryWorker.js` + `buildTerritory` (unchanged), `manifest.courses`, `island.png`, `base.jpg`.
- Produces: a cached `ImageBitmap[]` parallel to `snapshots` (one territory layer per snapshot), and `renderSnapshot(i)` that draws `cache[i]` to the `.territory` canvas.

- [ ] **Step 1: Add `territoryTimelineUrl`** to `web/src/lib/api.js`

```javascript
export const territoryTimelineUrl = (cc = 150) => `${API_BASE}/v1/territory/timeline?cc=${cc}`;
```

- [ ] **Step 2: Fetch + build snapshots on mount** in `WorldMap.svelte` — after `manifest` loads (reuse the existing `await tick()` gotcha so `terrCanvas` is bound), fetch the timeline, `snapshots = buildSnapshots(events, colors)`. Guard empty.

- [ ] **Step 3: Pre-render + cache each snapshot's bitmap via the worker.** Reuse the existing worker call shape (`renderTerritory`): load `island.png` + `base.jpg` bitmaps **once**; for each snapshot, post `{coverageBitmap, baseBitmap, W, H, manifestCourses, territoryRows}` where `territoryRows = Object.entries(owners).map(([slug,o]) => ({slug, color: o.color}))`; collect the returned `ImageBitmap` into `cache[i]`. Render sequentially (await each) with a small "building timeline…" progress state; the bitmaps are ≤~80, well within budget. **Perf note:** if pre-render is slow, optimize inside the worker by computing the fixed 30-course nearest field once and remapping owners per snapshot (course centres never move) — but measure first; ≤80 plain `buildTerritory` calls is likely fine.
  - Keep the existing one-shot live render as the fallback when the timeline endpoint is unavailable.

- [ ] **Step 4: Verify caching** — `npm --prefix web run dev` (+ `npm --prefix pi run dev` on :8787 reading `pi/mkw.db`), open `#/map`, confirm in DevTools that N bitmaps render without error and the map shows the present (last snapshot) territory matching the live `/v1/territory`. Headless-Edge screenshot the default state.

- [ ] **Step 5: Commit** — `git add web/src/lib/api.js web/src/WorldMap.svelte && git commit -m "feat(web): WorldMap pre-renders + caches per-snapshot territory bitmaps"`

---

### Task 5: Web — scrubber wiring, crossfade swap, play/pause

**Files:**
- Modify: `web/src/WorldMap.svelte`

**Interfaces:**
- Consumes: `TimelineScrubber.svelte`, the `cache[]` from Task 4.

- [ ] **Step 1: Two-layer crossfade.** Make `.territory` two stacked canvases (`terrA`/`terrB`, both `position:absolute; inset:0`); `showSnapshot(i, animate)` draws `cache[i]` to the inactive layer and crossfades opacity over ~280ms (CSS `transition: opacity .28s` toggled by a `.cross` class; for a hard step or initial render, no transition). Default `index = snapshots.length-1` (present).

- [ ] **Step 2: Mount the scrubber + wire events.** Render `<TimelineScrubber {snapshots} {index} {playing} on:scrub={e => showSnapshot(e.detail.index, false)} on:toggle={togglePlay} />` in a minimal strip below the `.frame`. `showSnapshot` updates `index`.

- [ ] **Step 3: Play/pause.** `togglePlay` starts a stepper (`setTimeout` ~700ms per snapshot) advancing `index` with `showSnapshot(i, true)` (crossfade); stop at the last snapshot (park at LIVE); restarting from the end replays from 0. No territory-fill animation beyond the crossfade.

- [ ] **Step 4: Verify the replay in headless Edge.** Screenshot at an early `index` (set the slider) and at the end: confirm Aliias's green dominating the islands early and Gub's teal at present. Confirm the icons stay on top, ocean visible, AA coast intact (the SP2 look is preserved since the worker is unchanged).

- [ ] **Step 5: `svelte-check`** — `npm --prefix web run check` → 0 errors / 0 warnings. Then commit — `git add web/src/WorldMap.svelte && git commit -m "feat(web): timeline scrubber + crossfade replay on the territory map"`

---

## Verification (whole feature)

- `cd pi && npx vitest run` → green; `cd web && npx vitest run` → green; `npm --prefix web run check` → 0/0.
- With `pi` dev (:8787, `MKW_DB=pi/mkw.db`) + `web` dev (:1430): open `#/map`, scrub from June 2025 → now. The territory must visibly shift (Aliias green reign → Gub teal takeover), default at present matching `/v1/territory`, icons/popups (SP3) still working. Capture the early-reign + present states via headless Edge and eyeball (never OpenCV).
- The recent-end caveat: the merged stream's present ≈ but not exactly the sheet on ~2 courses; acceptable for the timeline (the live `/v1/territory` remains the canonical "now" panel).

## Notes / open tweaks (safe to defer)

- **Transition style** (hard cut vs crossfade) and **playback cadence** were left as live-tune items in the brainstorm; default to the subtle crossfade + ~700ms/step and adjust on the user's eye.
- **Strength/dominance** colouring (modulate tint/rim by `fireModel.dominance`) stays deferred (SP2 note).
- **Placement** of the scrubber is provisional (minimal strip); it's a standalone component so the final page composition can relocate it.
