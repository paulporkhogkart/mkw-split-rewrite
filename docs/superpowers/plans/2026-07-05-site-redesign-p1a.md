# Site Redesign — Project 1a (auth flip, kit foundation, turf refactor, sharpness, mockup locks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the pi reads-public auth flip, the `web/src/kit/` foundation proven by a zero-visual-change TurfLeaderboard refactor + sharpness fix, and run the mockup rounds that LOCK the reference HTML for shell/nav, live cards, activity log, turf chrome, fire, and territory.

**Architecture:** Spec = `docs/superpowers/specs/2026-07-05-site-redesign-design.md`. Project 1 is split: **1a (this plan)** contains everything specifiable against existing pixel truth (the shipped turf cards + `docs/design/turf-leaderboard/*.html`) plus the design rounds that create the remaining truth; **Plan 1b** (written immediately after the locks, as this plan's final task) implements the locked references. The kit at 1a-level is tokens + jank data + imperative helpers + CSS classes — NOT Svelte wrapper components (those come in 1b when locked designs demand them); the turf cards' imperative WAAPI choreography must not be decomposed.

**Tech Stack:** pi: Hono + node:sqlite + vitest (`npm test` from `pi/`). web: Vite + Svelte 4 + vitest (`npm test` from `web/`), dev at `http://127.0.0.1:1430` (never `localhost`). Visual verification: real browser (headless Edge + CDP) per house rule.

## Global Constraints

- Spec §2 design language rules apply to everything built here; **deterministic jank only** (pure function of identity; never `Math.random()`).
- **Zero visual change** for the TurfLeaderboard refactor (Task 3) and **never-regress A/B** (incumbent wins ties) for fire/territory rounds (Tasks 9–10).
- Mockups use **REAL assets + REAL data** (real figures, real chips, real names/times) — no lorem, no synthetic players.
- All new web assets are **ordinary git binaries, NEVER Git LFS**.
- Smooth AA always: hi-res → downscale; never CSS-upscale a raster.
- Writes keep Bearer-header-only tokens. `/v1/me/*` stays token-gated (identity, not secrecy).
- Locked mockups commit to `docs/design/site-redesign/` and are pixel truth for Plan 1b; do not edit a LOCKED file afterwards without user sign-off.
- Branch: `site-redesign-p1` (create via worktree at execution start per `superpowers:using-git-worktrees`).

---

### Task 1: Pi auth flip — reads public, writes tokened

**Files:**
- Modify: `pi/src/api/app.ts:36-48`
- Modify: `pi/src/api/app.test.ts:41-64`
- Modify: `pi/src/api/reads.test.ts:24-30,76-84,109-119`
- Modify: `pi/src/api/courses.test.ts:32-35`
- Modify: `pi/src/api/players.test.ts:34-37`
- Modify: `pi/src/api/screen.test.ts:55-59`
- Modify: `pi/CLAUDE.md` (API gating section)

**Interfaces:**
- Consumes: existing `requireTokenAny(db)` from `pi/src/api/auth.ts` (unchanged).
- Produces: every HTTP GET (incl. WS upgrade GETs and `/explorer`) reachable token-less with `Access-Control-Allow-Origin: *`; non-GET routes gated exactly as before. Route-level guards inside `reads.ts` (`/v1/me/*` inline `requireToken`) and `screen.ts`/`runs.ts` (inline write guards) are untouched.

- [ ] **Step 1: Write the failing tests** — in `pi/src/api/app.test.ts` (fixture is `appWith()`, already defined at the top of the file): REPLACE the entire `describe('reads need a token', ...)` block (lines 40–50) with:

```ts
describe('reads are public', () => {
  it('any GET 200s token-less (gating was temporary; writes still 401)', async () => {
    const { app } = appWith();
    expect((await app.request('/v1/seasons')).status).toBe(200);
    expect((await app.request('/explorer')).status).not.toBe(401);   // 200, or 404 if html missing — never auth-blocked
    const w = await app.request('/v1/runs', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
    expect(w.status).toBe(401);
  });
  it('every GET carries permissive CORS for the website', async () => {
    const { app } = appWith();
    const res = await app.request('/v1/seasons', { headers: { origin: 'https://thekartoff.com' } });
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
  });
});
```

and in the `describe('public website reads (open + CORS)', ...)` block below it, update the `'leaves the other reads + writes token-gated'` test: the `/v1/seasons` expectation flips to `.toBe(200)` and the test renames to `'writes stay token-gated'` (drop the seasons line entirely — it's covered above — keeping only the POST 401 assertion). The first test of that block ('leaderboard / world-records / roster are open…') stays byte-identical — it must keep passing before AND after the flip.

- [ ] **Step 2: Run to verify the new tests fail** — `cd pi && npx vitest run src/api/app.test.ts` → the two new tests FAIL (`/v1/seasons` → 401), old write tests PASS.

- [ ] **Step 3: Implement the flip** — in `pi/src/api/app.ts` replace lines 31–48 (the comment block, `PUBLIC_READS`, `readCors`, the two `app.use(...:slug...)` lines, `OPEN`, both regexes, `isOpen`, and the gate `app.use`) with:

```ts
  // Reads are public (2026-07-05, spec §7): every GET — including the WS upgrade GETs and
  // /explorer — skips the token gate and gets permissive CORS for the cross-origin website.
  // Read-gating was a temporary measure. Writes keep Bearer tokens (enforced here for non-GETs
  // and again Bearer-only inside the write routes). Identity reads (/v1/me/*) still demand a
  // token via their own route-level requireToken — "me" is meaningless anonymously.
  app.use('*', cors({ origin: '*', allowMethods: ['GET'] }));
  app.use('*', (c, next) => (c.req.method === 'GET' ? next() : requireTokenAny(db)(c, next)));
```

- [ ] **Step 4: Flip the stale 401-read assertions** (each currently asserts a token-less read 401s):
  - `reads.test.ts:28` (`/v1/seasons` 401) → `expect((await app.request('/v1/seasons')).status).toBe(200);`
  - `reads.test.ts:76-84` (`/v1/trails` 401 test) → rename to `'token-less /v1/trails 200s with is_me false everywhere'`; token-less request now expects `200` and every row `is_me === false`; keep the tokened branch asserting `is_me` true for the caller.
  - `reads.test.ts:109-119` (`/v1/players/1/trails` 401) → expect `200` token-less (same payload assertions as the tokened case).
  - `reads.test.ts:36-48` (`/v1/me/pbs`, `/v1/me/pb-splits` 401 token-less) → **KEEP** — identity routes stay gated.
  - `courses.test.ts:32-35` → rename to `'two-segment course paths are open (404 until Plan-1b/2 routes exist, never 401)'`; assert `res.status === 404` and `res.status !== 401` for `/v1/courses/rainbow-road/model` token-less.
  - `players.test.ts:34-37` → rename to `'/v1/players/:id/pbs is public now'`; expect `200` token-less with the same body shape as the tokened assertion above it.
  - `screen.test.ts:58` (`/explorer` 401) → expect NOT 401. Keep `screen.test.ts:49` (write 401) exactly as-is.
  - `auth.test.ts` — untouched (unit-tests the middleware itself, still used for writes).

- [ ] **Step 5: Full pi suite + typecheck** — `cd pi && npm test` → ALL green (493+ tests). `npm run typecheck` → clean.

- [ ] **Step 6: Update `pi/CLAUDE.md`** "API gating" section: replace the PUBLIC_READS paragraphs with: *"Reads are public: every GET (incl. WS upgrades and `/explorer`) is token-less with permissive CORS. Writes require a Bearer header (never URL tokens). `/v1/me/*` reads require a Bearer token at route level (identity). Do NOT re-introduce read gating."*

- [ ] **Step 7: Commit** — `git add pi/src/api pi/CLAUDE.md && git commit -m "feat(pi): reads are public — drop PUBLIC_READS allowlist, gate writes only"`

---

### Task 2: Kit foundation — `web/src/kit/` tokens + jank + imperative helpers

**Files:**
- Create: `web/src/kit/tokens.css`
- Create: `web/src/kit/jank.js`
- Create: `web/src/kit/heroNum.js`
- Create: `web/src/kit/jank.test.js`
- Create: `web/src/kit/README.md`
- Modify: `web/src/lib/turf.js` (jank fns become re-exports)
- Modify: `web/src/main.js` (import `./kit/tokens.css` after the existing css imports)

**Interfaces:**
- Consumes: nothing (pure foundation).
- Produces (exact, used by Tasks 3+ and Plan 1b):
  - `jank.js`: `cardConfig(key, i) -> {shape, rot, ox, oy, fx}` and `digitJank(i) -> {rot, ty}` (MOVED here verbatim from `lib/turf.js`, same signatures); `TORN[n]` / `MASK[n]` for n=1..5 → CSS `polygon(...)` strings (the exact `.p1..p5` / `.m1..m5` polygons from `web/src/TurfLeaderboard.svelte:217-226`).
  - `heroNum.js`: `heroNumHTML(text, scale, {suffix='%', suffixRot=2} = {}) -> string` (the per-digit `<span class="d">…</span><span class="pc">…</span>` markup TurfLeaderboard builds today, jank from `digitJank`); `popEl(el)` (the `scale 1.16→1, 230ms, cubic-bezier(.3,1.6,.4,1)` WAAPI pop).
  - `tokens.css`: custom props on `:root` — `--ink:#101114; --ink-2:#191a1d; --paper:#f3f4f6;` the five player hexes as `--pc-gub:#38bdf8; --pc-aliias:#4ade80; --pc-paul:#a78bfa; --pc-luke:#f87171; --pc-alex:#fbbf24;` motion `--ease-slam:cubic-bezier(.3,1.55,.35,1); --ease-slide:cubic-bezier(.5,.05,.15,1); --ease-pop:cubic-bezier(.3,1.6,.4,1);` plus reusable classes `.kit-halftone` (the `radial-gradient(circle,var(--c) …)` 7px dot field at .16), `.kit-tag` (solid `var(--c)` slab, ink italic 900 text, hard shadow — the `.name` recipe), `.kit-num` (the `.num` hero-digit recipe: 900 italic, `-webkit-text-stroke` ink keyline, `paint-order:stroke fill`, colour drop shadow). All lengths written with the `calc(var(--s,1) * Npx)` scaling convention.

- [ ] **Step 1: Write failing tests** — `web/src/kit/jank.test.js`:

```js
import { describe, it, expect } from "vitest";
import { cardConfig, digitJank, TORN, MASK } from "./jank.js";
import { heroNumHTML } from "./heroNum.js";

describe("kit jank", () => {
  it("is deterministic and matches the shipped turf values", () => {
    expect(cardConfig("gub", 0)).toEqual({ shape: 1, rot: -1.6, ox: 5, oy: 5, fx: 0 });
    expect(cardConfig("aliias", 1)).toEqual({ shape: 2, rot: 1.4, ox: 5, oy: 4, fx: 12 });
    expect(digitJank(0)).toEqual({ rot: -4, ty: 0 });
    expect(digitJank(6)).toEqual({ rot: 5, ty: -5 });   // wraps mod 5
  });
  it("exposes the five torn card + mask polygons", () => {
    expect(TORN[1]).toMatch(/^polygon\(3% 2%,30% 0,66% 4%/);
    expect(MASK[4]).toMatch(/^polygon\(0% -40%,100% -40%/);
    expect(Object.keys(TORN)).toHaveLength(5);
  });
  it("heroNumHTML builds jank digits with a suffix", () => {
    const h = heroNumHTML("43", 1);
    expect(h).toContain('class="d"');
    expect(h).toContain(">4<");
    expect(h).toContain('class="pc"');
    expect((h.match(/class="d"/g) || [])).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd web && npx vitest run src/kit/jank.test.js` → FAIL (module not found).

- [ ] **Step 3: Implement** — `jank.js`: move `ROT/OY/FX/cardConfig/DIG_ROT/DIG_TY/digitJank` verbatim from `web/src/lib/turf.js:46-61`; add `TORN`/`MASK` objects holding the five `polygon(...)` strings copied character-exact from `TurfLeaderboard.svelte:217-226`. `heroNum.js`: extract the digit-markup loop from `TurfLeaderboard.svelte setNum()` (jank spans with `rotate/translateY(j.ty*scale)`, suffix span at `rotate(2deg)`) and the pop animation as `popEl`. `lib/turf.js`: delete the moved constants/fns and re-export (`export { cardConfig, digitJank } from "../kit/jank.js";`) so `lib/turf.test.js` + existing imports stay green. `tokens.css`: the custom props + `.kit-halftone`/`.kit-tag`/`.kit-num` classes with values copied exactly from `TurfLeaderboard.svelte`'s `.cf.dot::after`, `.name`, `.num` rules. `main.js`: `import "./kit/tokens.css";`. `README.md`: one page — what the kit is, the `--s` real-dimension scaling convention, the determinism rule, and a pointer to the spec.

- [ ] **Step 4: Run kit + full web tests** — `cd web && npm test` → all green (154+; `lib/turf.test.js` must pass UNCHANGED). `npm run check` → clean.

- [ ] **Step 5: Commit** — `git add web/src/kit web/src/lib/turf.js web/src/main.js && git commit -m "feat(web): kit foundation — tokens, deterministic jank, hero-num helpers"`

---

### Task 3: TurfLeaderboard onto the kit — zero visual change

**Files:**
- Modify: `web/src/TurfLeaderboard.svelte`

**Interfaces:**
- Consumes: `cardConfig`/`digitJank` (via existing `lib/turf.js` re-export or directly from `kit/jank.js` — import from the kit), `TORN`/`MASK`, `heroNumHTML`/`popEl`, tokens.css classes.
- Produces: identical rendered output; proof the kit reproduces the anchor.

- [ ] **Step 1: Refactor** — in `TurfLeaderboard.svelte`: import from `../kit/jank.js` + `../kit/heroNum.js`; `setNum()` body becomes `num.innerHTML = heroNumHTML(String(pct), scale); popEl(num);`; replace the five `.p*`/`.m*` clip-path CSS rules by setting `clip-path` inline from `TORN[cfg.shape]`/`MASK[cfg.shape]` in the markup (`style="clip-path:{TORN[cfg[i].shape]}"` etc.); replace the `.num`/`.name`/halftone style blocks with the `kit-num`/`kit-tag`/`kit-halftone` classes (keep any residual positional CSS local). DOM structure, imperative `place()/doSwap()/drive()` logic, and all timings stay byte-identical.
- [ ] **Step 2: Tests + check** — `cd web && npm test && npm run check` → green.
- [ ] **Step 3: Visual zero-diff proof** — run `npm run dev`; with headless Edge + CDP capture the turf page column at DPR 1 and DPR 2; capture the same states from `git stash`-ed pre-refactor build (or `git worktree` of `main`) and pixel-diff the column crops. Expected: no visible diff (AA-level noise only). Save the before/after pair to `docs/design/site-redesign/refactor-proof/`.
- [ ] **Step 4: Commit** — `git add web/src docs/design/site-redesign/refactor-proof && git commit -m "refactor(web): TurfLeaderboard onto kit — zero visual change"`

---

### Task 4: Sharpness audit + fix (kit-level crisp pattern)

**Files:**
- Modify: `web/src/TurfLeaderboard.svelte` (transform snapping)
- Modify: `web/src/kit/README.md` (document the crisp pattern)
- Create: `docs/design/site-redesign/sharpness/` (before/after device-pixel crops)

**Interfaces:**
- Consumes: Task 3's refactored component.
- Produces: the documented "crisp pattern" every 1b surface must follow.

- [ ] **Step 1: Capture the blur** — dev site → headless Edge CDP → device-pixel screenshots (DPR 1 and 2) of one L card + one R card, cropped to: hero digits, name tag, colour border edge, figure edge. Save as `sharpness/before-*.png`. Identify which elements are soft (user report: "some aspects… a little blurry").
- [ ] **Step 2: Apply candidate fixes ONE AT A TIME, re-capturing after each** (change-one-thing rule; keep what helps, revert what doesn't):
  1. Integer-snap the card transform: in `place()`, `translateY(${Math.round(slot * BASE * scale)}px)`; in `setBorder()`, round `nx`/`nt` components to whole px.
  2. Remove `will-change: transform` from `.rp` (or add it only for the duration of an animated reorder, removing on settle) so static rotated cards rasterize at final resolution instead of a cached layer being resampled.
  3. Round the `--fx` figure push and `.figmask img` bottom/height calc results to whole px at the callsite (`style="--fx:{Math.round(cfg[i].fx * scale)}px"`).
  4. If digits are the soft element: rebuild digits on scale settle (already done via `scaleChanged`) and ensure no ancestor `transform: scale` sneaks in from `WorldMap.svelte` sizing.
- [ ] **Step 3: Verify + document** — after the winning combination: save `sharpness/after-*.png`; confirm side-by-side at 2× zoom that resampling blur is gone (rotated-edge AA remains — that is correct smooth AA, not blur). Get user confirmation on their screen. Document the final rules in `kit/README.md` under "Crisp pattern" (integer device-px translates; no standing `will-change`; rotation allowed, resampling not; hi-res→downscale for rasters).
- [ ] **Step 4: Tests still green** — `cd web && npm test && npm run check`.
- [ ] **Step 5: Commit** — `git add web/src docs/design/site-redesign/sharpness && git commit -m "fix(web): turf card sharpness — integer-snapped transforms, no standing will-change"`

---

### Task 5: Roster chip placeholders → `web/public/chips/`

**Files:**
- Create: `web/public/chips/<player>__{idle,spawn,flourish}.webp` (5 players × 3 = 15 files)
- Create: `web/src/lib/chips-local.js` (tiny resolver) + `web/src/lib/chips-local.test.js`

**Interfaces:**
- Produces: `chipLoop(playerKey, kind) -> "/chips/<player>__<kind>.webp"` for kinds `idle|spawn|flourish`; files named by PLAYER key (not char/kart combo — the site cares about who, the combo is baked into the placeholder choice).

- [ ] **Step 1: Pick each roster player's representative combo** — check the live activity feed / `#/version` player list (or ask the user in the same message as the Task 6 mockup round if ambiguous) for each of gub, aliias, paul, luke, alex; then copy from `D:\kartoff\asset_chips\matte\<char>__<costume>__<kart>__{idle,spawn,flourish}_loop.webp` to `web/public/chips/<player>__<kind>.webp`. Check per-file size first (`ls -l`); if any loop exceeds ~4 MB, note it in the commit message as "placeholder, optimisation later" (user's stated plan) but still commit — ordinary binaries, never LFS.
- [ ] **Step 2: Resolver + test** — `chips-local.js`: `export const chipLoop = (key, kind) => `/chips/${key}__${kind}.webp`;` with a vitest asserting the three kinds and rejecting (returning null for) unknown kinds:

```js
import { describe, it, expect } from "vitest";
import { chipLoop } from "./chips-local.js";
it("builds chip urls per kind", () => {
  expect(chipLoop("gub", "idle")).toBe("/chips/gub__idle.webp");
  expect(chipLoop("paul", "flourish")).toBe("/chips/paul__flourish.webp");
  expect(chipLoop("gub", "nope")).toBeNull();
});
```

(Implementation: `const KINDS = new Set(["idle","spawn","flourish"]); export const chipLoop = (key, kind) => KINDS.has(kind) ? `/chips/${key}__${kind}.webp` : null;`)
- [ ] **Step 3: Run tests** — `cd web && npx vitest run src/lib/chips-local.test.js` → PASS.
- [ ] **Step 4: Commit** — `git add web/public/chips web/src/lib/chips-local.* && git commit -m "feat(web): roster chip placeholders + resolver (matte pipeline webps)"`

---

### Task 6: Mockup round — kit sampler + shell/nav → LOCK

**Files:**
- Create: `docs/design/site-redesign/kit-sampler.html` (LOCKED at end)
- Create: `docs/design/site-redesign/nav.html` (LOCKED at end)

This and Tasks 7–10 are **design rounds executed in the main session with the user** (not subagent tasks): build → user views in browser → iterate → LOCK → commit. Real assets: copy the needed figure/chip/map files next to the HTML or reference the dev server's `/public` paths; self-contained enough to open from disk or via `npm run serve`.

- [ ] **Step 1: Build `kit-sampler.html`** — one page, dark ground, showing the language OUT of the turf-card context: hero numbers (multiple sizes incl. non-% suffixes: `1:52.884`, `#1`, `30/30`), name tags for all five players, torn vs straight slabs side by side, halftone fields per player colour, an `InkTable` sample (quiet voice: real leaderboard rows — uppercase 11px headers, tabular figures, colour bars) and a `StatTile` sample (loud voice: value + rank tag), muted/zero state, and the hard-shadow/keyline recipes at 3 scales (`--s` 0.75 / 1 / 1.5) to prove crispness. Every value from `kit/tokens.css`.
- [ ] **Step 2: Build `nav.html`** — the real wordmark markup (reuse `Wordmark.svelte` output structure with the committed config values) + THREE active-marker treatments of the tab row (Live/Turf/Players/Tracks): (A) colour slab behind the active tab (torn edge), (B) thick ink-keyline underline bar with jank rotation, (C) the tab itself as a `kit-tag` name-slab. Random-player accent shown in at least two player colours.
- [ ] **Step 3: Present both in the user's browser; iterate in rounds** (one change-set per round, per house rule) until the user says LOCKED for each. Record each decision inline in an HTML comment block at the top of the file (`<!-- LOCKED 2026-07-XX: decisions: ... -->`).
- [ ] **Step 4: Commit** — `git add docs/design/site-redesign && git commit -m "design(web): LOCK kit sampler + nav reference"`

---

### Task 7: Mockup round — site-native live card → LOCK

**Files:**
- Create: `docs/design/site-redesign/live-card.html` (LOCKED at end)

- [ ] **Step 1: Gather real data** — from the live site/Pi (or the committed test fixtures if the Pi is unreachable): each roster player's name, colour, a real in-race state (course, lap, live time, PB, delta), a real idle state (career stats: firsts / runs·7d / pbs·30d), and PB-pace state. Use the Task 5 chips + `web/public/players/*.gif` figures.
- [ ] **Step 2: Build `live-card.html`** — the new loud-voice card in ALL states side by side: racing (chip idle loop as hero imagery, big italic timer, halftone progress fill with lap ticks, delta tag), selection screens (storyboard row: spawn-on-swap → flourish-on-confirm, animated via the actual spawn/flourish webps with a replay button), finished (beat-PB green / missed red per LiveSplit conventions re-cut as tags), idle (muted filter + career stats), offline (deeper mute), PB-pace (fire placeholder slot — final fire comes from Task 9). Wall context: a row of 5 at realistic wall size + one enlarged.
- [ ] **Step 3: Iterate rounds → LOCK** (as Task 6 Step 3; document the chip-choreography decisions — spawn interruptibility, flourish-on-finish yes/no — in the LOCKED comment).
- [ ] **Step 4: Commit** — `git add docs/design/site-redesign && git commit -m "design(web): LOCK live card reference"`

---

### Task 8: Mockup round — activity log + turf chrome → LOCK

**Files:**
- Create: `docs/design/site-redesign/activity-log.html` (LOCKED)
- Create: `docs/design/site-redesign/turf-chrome.html` (LOCKED)

- [ ] **Step 1: Build `activity-log.html`** — quiet-voice log with REAL recent rows (pull from `/v1/activity`): standard rows, a PB row with loud accent (colour slab + streak treatment candidate), system rows (Rank/Turf/WR), torn-cutout chips replacing the parallelograms. Show first/last-row corner handling.
- [ ] **Step 2: Build `turf-chrome.html`** — around a real map screenshot (capture the current turf page frame): print-transport scrubber (ink rail, per-snapshot colour ticks, blade playhead + play button as ink slab), console slab, date-stamp tag, and the hover popup re-cut in the kit (loud header: course name tag + WR hero-num; quiet body: 5-row board). Include the STREAK A/B strip here (spec §2.5): current gradient sheen vs hard print wipe vs none, on a looping side-swap demo.
- [ ] **Step 3: Iterate rounds → LOCK both files** (streak decision recorded in the LOCKED comment).
- [ ] **Step 4: Commit** — `git add docs/design/site-redesign && git commit -m "design(web): LOCK activity log + turf chrome reference"`

---

### Task 9: A/B round — fire → LOCK or keep incumbent

**Files:**
- Create: `docs/design/site-redesign/fire-ab.html`

- [ ] **Step 1: Build `fire-ab.html`** — side-by-side live loops on identical subjects (a live card figure, a map course icon, the wordmark): (A) INCUMBENT — the current goo-metaball engine verbatim (port the `Fire.svelte` loop into the page); (B) print/cel fire — 2–3 flat colour bands in the player colour, ink outline, torn tongue shapes, stepped ~12fps animation, halftone inner texture; (C, optional if B suggests it) hybrid: goo geometry, flat-band fill. Same size, same subject, same trigger.
- [ ] **Step 2: User picks in browser.** Incumbent wins ties (never-regress). If B/C wins → LOCK the file with the decision comment; if A wins → record `<!-- DECISION: keep incumbent goo fire -->` and the 1b fire work reduces to recolour/reuse.
- [ ] **Step 3: Commit** — `git add docs/design/site-redesign && git commit -m "design(web): fire A/B decision"`

---

### Task 10: A/B round — territory fills/front → LOCK or keep incumbent

**Files:**
- Create: `docs/design/site-redesign/territory-ab.html`

- [ ] **Step 1: Build `territory-ab.html`** — using a REAL captured map frame + the real territory canvas output at full map resolution (hi-res → downscale, verified in-browser only): (A) INCUMBENT fills + white front glow; (B) halftone-screened print fills (dot density carries ownership, terrain still reads through) + current front; (C) ink capture-front (hard printed edge, no white glow) on whichever fill wins visually. One variable changes per panel (change-one-thing).
- [ ] **Step 2: User picks; incumbent wins ties.** Record the decision comment; LOCK if a challenger wins.
- [ ] **Step 3: Commit** — `git add docs/design/site-redesign && git commit -m "design(web): territory A/B decision"`

---

### Task 11: Write Plan 1b against the locks

**Files:**
- Create: `docs/superpowers/plans/2026-07-XX-site-redesign-p1b.md`

- [ ] **Step 1:** Re-invoke `superpowers:writing-plans` with the LOCKED references as pixel truth. Plan 1b scope (from spec §4): implement shell/nav per `nav.html`; new `LiveCard.svelte` + wall per `live-card.html` (desktop `PlayerCard` retired from the site; realtime stores/clock reused); activity log per `activity-log.html`; turf chrome (scrubber/console/popup/datestamp) per `turf-chrome.html`; fire + territory per A/B decisions; kit Svelte primitives as the locked designs demand (`Slab`, `NameTag`, `StatTile`, `InkTable`, `SectionHead`, `FigureMask`, `Chip` wrappers); drop the `src/theme.css` import at the end (site fully on kit tokens); dev-pages (`/heat`, `/version`) token sweep; full-site real-browser verification.
- [ ] **Step 2:** Present the 1b plan for user review, then proceed to execution per the house SDD pattern.

---

## Self-Review (done)

- **Spec coverage (P1 scope):** auth flip → T1; kit → T2; turf refactor proof → T3; sharpness → T4; chip placeholders + choreography storyboard → T5/T7; shell/nav → T6→1b; live card → T7→1b; activity → T8→1b; turf chrome + streak decision → T8→1b; fire → T9; territory → T10; dev-page sweep + theme.css drop → deferred to 1b via T11 (implementation, needs no lock). Wordmark: unchanged this project (spec §1 — optional later round, correctly absent here).
- **Placeholders:** none — design-round outputs are the tasks' deliverables, with content requirements specified; all code steps carry code.
- **Type consistency:** `cardConfig/digitJank` signatures preserved; `TORN/MASK/heroNumHTML/popEl/chipLoop` used consistently across T2/T3/T5/T7.
