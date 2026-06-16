# Player Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render one live "timing tower" card per Season-1 player in the monitor's reserved band, driven by the `presence` store.

**Architecture:** Server enriches each presence entry with `resets` (passed through from the app) and the player's `pb_ms` for the current course (looked up from `runs.is_pb`). The frontend maps a `PresenceEntry` to a pure view-model (`playerCard.js`), which `PlayerCard.svelte` renders; `PlayerPanel.svelte` lays one card per roster entry into the band. Figure cut-outs are pre-extracted from gifs by a Python/Pillow prep script into bundled PNGs.

**Tech Stack:** TypeScript + `node:sqlite` (server, vitest), Svelte 4 + Vite (frontend, vitest), Python + Pillow (asset prep). Frontend tests: `npm run test:js`. Server tests: `npm --prefix pi test`. Types: `npm run check`. Build: `npm run build`.

**Spec:** `docs/superpowers/specs/2026-06-09-player-panel-design.md`

---

## File Structure

- **Create** `pi/src/db/pb.ts` → add `pbMsFor()` reader (PB time for season+player+course+cc).
- **Modify** `pi/src/presence/hub.ts` → `PresenceFrame`+`resets`; `PresenceEntry`+`resets,pb_ms`; seed `updated_at=0`; `update()` passes resets + enriches `pb_ms`.
- **Modify** `src/lib/presence.js` → `frame()` includes `resets`.
- **Create** `src/lib/playerCard.js` → pure view-model + formatters (unit-tested).
- **Create** `scripts/gen_player_figures.py` → extract on/off frames → `src/assets/players/*.png`.
- **Create** `assets/player_gifs/*.gif` (source, Git LFS) + `.gitattributes` rule.
- **Create** `src/lib/playerFigures.js` → name→figure-URL map (vite glob).
- **Create** `src/components/PlayerCard.svelte` and `src/components/PlayerPanel.svelte`.
- **Modify** `src/App.svelte` → mount `<PlayerPanel/>` in `.player-band`.

---

## Task 1: Server — `pbMsFor` PB reader

**Files:**
- Modify: `pi/src/db/pb.ts`
- Test: `pi/src/db/pb.test.ts`

- [ ] **Step 1: Write the failing test** — append to `pi/src/db/pb.test.ts`:

```ts
import { pbMsFor } from './pb';

describe('pbMsFor', () => {
  it('returns the is_pb run time for the scope, else null', () => {
    const d = new DatabaseSync(':memory:'); applySchema(d);
    d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
            INSERT INTO players(id,display_name) VALUES(1,'P');
            INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(1,1,7,150,'finished',83000,0,'live'),(1,1,7,150,'finished',79880,1,'live');`);
    expect(pbMsFor(d, 1, 1, 7, 150)).toBe(79880);
    expect(pbMsFor(d, 1, 1, 999, 150)).toBeNull();
  });
});
```

> `pi/src/db/pb.test.ts` already imports `DatabaseSync` (`node:sqlite`) and `applySchema` (`./connect`) at the top — reuse them. (`courses` is `(id, slug, display_name)`; there is no `name` column.)

- [ ] **Step 2: Run the test — verify it fails**

Run: `npm --prefix pi test -- src/db/pb.test.ts`
Expected: FAIL — `pbMsFor is not a function`.

- [ ] **Step 3: Implement** — append to `pi/src/db/pb.ts`:

```ts
/** Total time (ms) of the player's PB run for a (season,player,course,cc) scope, or null. */
export function pbMsFor(db: DatabaseSync, seasonId: number, playerId: number, courseId: number, cc: number): number | null {
  const row = db.prepare(
    "SELECT total_time_ms FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND is_pb=1 LIMIT 1"
  ).get(seasonId, playerId, courseId, cc) as { total_time_ms: number } | undefined;
  return row ? row.total_time_ms : null;
}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `npm --prefix pi test -- src/db/pb.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/pb.ts pi/src/db/pb.test.ts
git commit -m "feat(presence): pbMsFor reader for current-course PB lookup"
```

