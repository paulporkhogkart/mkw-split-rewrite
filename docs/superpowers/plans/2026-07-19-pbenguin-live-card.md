# pbenguin Live Card (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the locked "KART-OFF print" live card ONCE as a shared `LiveCard.svelte`
(with sprite-sheet chip playback through the chip cache) and switch pbenguin's `PlayerPanel`
to it.

**Architecture:** Faithful Svelte translation of the LOCKED mockups
`docs/design/site-redesign/live-card.html` + `fire-live-card.html` (decision-log headers are
the truth). Data comes from the existing `viewModel` in `src/lib/playerCard.js` (unchanged).
Chips render on canvas per the site-pack spec's binding Playback rules via
`src/lib/chipSheet.js`; sheets/sils arrive from `http://chips.localhost/…` (Plan A's
protocol). The site adopts this component in site-redesign P1b — `PlayerCard.svelte` is NOT
touched (the site's CardWall still imports it).

**Tech Stack:** Svelte 4, vitest, canvas/ImageBitmap, chipSheet.js stepper.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-pbenguin-chip-cache-and-cards-design.md` (Part B)
  + the site-pack spec's **Playback rules** (`2026-07-18-chip-site-pack-design.md`): canvas
  `drawImage` with `frameRect` (NEVER background-position), all of a combo's sheets
  pre-decoded to `ImageBitmap`s, skip-draw-hold-last when a bitmap isn't ready, ink ring
  BAKED in the canvas draw (±1 device px 4-way stamp, `source-in` ink fill, composite under).
- Locked card rules (live-card.html header): timers m:ss.mmm; jank marks SETTLED facts only
  (ticking numbers run straight); hero text authored at 2× inside `scale(.5)` wrappers; card
  250×150 (wall 224×134) torn slab, colour border figure-side; photo figure on every card;
  chip above photo; stacked S-C selection tags; zigzag lap progress; PB digit wave in the
  player's own colour; states racing / finished-beat / finished-missed / idle / offline.
- Fire (fire-live-card.html header): hand-drawn 3-frame tearout blaze at 125 ms, on-fire
  figure frame + 1px ink cel ring, embers across the figure's width.
- Component must be site-adoptable: no Tauri imports in `LiveCard.svelte` or its helpers;
  the chips base URL is a prop (`chipsBase`), default `http://chips.localhost/`.
- Never edit `PlayerCard.svelte`, `playerCard.js` behaviour, or anything under `web/`.
- **Concurrent Velopack work is live in the main checkout** — work in a fresh worktree
  branch off `main` (superpowers:using-git-worktrees); the only shared file this plan touches
  is `PlayerPanel.svelte` (not on Velopack's touch list).
- Chip slugs: lowercase, strip `?'.`, spaces→`_` (matches `toFilename` in `App.svelte:561`);
  combo keys `char__costume__kart` / `char__costume`, costume defaults to `base`.
- Manifest shape (measured, chips-v1): `{version, scale, fps:60, fw:205, fh:216,
  base, combos: { "<slug>": { kart: bool, idle_resume: int,
  anims: { idle|spawn|flourish: {frames, cols, rows} } } } }`.
- Tests: `npx vitest run` from repo root. Visual gate: Paul's in-app eyeball vs the locked
  HTML — do not tweak locked visual constants to taste.

---

### Task 1: Bring the locked design files onto this branch

**Files:**
- Create (copied): `docs/design/site-redesign/live-card.html`,
  `docs/design/site-redesign/fire-live-card.html`, `docs/design/site-redesign/sil/` (the
  placeholder masks the mockups reference)

**Interfaces:**
- Produces: the locked reference files at stable in-repo paths for every later task (and for
  the eyeball gate). Their decision-log headers ride along unmodified.

- [ ] **Step 1: Copy from the site-redesign-p1 branch** (the worktree branch has them
committed; this stays a plain file copy so nothing else from that branch rides along):

```bash
git checkout site-redesign-p1 -- docs/design/site-redesign/live-card.html docs/design/site-redesign/fire-live-card.html docs/design/site-redesign/sil
git status   # exactly these paths staged, nothing else
```

- [ ] **Step 2: Open both HTML files and verify** the LOCKED headers are intact (rounds
1-21 / fire r31 notes). Do NOT edit them.

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(design): copy locked live-card + fire mockups from site-redesign-p1 for the LiveCard port"
```

---

### Task 2: `chipKey.js` — display names → pack slugs

**Files:**
- Create: `src/lib/chipKey.js`
- Test: `src/lib/chipKey.test.js`

**Interfaces:**
- Produces:
  - `slug(name) -> string|null` — `"Baby Daisy"` → `"baby_daisy"`, `"Bowser Jr."` →
    `"bowser_jr"`, null/empty → null
  - `comboKey({ character, costume, kart }) -> string|null` — `"mario__base__standard_kart"`;
    kart absent → `"mario__base"`; character absent → null; costume absent/"Base" → `"base"`
- Consumed by: Task 6 (ChipCanvas) and Task 7 (LiveCard) to key into `manifest.combos`.

- [ ] **Step 1: Write failing tests** (`src/lib/chipKey.test.js`):

```js
import { describe, it, expect } from "vitest";
import { slug, comboKey } from "./chipKey.js";

describe("slug", () => {
  it("lowercases, underscores spaces, strips punctuation", () => {
    expect(slug("Baby Daisy")).toBe("baby_daisy");
    expect(slug("Bowser Jr.")).toBe("bowser_jr");
    expect(slug("B Dasher")).toBe("b_dasher");
    expect(slug("Chargin' Chuck")).toBe("chargin_chuck");
  });
  it("null-safe", () => {
    expect(slug(null)).toBeNull();
    expect(slug("")).toBeNull();
  });
});

describe("comboKey", () => {
  it("kart combo", () =>
    expect(comboKey({ character: "Baby Daisy", costume: "Base", kart: "B Dasher" }))
      .toBe("baby_daisy__base__b_dasher"));
  it("costume folds in, leading position handled by slugs not display order", () =>
    expect(comboKey({ character: "Toad", costume: "Burger Bud", kart: "Mach Rocket" }))
      .toBe("toad__burger_bud__mach_rocket"));
  it("char-only while no kart picked", () =>
    expect(comboKey({ character: "Luigi", costume: null, kart: null })).toBe("luigi__base"));
  it("no character -> null", () =>
    expect(comboKey({ character: null, costume: null, kart: "B Dasher" })).toBeNull());
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/lib/chipKey.test.js` → FAIL.

- [ ] **Step 3: Implement** (`src/lib/chipKey.js`):

```js
// Display names -> chip-pack slugs. Mirrors App.svelte's toFilename rule (the same rule
// that names the capture/template files): lowercase, strip [?'.], spaces -> underscore.

export function slug(name) {
  if (!name) return null;
  const s = name.toLowerCase().replace(/[?'.]/g, "").trim().replace(/\s+/g, "_");
  return s || null;
}

/** Pack combo key for a presence entry's selection. Kart present -> char__costume__kart;
 *  else the standalone char__costume chip. Costume "Base"/absent -> "base". */
export function comboKey({ character, costume, kart }) {
  const c = slug(character);
  if (!c) return null;
  const co = slug(costume) || "base";
  const k = slug(kart);
  return k ? `${c}__${co}__${k}` : `${c}__${co}`;
}
```

- [ ] **Step 4: Run tests** — PASS.  **Step 5: Sanity vs the real pack** — spot-check four
keys against `web/chips.lock` shard names / the manifest combos list (e.g.
`chargin_chuck`, `bowser_jr` appear as shard names in `web/chips.lock`). Note any mismatch
and STOP if one exists (naming truth = the pack).

- [ ] **Step 6: Commit**

```bash
git add src/lib/chipKey.js src/lib/chipKey.test.js
git commit -m "feat(card): chipKey — display names to chip-pack combo slugs"
```

---

### Task 3: `liveCard.js` — pure print-language helpers

**Files:**
- Create: `src/lib/liveCard.js`
- Test: `src/lib/liveCard.test.js`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `JANK` — the locked 5-transform cycle (from the mockup's digit spans):
    `["rotate(-3deg)", "rotate(4deg) translateY(-5px)", "rotate(-2.5deg) translateY(3px)",
      "rotate(3deg) translateY(-4px)", "rotate(-4deg) translateY(2px)"]`
  - `digitSpans(text) -> [{ ch, tj, wd }]` — per character: jank transform `JANK[i % 5]`
    and wave delay `wd = i * 0.07` (seconds, number)
  - `zigzag(laps, fill, w = 128, h = 16) -> { done: string[], current: {d, offset} | null,
    future: string[] }` — SVG path `d` strings in the mockup's language: per-lap 5-point
    zigzag segments with 8px gaps, margin 2; `fill` = overall race completion 0..1 mapped
    onto the current lap's segment as a `stroke-dashoffset` percentage of `pathLength=100`
    (offset `100 - lapFrac*100`, clamped 0..100); laps<2 or invalid → single segment.
  - `sessTags(activity, now) -> { att: number|null, racing: string|null }` — from
    `viewModel(...).activity` (`{count, label, sinceMs}`): `att` = count; `racing` =
    `m:ss` elapsed from sinceMs to now (null when sinceMs null).

- [ ] **Step 1: Write failing tests** (`src/lib/liveCard.test.js`):

```js
import { describe, it, expect } from "vitest";
import { JANK, digitSpans, zigzag, sessTags } from "./liveCard.js";

describe("digitSpans", () => {
  it("cycles the locked jank transforms with 0.07s wave steps", () => {
    const s = digitSpans("1:50.517");
    expect(s.length).toBe(8);
    expect(s[0]).toEqual({ ch: "1", tj: JANK[0], wd: 0 });
    expect(s[5].tj).toBe(JANK[0]);          // cycle of 5
    expect(s[3].wd).toBeCloseTo(0.21);
  });
});

describe("zigzag", () => {
  it("one segment per lap, gapped, inside the well", () => {
    const z = zigzag(3, 0, 128, 16);
    expect(z.done.length + z.future.length + (z.current ? 1 : 0)).toBe(3);
    expect(z.done.length).toBe(0);
    expect(z.current).not.toBeNull();
    const xs = [...(z.current.d + z.future.join(" ")).matchAll(/([\d.]+),/g)].map((m) => +m[1]);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(2);
    expect(Math.max(...xs)).toBeLessThanOrEqual(126);
  });
  it("fill walks laps from done to future", () => {
    const z = zigzag(3, 2.5 / 3, 128, 16);   // in lap 3, half done
    expect(z.done.length).toBe(2);
    expect(z.current.offset).toBeCloseTo(50, 0);
    expect(z.future.length).toBe(0);
  });
  it("finished = all done", () => {
    const z = zigzag(3, 1, 128, 16);
    expect(z.done.length).toBe(3);
    expect(z.current).toBeNull();
  });
  it("degenerate lap counts fall back to one segment", () => {
    expect(zigzag(0, 0.4, 128, 16).done.length + 1).toBe(2 - 1 + 1); // 1 segment total
  });
});

describe("sessTags", () => {
  it("counts + m:ss elapsed", () => {
    expect(sessTags({ count: 15, label: null, sinceMs: 1_000_000 }, 1_000_000 + 84_000))
      .toEqual({ att: 15, racing: "1:24" });
    expect(sessTags(null, 5)).toEqual({ att: null, racing: null });
  });
});
```

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement** (`src/lib/liveCard.js`):

```js
// Pure helpers for LiveCard.svelte — the locked print language's math. No DOM, no Svelte.
// Sources: docs/design/site-redesign/live-card.html (LOCKED header = the decision log).

export const JANK = [
  "rotate(-3deg)", "rotate(4deg) translateY(-5px)", "rotate(-2.5deg) translateY(3px)",
  "rotate(3deg) translateY(-4px)", "rotate(-4deg) translateY(2px)",
];

/** Settled timers render as janked digit spans; the PB wave staggers 0.07s per char. */
export function digitSpans(text) {
  return [...(text || "")].map((ch, i) => ({ ch, tj: JANK[i % 5], wd: +(i * 0.07).toFixed(2) }));
}

/** Deterministic per-index y-jitter for zigzag peaks (mockup's hand-authored vibe). */
const YS = [12, 4, 12, 5, 10, 11, 3, 13, 5, 11, 10, 4, 12, 5, 10];

/** One 5-point zigzag segment starting at x, width w, points at YS offsets. */
function seg(x, w, yi) {
  const px = [0, 0.25, 0.5, 0.75, 0.92].map((f) => +(x + f * w).toFixed(1));
  const py = px.map((_, i) => YS[(yi + i) % YS.length]);
  return `M${px[0]},${py[0]} ` + px.slice(1).map((v, i) => `L${v},${py[i + 1]}`).join(" ");
}

/** Segmented zigzag mini-track: laps as gapped segments; `fill` = race completion 0..1.
 *  Done laps ink solid, the current lap fills via dashoffset, future laps ghost. */
export function zigzag(laps, fill, w = 128) {
  const n = Number.isInteger(laps) && laps >= 1 ? laps : 1;
  const usable = w - 4, gap = n > 1 ? 8 : 0;
  const segW = (usable - gap * (n - 1)) / n;
  const f = Math.min(1, Math.max(0, fill || 0));
  const cur = f >= 1 ? n : Math.floor(f * n);        // index of the in-progress lap
  const lapFrac = f >= 1 ? 1 : f * n - cur;
  const out = { done: [], current: null, future: [] };
  for (let i = 0; i < n; i++) {
    const d = seg(2 + i * (segW + gap), segW, i * 3);
    if (i < cur) out.done.push(d);
    else if (i === cur && f < 1) out.current = { d, offset: Math.min(100, Math.max(0, 100 - lapFrac * 100)) };
    else out.future.push(d);
  }
  return out;
}

/** ATT + RACING micro-tags from viewModel().activity. */
export function sessTags(activity, now) {
  if (!activity) return { att: null, racing: null };
  let racing = null;
  if (activity.sinceMs != null) {
    const s = Math.max(0, Math.floor((now - activity.sinceMs) / 1000));
    racing = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  return { att: activity.count ?? null, racing };
}
```

- [ ] **Step 4: Run tests** → PASS (fix the degenerate-laps test to assert plainly:
`zigzag(0, .4).done.length + (zigzag(0, .4).current ? 1 : 0) + zigzag(0, .4).future.length === 1`).

- [ ] **Step 5: Commit**

```bash
git add src/lib/liveCard.js src/lib/liveCard.test.js
git commit -m "feat(card): liveCard helpers — jank spans, zigzag track, session tags"
```

---

### Task 4: `chipDirector.js` — entry transitions → chip choreography

**Files:**
- Create: `src/lib/chipDirector.js`
- Test: `src/lib/chipDirector.test.js`

**Interfaces:**
- Consumes: `comboKey` from `chipKey.js`.
- Produces: `directorStep(prev, next) -> { combo: string|null, action:
  "select"|"confirm"|"idle"|null }` — pure transition mapper. `prev`/`next` are presence
  entries (`{screen, character, costume, kart, final_time, online}`); `action` maps 1:1 onto
  `createChipPlayer`'s `select()/confirm()/idle()`; `null` action = leave the player alone.
  Rules (spec Part B):
  - `combo` = `comboKey(next)` (null hides the chip).
  - combo changed while `next.screen` ∈ `{CHARACTER_SELECT, KART_SELECT, COURSE_SELECT}` → `"select"` (spawn, interruptible).
  - `prev.screen === "KART_SELECT"` and `next.screen !== "KART_SELECT"` and `next.kart` → `"confirm"` (kart locked in → flourish).
  - `next.final_time` truthy and `prev.final_time` falsy → `"confirm"` (race-finish flourish).
  - combo changed anywhere else (incl. first sight) → `"idle"` (fresh player starts idling).
  - otherwise → `null`.

- [ ] **Step 1: Write failing tests** (`src/lib/chipDirector.test.js`):

```js
import { describe, it, expect } from "vitest";
import { directorStep } from "./chipDirector.js";

const e = (o = {}) => ({ screen: "RACING", character: "Mario", costume: "Base",
  kart: "Standard Kart", final_time: null, online: true, ...o });

describe("directorStep", () => {
  it("swap on a select screen -> spawn", () => {
    const r = directorStep(e({ screen: "KART_SELECT" }), e({ screen: "KART_SELECT", kart: "B Dasher" }));
    expect(r).toEqual({ combo: "mario__base__b_dasher", action: "select" });
  });
  it("leaving kart select with a kart -> flourish", () => {
    const r = directorStep(e({ screen: "KART_SELECT" }), e({ screen: "COURSE_SELECT" }));
    expect(r.action).toBe("confirm");
  });
  it("finish -> flourish once", () => {
    expect(directorStep(e(), e({ final_time: "1:50.517" })).action).toBe("confirm");
    expect(directorStep(e({ final_time: "1:50.517" }), e({ final_time: "1:50.517" })).action).toBeNull();
  });
  it("combo change off-select (or first sight) -> idle", () => {
    expect(directorStep(null, e()).action).toBe("idle");
    expect(directorStep(e(), e({ character: "Luigi" })).action).toBe("idle");
  });
  it("steady state -> no action", () => {
    expect(directorStep(e(), e()).action).toBeNull();
  });
  it("no character -> chip hidden", () => {
    expect(directorStep(e(), e({ character: null })).combo).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement** (`src/lib/chipDirector.js`):

```js
// Presence-entry transitions -> chip player actions (spec Part B choreography:
// swap on a select screen = spawn, kart lock-in = flourish, finish = flourish).
import { comboKey } from "./chipKey.js";

const SELECT_SCREENS = new Set(["CHARACTER_SELECT", "KART_SELECT", "COURSE_SELECT"]);

export function directorStep(prev, next) {
  const combo = comboKey(next || {});
  if (!combo) return { combo: null, action: null };
  const prevCombo = prev ? comboKey(prev) : null;
  const changed = combo !== prevCombo;
  if (changed && SELECT_SCREENS.has(next.screen)) return { combo, action: "select" };
  if (prev && prev.screen === "KART_SELECT" && next.screen !== "KART_SELECT" && next.kart)
    return { combo, action: "confirm" };
  if (next.final_time && !(prev && prev.final_time)) return { combo, action: "confirm" };
  if (changed) return { combo, action: "idle" };
  return { combo, action: null };
}
```

- [ ] **Step 4: Run tests** → PASS.  **Step 5: Commit**

```bash
git add src/lib/chipDirector.js src/lib/chipDirector.test.js
git commit -m "feat(card): chipDirector — presence transitions to spawn/flourish/idle"
```

---

### Task 5: `chipStream.js` — manifest + ImageBitmap combo cache

**Files:**
- Create: `src/lib/chipStream.js`
- Test: `src/lib/chipStream.test.js`

**Interfaces:**
- Consumes: Plan A's URL contract (`<base>manifest.json`, `<base rewritten in manifest>` +
  `<combo>__<anim>.webp` / `__<anim>__sil_k{0..3}.png`).
- Produces:
  - `loadManifest(chipsBase, fetchFn = fetch) -> Promise<manifest|null>` — GET
    `${chipsBase}manifest.json`, null on any failure (cards render chipless), memoized per
    base for 5 min (`_resetManifestCache()` export for tests).
  - `sheetUrl(manifest, combo, anim)` / `silUrl(manifest, combo, anim, k)` — string URLs off
    `manifest.base`.
  - `createBitmapCache(limit = 12, loader = defaultBitmapLoader)` →
    `{ get(manifest, combo) -> { bitmaps: {anim: ImageBitmap|null}, ready(anim): bool } }` —
    per-combo lazy decode of ALL the combo's sheets (`img.decode()` → `createImageBitmap`,
    inside `loader`), LRU-evicted past `limit` combos (evicted bitmaps `.close()`d). Until a
    bitmap resolves, `ready()` is false → caller skips the draw and holds (binding rule).
  - LRU + URL logic is pure and tested with an injected fake loader; the real
    `defaultBitmapLoader` (DOM Image/createImageBitmap) stays thin and untested.

- [ ] **Step 1: Write failing tests** (`src/lib/chipStream.test.js`):

```js
import { describe, it, expect, vi } from "vitest";
import { loadManifest, sheetUrl, silUrl, createBitmapCache, _resetManifestCache } from "./chipStream.js";

const MANIFEST = { fw: 205, fh: 216, fps: 60, base: "http://chips.localhost/chips-v1/",
  combos: { "mario__base": { kart: false, idle_resume: 0, anims: { idle: { frames: 8, cols: 3, rows: 3 } } } } };

describe("loadManifest", () => {
  it("fetches and memoizes per base", async () => {
    _resetManifestCache();
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => MANIFEST });
    expect(await loadManifest("http://chips.localhost/", f)).toEqual(MANIFEST);
    await loadManifest("http://chips.localhost/", f);
    expect(f).toHaveBeenCalledTimes(1);
  });
  it("null on failure (chipless cards, never a throw)", async () => {
    _resetManifestCache();
    const f = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await loadManifest("http://chips.localhost/", f)).toBeNull();
  });
});

describe("urls", () => {
  it("builds tagged urls from the manifest base", () => {
    expect(sheetUrl(MANIFEST, "mario__base", "idle"))
      .toBe("http://chips.localhost/chips-v1/mario__base__idle.webp");
    expect(silUrl(MANIFEST, "mario__base", "idle", 2))
      .toBe("http://chips.localhost/chips-v1/mario__base__idle__sil_k2.png");
  });
});

describe("createBitmapCache", () => {
  it("loads each anim once, LRU-evicts and closes", async () => {
    const closed = [];
    const loader = vi.fn(async (url) => ({ url, close: () => closed.push(url) }));
    const cache = createBitmapCache(2, loader);
    const a = cache.get(MANIFEST, "mario__base");
    await Promise.resolve(); await Promise.resolve();
    expect(loader).toHaveBeenCalledTimes(1);           // one anim in this combo
    expect(a.ready("idle")).toBe(true);
    const m2 = { ...MANIFEST, combos: { ...MANIFEST.combos, x__base: MANIFEST.combos["mario__base"], y__base: MANIFEST.combos["mario__base"] } };
    cache.get(m2, "x__base"); cache.get(m2, "y__base"); // 3rd combo evicts mario
    await Promise.resolve(); await Promise.resolve();
    expect(closed.some((u) => u.includes("mario__base"))).toBe(true);
  });
  it("not-ready before decode resolves (skip-draw-hold contract)", () => {
    let resolve; const loader = () => new Promise((r) => (resolve = r));
    const cache = createBitmapCache(2, loader);
    const h = cache.get(MANIFEST, "mario__base");
    expect(h.ready("idle")).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement** (`src/lib/chipStream.js`):

```js
// Chip asset access for LiveCard: manifest (memoized) + per-combo ImageBitmap cache.
// Binding rules (site-pack spec Playback): pre-decode a combo's sheets into ImageBitmaps
// (pinned, GPU-backed); if one isn't ready at draw time, the caller skips the draw and
// holds the previous frame — never blank.

const TTL = 5 * 60 * 1000;
let manifestMemo = new Map(); // base -> { at, p }

export function _resetManifestCache() { manifestMemo = new Map(); }

export async function loadManifest(chipsBase, fetchFn = globalThis.fetch) {
  const hit = manifestMemo.get(chipsBase);
  if (hit && Date.now() - hit.at < TTL) return hit.p;
  const p = (async () => {
    try {
      const r = await fetchFn(`${chipsBase}manifest.json`);
      return r.ok ? await r.json() : null;
    } catch { return null; }
  })();
  manifestMemo.set(chipsBase, { at: Date.now(), p });
  const v = await p;
  if (v === null) manifestMemo.delete(chipsBase); // failed fetch: retry next mount
  return v;
}

export const sheetUrl = (m, combo, anim) => `${m.base}${combo}__${anim}.webp`;
export const silUrl = (m, combo, anim, k) => `${m.base}${combo}__${anim}__sil_k${k}.png`;

/** Default loader: fetch-decode a sheet into a pinned ImageBitmap. DOM-only, untested. */
export async function defaultBitmapLoader(url) {
  const img = new Image();
  img.src = url;
  await img.decode();
  return await createImageBitmap(img);
}

export function createBitmapCache(limit = 12, loader = defaultBitmapLoader) {
  const combos = new Map(); // combo -> { bitmaps: {anim: bitmap|null} }
  function evict() {
    while (combos.size > limit) {
      const [oldest, entry] = combos.entries().next().value;
      combos.delete(oldest);
      for (const b of Object.values(entry.bitmaps)) b && b.close && b.close();
    }
  }
  return {
    get(manifest, combo) {
      let entry = combos.get(combo);
      if (entry) { combos.delete(combo); combos.set(combo, entry); } // LRU touch
      else {
        entry = { bitmaps: {} };
        combos.set(combo, entry);
        const def = manifest.combos[combo];
        if (def) for (const anim of Object.keys(def.anims)) {
          entry.bitmaps[anim] = null;
          loader(sheetUrl(manifest, combo, anim))
            .then((b) => { if (combos.has(combo)) entry.bitmaps[anim] = b; else b && b.close && b.close(); })
            .catch(() => {}); // missing sheet: stays null, draw skips forever (chipless)
        }
        evict();
      }
      return { bitmaps: entry.bitmaps, ready: (anim) => !!entry.bitmaps[anim] };
    },
  };
}
```

- [ ] **Step 4: Run tests** → PASS.  **Step 5: Commit**

```bash
git add src/lib/chipStream.js src/lib/chipStream.test.js
git commit -m "feat(card): chipStream — memoized manifest + LRU ImageBitmap combo cache"
```

---

### Task 6: `ChipCanvas.svelte` — canvas chip playback

**Files:**
- Create: `src/components/ChipCanvas.svelte`

**Interfaces:**
- Consumes: `createChipPlayer`/`frameRect` (`chipSheet.js`), `createBitmapCache`/`loadManifest`
  (`chipStream.js`), `directorStep` output via props.
- Produces: `<ChipCanvas manifest bitmapCache combo action actionSeq ink height />`
  - `manifest`, `bitmapCache` — shared per-panel instances (passed down from PlayerPanel/
    LiveCard so five cards share ONE cache).
  - `combo` — pack slug or null (null renders nothing).
  - `action` + `actionSeq` — latest director action and a monotonically increasing sequence
    number (the seq forces re-triggering repeated "select"s).
  - `ink` (default `#101114`) — baked ring colour; `height` — CSS px (92 / 112 / 76).
  - Canvas backing store = `fw×fh` native, CSS height set by prop, width from aspect;
    per rAF: `player.tick()` → if the current anim's bitmap `ready()`, draw; else skip and
    hold the last painted frame (never clear).
  - Baked ink ring per spec: scratch canvas, 4× offset stamps at ±1 device px, `source-in`
    ink fill, then the frame composited on top.

- [ ] **Step 1: Implement** (logic is thin over tested modules; no component test —
`chipSheet` math and cache behaviour are already covered):

```svelte
<script>
  // Canvas sprite-sheet chip per the BINDING Playback rules (site-pack spec):
  // drawImage const-dest-rect (never background-position), ImageBitmap-only sources,
  // skip-draw-hold-last, ink ring baked in the draw. One rAF per canvas; five cards'
  // draws are texture copies, cheap.
  import { onDestroy } from "svelte";
  import { createChipPlayer, frameRect } from "../lib/chipSheet.js";

  export let manifest = null;
  export let bitmapCache = null;
  export let combo = null;
  export let action = null;      // "select" | "confirm" | "idle" | null
  export let actionSeq = 0;      // bump to re-fire the same action
  export let ink = "#101114";
  export let height = 92;

  let canvas, raf = 0, player = null, handle = null, lastSeq = -1, curCombo = null;
  let scratch = null;

  $: entry = manifest && combo ? manifest.combos[combo] : null;
  $: fw = manifest?.fw ?? 205;
  $: fh = manifest?.fh ?? 216;

  $: if (entry && combo !== curCombo) {
    curCombo = combo;
    handle = bitmapCache.get(manifest, combo);
    player = createChipPlayer({ entry, fps: manifest.fps, fw, fh });
    lastSeq = -1; // a fresh combo consumes the pending action below
  }
  $: if (player && actionSeq !== lastSeq && action) {
    lastSeq = actionSeq;
    if (action === "select") player.select();
    else if (action === "confirm") player.confirm();
    else player.idle();
  }

  function draw() {
    raf = requestAnimationFrame(draw);
    if (!canvas || !player || !handle) return;
    const { anim, frame } = player.tick();
    if (!handle.ready(anim)) return;                 // skip + hold, never blank
    const bmp = handle.bitmaps[anim];
    const a = entry.anims[anim];
    const { sx, sy } = frameRect(frame, a.cols, fw, fh);
    if (!scratch) { scratch = document.createElement("canvas"); scratch.width = fw; scratch.height = fh; }
    const s = scratch.getContext("2d");
    // ink ring: 4-way ±1px stamp, source-in ink, frame on top (spec: == the CSS ring chain)
    s.clearRect(0, 0, fw, fh);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]])
      s.drawImage(bmp, sx, sy, fw, fh, dx, dy, fw, fh);
    s.globalCompositeOperation = "source-in";
    s.fillStyle = ink; s.fillRect(0, 0, fw, fh);
    s.globalCompositeOperation = "source-over";
    s.drawImage(bmp, sx, sy, fw, fh, 0, 0, fw, fh);
    const c = canvas.getContext("2d");
    c.imageSmoothingQuality = "high";
    c.clearRect(0, 0, fw, fh);
    c.drawImage(scratch, 0, 0);
  }
  $: if (canvas && entry && !raf) raf = requestAnimationFrame(draw);
  onDestroy(() => cancelAnimationFrame(raf));
</script>

{#if entry}
  <canvas bind:this={canvas} width={fw} height={fh}
    style="height:{height}px;width:{(height * fw / fh).toFixed(1)}px" />
{/if}
```

- [ ] **Step 2: Verify build** — `npx vitest run` still green, `npm run tauri dev` compiles
(component unused yet; import it once from a scratch page or just confirm the vite build).

- [ ] **Step 3: Commit**

```bash
git add src/components/ChipCanvas.svelte
git commit -m "feat(card): ChipCanvas — binding-rules sprite playback with baked ink ring"
```

---

### Task 7: `LiveCard.svelte` — the locked card, translated

**Files:**
- Create: `src/components/LiveCard.svelte`
- Reference (READ ONLY): `docs/design/site-redesign/live-card.html`,
  `docs/design/site-redesign/fire-live-card.html`

**Interfaces:**
- Consumes: `viewModel`/`fmtTimeMs` (`playerCard.js` — unchanged), `figureFor/onpaceFigure`
  (`playerFigures.js`), `updateFire` (`fireState.js`), `deltaMode` (`cardSettings.js`),
  `digitSpans/zigzag/sessTags` (Task 3), `directorStep` (Task 4),
  `loadManifest/createBitmapCache/silUrl` (Task 5), `ChipCanvas` (Task 6).
- Produces: `<LiveCard entry now stale chipsBase manifest bitmapCache />` — one card,
  250×150 design space, scaled to its container via `transform: scale(var(--s))` (the parent
  sets `--s`; wall size 224 ⇒ `--s:.896`). All five locked states. No Tauri imports.

**Translation contract (from the LOCKED files — copy values verbatim, do not restyle):**
- DOM skeleton per state, exactly the mockup's: `.bd` + `.face` (torn clip-path polygon),
  `.figmask > img` (photo, `--fx` nudge), two `.tearW` plies (`ply2`/`plyC`, `bigT front`
  variants during selection) each holding a `.tearS` whose `mask-image` cycles
  `silUrl(manifest, combo, anim, k)` k=0..3 at 300 ms (settled cards hold k0),
  `ChipCanvas` in the `.kchip` slot (92px, 112px selection, 76px wall), `.in` content block:
  `.ntag` name, `.course`, `.ssw > .timer` (2×-authored, `scale(.5)`), `.sess` micro-tags,
  `.pbrow` (PB + delta), `.twell` zigzag SVG, `.stags` stacked selection tags, `.stats3`
  idle/offline micro-grid.
- CSS: lift the rule blocks for the classes above from `live-card.html:54-163` unchanged
  (drop the mockup-page chrome: `.lab/.cap/.row/.board/.frame/.btn/.wall/.zoom/.gridzoom`).
  The `.kchip` `<img>` drop-shadow ring filter is NOT copied — the ring is baked in
  ChipCanvas (spec); keep the positional rules (`right:var(--kx…)`, heights, z-indexes).
- Fire (from `fire-live-card.html:45-60,138-147,153-166`): the three SVG `defs` paths
  (`tA/tB/tC` body+core) inlined once, `.firewin/.tfx` + `.embwin/.deb` blocks verbatim;
  frame stepping k=(k+1)%3 every 125 ms; body fill `var(--c)`, core fill
  `color-mix(in srgb, var(--c) 45%, #fff)` (mockup's #d9ccfd vs #a78bfa ≈ this mix — eyeball
  vs the mockup and adjust the percentage only if visibly off).
- State mapping (all from `vm = viewModel(entry, now, delayed, …)` — same wiring as
  `PlayerCard.svelte:12-38`, reuse it verbatim including the delay-buffer sampling, fire
  hold, and `forceFire`):
  - racing → live timer (straight digits, ticking from `vm.primary`), zigzag current-lap
    fill from `vm.bar.fill`, sess tags, PB row.
  - finished-beat → settled jank digits + `wave` class (3 iterations), delta `ok`; missed →
    settled jank, delta `bad`; gold delta when `vm.delta.cls` marks best (`gold` for
    pace-gold, map `vm.delta.cls` "ahead-gain"→`ok`, "behind-loss"→`bad`, gold via
    `vm.badge`/pace-gold same as the old card's colours — read `PlayerCard.svelte`'s delta
    usage and mirror its class mapping into ok/bad/gold).
  - idle online (no selection) → `mutd` + `.stats3` grid from `vm.stats`.
  - offline/stale → `offl` + stats3 (vm handles which stats exist).
  - onFire/forceFire → fire block + onpace figure + gold delta (fire-live-card rules).
- Chip wiring: `$: step = directorStep(prevEntry, entry)` with `prevEntry` kept in a
  variable AFTER computing step (classic prev-tracking); `actionSeq` increments whenever
  `step.action` is non-null. Tear plies render only when a chip combo exists and the sils'
  `mask-image` URLs come from the same manifest; performing cards (racing/selection) cycle
  k0..3, settled hold k0 (mockup JS `applyMask` loop → a Svelte interval).

- [ ] **Step 1: Build the component.** Start from this skeleton and complete each region by
transcribing the mockup blocks listed above:

```svelte
<script>
  import { viewModel } from "../lib/playerCard.js";
  import { sampleAt, deltaTrendAt, DELAY_MS } from "../lib/raceTimerBuffer.js";
  import { deltaMode } from "../lib/cardSettings.js";
  import { updateFire } from "../lib/fireState.js";
  import { figureFor, onpaceFigure } from "../lib/playerFigures.js";
  import { digitSpans, zigzag, sessTags } from "../lib/liveCard.js";
  import { directorStep } from "../lib/chipDirector.js";
  import { silUrl } from "../lib/chipStream.js";
  import ChipCanvas from "./ChipCanvas.svelte";
  import { onDestroy } from "svelte";

  export let entry;
  export let now = Date.now();
  export let stale = false;
  export let manifest = null;      // shared, loaded by the panel
  export let bitmapCache = null;   // shared LRU

  // ── identical data wiring to PlayerCard.svelte (delay buffer, fire, forceFire) ──
  $: isRacing = !stale && !!entry && entry.online !== false && entry.screen === "RACING" && !entry.final_time;
  $: delayed = isRacing ? sampleAt(entry.player_id, now - DELAY_MS) : null;
  $: trend = isRacing && $deltaMode === "pace" ? deltaTrendAt(entry.player_id, now - DELAY_MS) : null;
  $: vm = viewModel(entry, now, delayed, { deltaMode: $deltaMode, trend, stale });
  $: forceFire = vm.state === "finished" && vm.finPb;
  $: aheadNow = isRacing && ($deltaMode === "laps"
      ? !!(entry.lap_delta && entry.lap_delta.delta_ms != null && entry.lap_delta.delta_ms < 0)
      : !!(delayed && delayed.pb_delta_ms != null && delayed.pb_delta_ms < 0));
  $: onFire = entry ? updateFire(entry.player_id, { ahead: aheadNow, racing: isRacing, now, mode: $deltaMode }) : false;
  $: fig = (onFire || forceFire) ? (onpaceFigure(vm.name) || figureFor(vm.name, true)) : figureFor(vm.name, vm.online);

  // ── chip choreography ──
  let prevEntry = null, actionSeq = 0, chip = { combo: null, action: null };
  $: {
    const step = directorStep(prevEntry, entry);
    if (step.action) { actionSeq += 1; chip = step; }
    else chip = { ...chip, combo: step.combo };
    prevEntry = entry;
  }
  $: selecting = ["CHARACTER_SELECT", "KART_SELECT", "COURSE_SELECT"].includes(entry?.screen);
  $: performing = isRacing || selecting;

  // tear mask k-cycle: performing cards step k0..3 @300ms, settled hold k0 (locked rule)
  let tearK = 0;
  const tearT = setInterval(() => (tearK = (tearK + 1) % 4), 300);
  // fire frame cycle @125ms (locked fire)
  let fireK = 0;
  const fireT = setInterval(() => (fireK = (fireK + 1) % 3), 125);
  onDestroy(() => { clearInterval(tearT); clearInterval(fireT); });
  $: k = performing ? tearK : 0;
  $: tearAnim = chip.action === "select" ? "spawn" : chip.action === "confirm" ? "flourish" : "idle";
  $: sess = sessTags(vm.activity, typeof now === "number" ? now : Date.now());
  $: zz = vm.bar ? zigzag(entry?.tot_lap ?? 3, vm.bar.fill) : null;
</script>

<!-- markup: transcribe the mockup state blocks here (racing/finished/idle/offline/selection),
     conditioned on vm.state / selecting / onFire, with ChipCanvas in the .kchip slot and
     the two tear plies masked by silUrl(manifest, chip.combo, tearAnim, k). -->

<style>
  /* transcribed verbatim from live-card.html:54-163 + fire-live-card.html:45-60 (minus
     page chrome and the .kchip img ring filter) — see plan Task 7 translation contract */
</style>
```

The markup/CSS transcription is the deliberate bulk of this task: work state-by-state
against the open mockup file, and after each state run the app side-by-side with the mockup
in a browser.

- [ ] **Step 2: Verify** — `npx vitest run` green (helpers untouched); visual: temporary
harness route or Storybook-less trick — mount `LiveCard` behind a `?cardlab` query flag in
`App.svelte` fed with five hardcoded entries copying the mockup states (racing / beat /
missed / idle / offline). Compare against `live-card.html` open in a browser at 100%.
Delete the harness before the final commit of this task or keep it behind the flag if it
earns its keep (note the choice in the commit).

- [ ] **Step 3: Commit**

```bash
git add src/components/LiveCard.svelte src/App.svelte
git commit -m "feat(card): LiveCard — locked print-language card with canvas chips + fire"
```

---

### Task 8: PlayerPanel swap + shared chip plumbing

**Files:**
- Modify: `src/components/PlayerPanel.svelte`

**Interfaces:**
- Consumes: `LiveCard` (Task 7), `loadManifest`, `createBitmapCache` (Task 5).
- Produces: pbenguin renders LiveCards. ONE manifest load + ONE bitmap cache for the panel
  (`chipsBase` default `http://chips.localhost/`). `PlayerCard.svelte` stays in the tree
  untouched (site still imports it until P1b).

- [ ] **Step 1: Swap** in `PlayerPanel.svelte`:

```svelte
<script>
  // …existing imports…
  import LiveCard from "./LiveCard.svelte";
  import { loadManifest, createBitmapCache } from "../lib/chipStream.js";

  const CHIPS_BASE = "http://chips.localhost/";
  let manifest = null;
  loadManifest(CHIPS_BASE).then((m) => (manifest = m)); // null => chipless cards
  const bitmapCache = createBitmapCache(12);
</script>
```

and in the markup replace the `PlayerCard` line:

```svelte
{#each players as p (p.player_id)}
  <LiveCard entry={p} {now} stale={!connected && !p._localSelf} {manifest} {bitmapCache} />
{/each}
```

Also set the card scale on the panel grid so 250×150 fits the cell (compute `--s` from the
cell width via CSS `container`-free approach: the panel already knows its column count; use
`transform: scale(calc(var(--cellw) / 250))` with `--cellw` measured in a `bind:clientWidth`
on the panel — one measurement, five cards).

- [ ] **Step 2: Run the app** (`npm run tauri dev`) with the tracker live or `--video`
replay feeding presence; sanity-check every state transition end-to-end:
select screens → spawn chips; kart lock → flourish; racing → idle bob + ticking straight
timer + zigzag fill; finish (PB) → wave + flourish; idle/offline cards. Offline test: stop
the Pi link → cards render chipless but intact.

- [ ] **Step 3: Full suites** — `npx vitest run`, `cd src-tauri && cargo test` (untouched
but confirm), `npm run check` if configured for the desktop src.

- [ ] **Step 4: Commit**

```bash
git add src/components/PlayerPanel.svelte
git commit -m "feat(card): PlayerPanel renders LiveCard — locked print cards live in pbenguin"
```

---

## Final verification (whole plan)

- [ ] All vitest suites green; `cargo test` green.
- [ ] Side-by-side eyeball: pbenguin cards vs `docs/design/site-redesign/live-card.html` and
  `fire-live-card.html` in a browser — **Paul's sign-off is the gate**; capture disagreements
  as findings, never self-tune locked values.
- [ ] `PlayerCard.svelte` and `web/` untouched (`git diff --stat main..HEAD -- web/ src/components/PlayerCard.svelte` is empty).
- [ ] Chips exercise the Plan A cache (watch `%APPDATA%\mkw-tracker\chips\` fill during use).