---

## Task 2: Server — resets passthrough + PB enrichment in `PresenceHub`

**Files:**
- Modify: `pi/src/presence/hub.ts`
- Test: `pi/src/presence/hub.test.ts`

- [ ] **Step 1: Write the failing test** — append a new `it` inside the `describe('PresenceHub', …)` block in `pi/src/presence/hub.test.ts`:

```ts
  it('passes resets through and enriches pb_ms for the current course', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(1,1,7,150,'finished',79880,1,'live');`);
    const hub = new PresenceHub(d, () => null, () => 5000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', resets: 3 });
    expect(got.at(-1).player).toMatchObject({ player_id: 1, resets: 3, pb_ms: 79880 });
  });

  it('seeds offline entries with updated_at 0 (never seen)', () => {
    const hub = new PresenceHub(db(), noCompletion, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].players[0].updated_at).toBe(0);
  });
```

- [ ] **Step 2: Run the test — verify it fails**

Run: `npm --prefix pi test -- src/presence/hub.test.ts`
Expected: FAIL — `resets`/`pb_ms` undefined and `updated_at` is `1000`.

- [ ] **Step 3: Implement** — edit `pi/src/presence/hub.ts`:

(a) Add imports under the existing imports at the top:

```ts
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { pbMsFor } from '../db/pb';
```

(b) Add the two fields to `PresenceFrame` (the `resets` line) and `PresenceEntry`:

```ts
// in PresenceFrame:
  resets?: number | null;
// in PresenceEntry (after `mushrooms`):
  resets: number | null; pb_ms: number | null;
```

(c) Update `offlineEntry` to include the new fields:

```ts
function offlineEntry(player_id: number, name: string, color: string | null, now: number): PresenceEntry {
  return { player_id, name, color, online: false, screen: null, course: null, character: null, kart: null,
           costume: null, cur_lap: null, tot_lap: null, coins: null, mushrooms: null, resets: null,
           pb_ms: null, completion: null, final_time: null, updated_at: now };
}
```

(d) In `seedRoster`, seed with `updated_at = 0` (never seen):

```ts
    for (const r of rows) if (!this.map.has(r.id)) this.map.set(r.id, offlineEntry(r.id, r.display_name, r.color, 0));
```

(e) In `update`, add `resets` + `pb_ms` to the built `entry`. Replace the `entry` object with:

```ts
    const entry: PresenceEntry = {
      player_id: playerId, name: cur.name, color: cur.color, online: true,
      screen: frame.screen ?? null, course: frame.course ?? null,
      character: frame.character ?? null, kart: frame.kart ?? null, costume: frame.costume ?? null,
      cur_lap: frame.cur_lap ?? null, tot_lap: frame.tot_lap ?? null,
      coins: frame.coins ?? null, mushrooms: frame.mushrooms ?? null, resets: frame.resets ?? null,
      completion: this.completion(frame.course, frame.cur_lap, frame.pos),
      pb_ms: this.pbForCourse(playerId, frame.course),
      final_time: frame.final_time ?? null, updated_at: this.now(),
    };
```

(f) Add a private helper method to the `PresenceHub` class (next to `broadcast`):

```ts
  private pbForCourse(playerId: number, course: string | null | undefined): number | null {
    if (!course) return null;
    const courseId = courseIdBySlug(this.db, slugify(course));
    if (courseId == null) return null;
    return pbMsFor(this.db, activeSeasonId(this.db), playerId, courseId, 150);
  }
```

- [ ] **Step 4: Run the test — verify it passes (and nothing regressed)**

Run: `npm --prefix pi test`
Expected: PASS — all server tests green (count increases by the two new cases).

- [ ] **Step 5: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(presence): enrich entries with resets + current-course pb_ms; seed updated_at=0"
```

---

## Task 3: Frontend — `resets` in the presence frame

**Files:**
- Modify: `src/lib/presence.js`
- Test: `src/lib/presence.test.js`

- [ ] **Step 1: Update the failing test** — in `src/lib/presence.test.js`, add the `resets` store import and a set + assertion. Change the import line:

```js
import { screen, selection, race, minimap } from "./stores.js";
import { resets } from "./resets.js";
```

In the "maps the live stores into a frame" test, add before the assert: `resets.set(4);` and add `resets: 4,` to the expected object.

- [ ] **Step 2: Run the test — verify it fails**

Run: `npm run test:js -- src/lib/presence.test.js`
Expected: FAIL — frame has no `resets` key.

- [ ] **Step 3: Implement** — in `src/lib/presence.js`:

Add the import (with the other store import):

```js
import { resets } from "./resets.js";
```

Add `resets` to the returned frame in `frame()`:

```js
    pos: mm ? [mm.cx, mm.cy] : null, final_time: r.finishTime, resets: get(resets),
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `npm run test:js -- src/lib/presence.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/presence.js src/lib/presence.test.js
git commit -m "feat(presence): send resets in the frontend presence frame"
```

---

## Task 4: Frontend — `playerCard.js` view-model + formatters

**Files:**
- Create: `src/lib/playerCard.js`
- Test: `src/lib/playerCard.test.js`

- [ ] **Step 1: Write the failing test** — create `src/lib/playerCard.test.js`:

```js
import { describe, it, expect } from "vitest";
import { viewModel, lapSegments, lastSeen, pbDelta, fmtTimeMs } from "./playerCard.js";

const base = { player_id: 1, name: "Paul", color: "#a78bfa", online: true, screen: "RACING",
  course: "Rainbow Road", character: "Mario", kart: "Standard", cur_lap: 2, tot_lap: 3,
  coins: 7, mushrooms: 2, resets: 3, pb_ms: 79880, completion: 0.63, final_time: null, updated_at: 1000 };

describe("fmtTimeMs", () => {
  it("formats ms as m:ss.SSS", () => {
    expect(fmtTimeMs(79880)).toBe("1:19.880");
    expect(fmtTimeMs(2044)).toBe("0:02.044");
    expect(fmtTimeMs(null)).toBeNull();
  });
});

describe("lapSegments", () => {
  it("splits completion across laps", () => {
    const s = lapSegments(0.63, 3);
    expect(s.length).toBe(3);
    expect(s[0]).toBe(1);
    expect(s[1]).toBeCloseTo(0.89, 2);
    expect(s[2]).toBe(0);
  });
  it("defaults to 3 segments when tot_lap is missing", () => {
    expect(lapSegments(0, null).length).toBe(3);
  });
});

describe("lastSeen", () => {
  it("buckets a delta", () => {
    expect(lastSeen(5000)).toBe("just now");
    expect(lastSeen(120000)).toBe("2m ago");
    expect(lastSeen(3 * 3600000)).toBe("3h ago");
    expect(lastSeen(2 * 86400000)).toBe("2d ago");
  });
});

describe("pbDelta", () => {
  it("signs the delta vs PB", () => {
    expect(pbDelta("1:21.044", 79880)).toEqual({ text: "+1.16", cls: "slow" });
    expect(pbDelta("1:18.880", 79880)).toEqual({ text: "-1.00", cls: "fast" });
    expect(pbDelta(null, 79880)).toBeNull();
  });
});

describe("viewModel", () => {
  it("racing: time is dashes, race cluster populated", () => {
    const vm = viewModel(base, () => 2000);
    expect(vm.state).toBe("racing");
    expect(vm.primary).toEqual({ kind: "time", text: "—" });
    expect(vm.resets).toBe(3);
    expect(vm.pbStr).toBe("1:19.880");
    expect(vm.dotPct).toBeCloseTo(63, 0);
    expect(vm.segments.length).toBe(3);
  });
  it("setup: activity phrase, no race cluster", () => {
    const vm = viewModel({ ...base, screen: "KART_SELECT" }, () => 2000);
    expect(vm.state).toBe("setup");
    expect(vm.primary).toEqual({ kind: "activity", text: "Choosing kart" });
    expect(vm.segments).toBeNull();
  });
  it("finished: final time + delta, full bar, no dot", () => {
    const vm = viewModel({ ...base, final_time: "1:21.044" }, () => 2000);
    expect(vm.state).toBe("finished");
    expect(vm.primary).toEqual({ kind: "time", text: "1:21.044" });
    expect(vm.delta).toEqual({ text: "+1.16", cls: "slow" });
    expect(vm.dotPct).toBeNull();
  });
  it("offline seen: last seen line; never-seen: plain offline", () => {
    const seen = viewModel({ ...base, online: false, updated_at: 1000 }, () => 1000 + 3 * 3600000);
    expect(seen.state).toBe("offline");
    expect(seen.primary).toEqual({ kind: "seen", text: "last seen 3h ago" });
    expect(seen.char).toBeNull();
    const never = viewModel({ ...base, online: false, updated_at: 0 }, () => 5000);
    expect(never.primary).toEqual({ kind: "seen", text: "offline" });
  });
});
```

- [ ] **Step 2: Run the test — verify it fails**

Run: `npm run test:js -- src/lib/playerCard.test.js`
Expected: FAIL — cannot import from `./playerCard.js`.

- [ ] **Step 3: Implement** — create `src/lib/playerCard.js`:

```js
// Pure mapping: a presence entry -> the player-card view model, plus formatters.
// No Svelte/Tauri imports, so it's unit-testable. See docs/superpowers/specs/2026-06-09-player-panel-design.md.
import { parseTime } from "./discordFormat.js";

const SETUP = { CHARACTER_SELECT: "Choosing character", KART_SELECT: "Choosing kart", COURSE_SELECT: "Choosing track" };

/** ms -> "m:ss.SSS" (always shows minutes), or null. */
export function fmtTimeMs(ms) {
  if (ms == null || Number.isNaN(ms)) return null;
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), msec = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(msec).padStart(3, "0")}`;
}

/** completion (0..1) over `totLap` laps -> array of per-lap fill fractions (defaults to 3 laps). */
export function lapSegments(completion, totLap) {
  const n = totLap && totLap > 0 ? totLap : 3;
  const out = [];
  for (let i = 0; i < n; i++) {
    const lo = i / n, hi = (i + 1) / n;
    out.push(completion == null ? 0 : completion <= lo ? 0 : completion >= hi ? 1 : (completion - lo) / (hi - lo));
  }
  return out;
}

/** elapsed ms since last seen -> coarse relative label, or null. */
export function lastSeen(deltaMs) {
  if (deltaMs == null) return null;
  const s = Math.floor(deltaMs / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** final time string vs PB ms -> { text:"+1.16"|"-1.00", cls:"slow"|"fast" } or null. */
export function pbDelta(finalStr, pbMs) {
  if (!finalStr || pbMs == null) return null;
  const f = parseTime(finalStr);
  if (f == null) return null;
  const d = f - pbMs, ahead = d < 0;
  return { text: `${ahead ? "-" : "+"}${(Math.abs(d) / 1000).toFixed(2)}`, cls: ahead ? "fast" : "slow" };
}

/** A presence entry -> the card view model. `now` is a fn (Date.now) for testability. */
export function viewModel(e, now = Date.now) {
  const t = typeof now === "function" ? now() : now;
  const color = e.color || "#888";
  if (!e.online) {
    const seen = e.updated_at > 0 ? lastSeen(t - e.updated_at) : null;
    return { state: "offline", name: e.name, color, online: false, char: null, kart: null, trk: null,
      primary: { kind: "seen", text: seen ? `last seen ${seen}` : "offline" },
      resets: null, pbStr: null, delta: null, segments: null, dotPct: null };
  }
  const racing = e.screen === "RACING" && !e.final_time;
  const finished = (e.screen === "RACING" && e.final_time) || e.screen === "POST_TIME_TRIAL";
  let state, primary;
  if (SETUP[e.screen]) { state = "setup"; primary = { kind: "activity", text: SETUP[e.screen] }; }
  else if (racing) { state = "racing"; primary = { kind: "time", text: "—" }; }
  else if (finished) { state = "finished"; primary = { kind: "time", text: e.final_time }; }
  else { state = "menus"; primary = { kind: "activity", text: "In the menus" }; }
  const race = state === "racing" || state === "finished";
  return {
    state, name: e.name, color, online: true,
    char: e.character || null, kart: e.kart || null, trk: e.course || null, primary,
    resets: race ? (e.resets ?? 0) : null,
    pbStr: race && e.pb_ms != null ? fmtTimeMs(e.pb_ms) : null,
    delta: state === "finished" ? pbDelta(e.final_time, e.pb_ms) : null,
    segments: race ? lapSegments(e.completion, e.tot_lap) : null,
    dotPct: state === "racing" && e.completion != null && e.completion > 0 && e.completion < 1 ? e.completion * 100 : null,
  };
}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `npm run test:js -- src/lib/playerCard.test.js`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/lib/playerCard.js src/lib/playerCard.test.js
git commit -m "feat(player-panel): pure view-model + formatters for the card"
```

---

## Task 5: Figure assets — source gifs (LFS) + extraction script

**Files:**
- Create: `assets/player_gifs/*.gif` (copied from `temp/360/`)
- Modify: `.gitattributes`
- Create: `scripts/gen_player_figures.py`
- Create: `src/assets/players/*.png` (generated)

- [ ] **Step 1: Ensure Pillow is available**

Run: `python -m pip install pillow`
Expected: "Requirement already satisfied" or a successful install.

- [ ] **Step 2: Stage the source gifs**

```bash
mkdir -p assets/player_gifs src/assets/players
cp temp/360/paulPosted.gif temp/360/aliiasPosted.gif temp/360/aliiasBird.gif \
   temp/360/lukePosted.gif temp/360/lukeThumbsUp.gif temp/360/gubPosted.gif assets/player_gifs/
ls assets/player_gifs/
```
Expected: the six gifs listed.

- [ ] **Step 3: Add the LFS rule** — append to `.gitattributes`:

```
assets/player_gifs/*.gif filter=lfs diff=lfs merge=lfs -text
```

- [ ] **Step 4: Write the extraction script** — create `scripts/gen_player_figures.py`:

```python
"""Extract per-player figure frames from source gifs into bundled PNGs.

Online frame = a late ('end', ~88%) frame of the player's online gif.
Offline frame = an early ('start', first frame >10% opaque) frame of the offline gif.
Both: alpha preserved, cropped to the figure's bounding box, resized to <=260px tall.
Greyscale for offline is applied in CSS at render time, not baked in here.

Run: python scripts/gen_player_figures.py
"""
import os
from PIL import Image

SRC = os.path.join(os.path.dirname(__file__), "..", "assets", "player_gifs")
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "assets", "players")

# player (lowercased) -> (online_gif, offline_gif). Alex has no art -> borrows Gub's.
MAP = {
    "paul":   ("paulPosted.gif",   "paulPosted.gif"),
    "aliias": ("aliiasPosted.gif", "aliiasBird.gif"),
    "luke":   ("lukePosted.gif",   "lukeThumbsUp.gif"),
    "gub": ("gubPosted.gif", "gubPosted.gif"),
    "alex":   ("gubPosted.gif", "gubPosted.gif"),
}

def frame(path, end, h=260):
    im = Image.open(path); n = getattr(im, "n_frames", 1)
    if end:
        idx = int(n * 0.88)
    else:
        idx = 0
        for i in range(n):
            im.seek(i)
            if im.convert("RGBA").getchannel("A").histogram()[255] / (im.width * im.height) > 0.10:
                idx = i; break
    im.seek(idx); fr = im.convert("RGBA")
    bb = fr.getchannel("A").getbbox()
    if bb: fr = fr.crop(bb)
    if fr.height > h: fr = fr.resize((round(fr.width * h / fr.height), h), Image.LANCZOS)
    return fr

def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (on_gif, off_gif) in MAP.items():
        for suffix, gif, end in (("on", on_gif, True), ("off", off_gif, False)):
            out = os.path.join(OUT, f"{name}__{suffix}.png")
            frame(os.path.join(SRC, gif), end).save(out, "PNG", optimize=True)
            print("wrote", os.path.relpath(out))

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run it and verify output**

Run: `python scripts/gen_player_figures.py`
Expected: prints `wrote src/assets/players/paul__on.png` … 10 lines (on+off for 5 players).

Run: `python -c "from PIL import Image; im=Image.open('src/assets/players/paul__on.png'); print(im.mode, im.size, im.getchannel('A').getextrema())"`
Expected: `RGBA (W, 260) (0, 255)` — i.e. transparency preserved.

- [ ] **Step 6: Commit**

```bash
git add .gitattributes assets/player_gifs scripts/gen_player_figures.py src/assets/players
git commit -m "feat(player-panel): source gifs (LFS) + figure-frame extraction script"
```

---

## Task 6: Frontend — `playerFigures.js` figure-URL map

**Files:**
- Create: `src/lib/playerFigures.js`

> No unit test: this module only resolves bundled asset URLs via Vite's `import.meta.glob` (not available under the vitest node env). It's exercised by `npm run build` in Task 8.

- [ ] **Step 1: Implement** — create `src/lib/playerFigures.js`:

```js
// Maps a player name -> { on, off } figure URLs, bundled by Vite from src/assets/players/.
// Filenames are <name>__on.png / <name>__off.png (see scripts/gen_player_figures.py).
const mods = import.meta.glob("../assets/players/*.png", { eager: true, query: "?url", import: "default" });

const map = {};
for (const [path, url] of Object.entries(mods)) {
  const m = /\/([a-z0-9]+)__(on|off)\.png$/.exec(path);
  if (m) (map[m[1]] ??= {})[m[2]] = url;
}

/** Figure URL for a player by display name + online state; null when none is bundled. */
export function figureFor(name, online) {
  const e = map[(name || "").toLowerCase()] || {};
  return (online ? e.on : e.off) || e.on || e.off || null;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/lib/playerFigures.js
git commit -m "feat(player-panel): bundled figure-URL resolver"
```

---

## Task 7: `PlayerCard.svelte`

**Files:**
- Create: `src/components/PlayerCard.svelte`

> No unit test (Svelte component). Logic is in `playerCard.js` (Task 4, tested). Verified by `npm run check` + `npm run build` (Task 8).

- [ ] **Step 1: Implement** — create `src/components/PlayerCard.svelte`:

```svelte
<script>
  import { viewModel } from "../lib/playerCard.js";
  import { figureFor } from "../lib/playerFigures.js";
  export let entry;
  $: vm = viewModel(entry, Date.now());
  $: fig = figureFor(vm.name, vm.online);
</script>

<div class="tt" class:off={!vm.online} style="--pc:{vm.color}">
  <div class="spine"></div>
  {#if fig}<div class="fig" style="background-image:url({fig})"></div>{/if}
  <div class="data">
    <div class="nm">{vm.name}</div>
    <div class="sel">
      <div class="kv" class:dim={!vm.char}><span>CHR</span>{vm.char || "—"}</div>
      <div class="kv" class:dim={!vm.kart}><span>KRT</span>{vm.kart || "—"}</div>
      <div class="kv" class:dim={!vm.trk}><span>TRK</span>{vm.trk || "—"}</div>
    </div>
    <div class="sp"></div>
    {#if vm.resets != null}
      <div class="foot"><span class="rk">RESETS</span><b>{vm.resets}</b></div>
    {/if}
    {#if vm.pbStr}
      <div class="pb"><span>PB</span>{vm.pbStr}{#if vm.delta}<span class="delta {vm.delta.cls}">{vm.delta.text}</span>{/if}</div>
    {/if}
    {#if vm.primary.kind === "time"}
      <div class="prim time" class:fin={vm.state === "finished"}>{vm.primary.text}</div>
    {:else if vm.primary.kind === "activity"}
      <div class="prim act">{vm.primary.text}</div>
    {:else}
      <div class="prim seen">{vm.primary.text}</div>
    {/if}
    {#if vm.segments}
      <div class="barwrap">
        <div class="lapbar">
          {#each vm.segments as f}<span class="seg"><i style="width:{f * 100}%"></i></span>{/each}
        </div>
        {#if vm.dotPct != null}<span class="live" style="left:{vm.dotPct}%"></span>{/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .tt { --pc: #888; position: relative; height: 100%; min-height: 146px; background: var(--panel);
        overflow: hidden; }
  .tt.off { background: var(--well); }
  .spine { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--pc); }
  .tt.off .spine { background: var(--idle); }
  .fig { position: absolute; left: 6px; bottom: 0; top: 11px; width: 33%; background-repeat: no-repeat;
         background-position: bottom center; background-size: auto 100%; }
  .tt.off .fig { filter: grayscale(1) brightness(.6); }
  .data { position: absolute; left: 39%; right: 0; top: 0; bottom: 0; padding: 9px 9px 8px;
          display: flex; flex-direction: column; }
  .nm { font-size: 12px; font-weight: 700; color: var(--pc); letter-spacing: .05em; text-transform: uppercase;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tt.off .nm { color: var(--tx-mut); }
  .sel { margin-top: 6px; }
  .kv { font-size: 10px; color: var(--tx); display: flex; gap: 7px; line-height: 1.45; }
  .kv span { color: var(--tx-dim); font-size: 7.5px; letter-spacing: .08em; width: 18px; flex: 0 0 auto; padding-top: 2px; }
  .kv.dim { color: var(--tx-dim); }
  .sp { flex: 1; }
  .foot { display: flex; align-items: center; gap: 6px; }
  .rk { font-size: 7.5px; letter-spacing: .1em; color: var(--tx-dim); }
  .foot b { font-size: 11px; font-weight: 700; color: var(--tx); }
  .pb { font-size: 9.5px; color: var(--tx-dim); margin-top: 3px; display: flex; gap: 5px; align-items: center; }
  .pb span { font-size: 7.5px; letter-spacing: .1em; }
  .delta { font-weight: 600; }
  .delta.slow { color: var(--warn); }
  .delta.fast { color: var(--ok); }
  .prim.time { font-size: 20px; font-weight: 700; color: var(--tx); line-height: 1; margin-top: 2px; }
  .prim.time.fin { color: #cfe0f2; }
  .prim.act { font-size: 11.5px; font-weight: 600; color: var(--tx-mut); margin-top: 4px; }
  .prim.seen { font-size: 10.5px; color: var(--tx-dim); margin-top: 4px; }
  .barwrap { position: relative; margin-top: 7px; }
  .lapbar { display: flex; gap: 2px; }
  .seg { flex: 1; height: 4px; background: var(--track); overflow: hidden; border-radius: 1px; }
  .seg > i { display: block; height: 100%; background: var(--pc); }
  .live { position: absolute; top: 2px; width: 7px; height: 7px; margin-left: -3.5px; border-radius: 50%;
          background: var(--pc); transform: translateY(-50%); box-shadow: 0 0 0 1.5px var(--panel); }
  .live::after { content: ""; position: absolute; inset: 0; border-radius: 50%; background: var(--pc);
                 animation: ppulse 1.7s ease-out infinite; }
  @keyframes ppulse { 0% { transform: scale(1); opacity: .55; } 100% { transform: scale(2.6); opacity: 0; } }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/PlayerCard.svelte
git commit -m "feat(player-panel): PlayerCard timing-tower component"
```

---

## Task 8: `PlayerPanel.svelte` + wire into App, then verify

**Files:**
- Create: `src/components/PlayerPanel.svelte`
- Modify: `src/App.svelte` (import + `.player-band` markup + `.player-band` CSS)

- [ ] **Step 1: Implement the panel** — create `src/components/PlayerPanel.svelte`:

```svelte
<script>
  import { presence } from "../lib/stores.js";
  import PlayerCard from "./PlayerCard.svelte";
  // presence is { [player_id]: entry }; object order = server roster order.
  $: players = Object.values($presence);
</script>

{#if players.length}
  <div class="panel" style="--n:{players.length}">
    {#each players as p (p.player_id)}<PlayerCard entry={p} />{/each}
  </div>
{:else}
  <span class="empty">no players</span>
{/if}

<style>
  .panel { display: grid; grid-template-columns: repeat(var(--n, 5), 1fr); gap: 1px;
           background: var(--bd); height: 100%; }
  .empty { font-size: .66rem; color: var(--tx-dim); letter-spacing: .05em; text-transform: uppercase;
           align-self: center; margin: auto; }
</style>
```

- [ ] **Step 2: Import the panel in App.svelte** — add after the other component imports (near `src/App.svelte:22`):

```js
  import PlayerPanel from "./components/PlayerPanel.svelte";
```

- [ ] **Step 3: Mount it in the band** — in `src/App.svelte`, replace the placeholder block:

```svelte
        <!-- Reserved band for the live player panel (sub-project #3) -->
        <div class="player-band">
          <span class="player-band-ph">player status panel</span>
        </div>
```

with:

```svelte
        <!-- Live player panel (sub-project #3) -->
        <div class="player-band">
          <PlayerPanel />
        </div>
```

- [ ] **Step 4: Let the panel fill the band** — in `src/App.svelte`, replace the `.player-band` rule (it currently centres the placeholder) with:

```css
  .player-band {
    /* Live player panel (sub-project #3): fills the space below the feed + controls. */
    flex: 1 0 0; min-height: 130px; overflow: hidden;
    border-top: 1px solid var(--bd); background: var(--bg);
  }
```

Then delete the now-unused `.player-band-ph` rule.

- [ ] **Step 5: Type-check**

Run: `npm run check`
Expected: `0 errors, 0 warnings`.

- [ ] **Step 6: Build (exercises `import.meta.glob` + asset bundling)**

Run: `npm run build`
Expected: build succeeds; the figure PNGs appear under `dist-ui/assets/`.

- [ ] **Step 7: Run the full frontend suite**

Run: `npm run test:js`
Expected: all frontend tests pass (presence + playerCard included).

- [ ] **Step 8: Commit**

```bash
git add src/components/PlayerPanel.svelte src/App.svelte
git commit -m "feat(player-panel): PlayerPanel band, wired into the monitor"
```

---

## Task 9: Final verification

- [ ] **Step 1: Server suite**

Run: `npm --prefix pi test`
Expected: all pass.

- [ ] **Step 2: Frontend suite + types + build**

Run: `npm run test:js && npm run check && npm run build`
Expected: tests pass, `0 errors, 0 warnings`, build succeeds.

- [ ] **Step 3 (manual): live sanity**

With the app running against the server, confirm the band shows one card per roster player and that a racing player shows selections + resets + PB + dashed time + a filling lap bar with the dot; offline players are greyscale with "last seen". (The live timer intentionally shows `—` until the engine provides it.)

- [ ] **Step 4: Finish the branch** — REQUIRED SUB-SKILL: `superpowers:finishing-a-development-branch`.

---

## Notes for the implementer

- **Do NOT commit** `src-tauri/Cargo.toml` (pre-existing unrelated change) or anything under `temp/` (gitignored scratch).
- Player colours, character/kart/course, completion, final_time all already arrive in the presence entry from sub-project #2; this plan only *adds* `resets` and `pb_ms`.
- The live **timer is intentionally dashes** — real in-game time is future engine work, out of scope here.
- `parseTime` (used by `pbDelta`) lives in `src/lib/discordFormat.js` and parses `"m:ss.mmm"`.
