# Turf ownership leaderboard column — design (LOCKED)

**Date:** 2026-07-03
**Area:** `web/` (thekartoff.com) — the Turf page (`WorldMap.svelte`)
**Status:** **Design + animation model LOCKED.** Ready for the implementation plan.

**Pixel-exact references (committed, self-contained — open in a browser):**
- `docs/design/turf-leaderboard/turf-card-design.html` — the static card design (source of truth for all CSS, shapes, colours, offsets).
- `docs/design/turf-leaderboard/turf-animation-prototype.html` — the running animation model (▶ Play). Reference implementation for the JS.

This spec is authoritative for architecture + constants; where it says "see reference," the HTML is the exact truth.

---

## 1. Summary

A stylised turf-ownership leaderboard down the **left** of the Turf page. Each player is a ragged, slanted "ransom-note" card showing their share of the 30 courses (**% = coursesOwned ÷ 30**). The column **re-ranks + resizes-free (uniform cards)** and its numbers **tick in lockstep with the map's territory sweep** during playback. Persona 5 / NEO:TWEWY energy — sharp, janky, controlled chaos.

The map bumps right; the timeline scrubber becomes a **full-width bar across the top**, with the column + map side by side beneath it. **No backend changes** — everything derives from data `WorldMap.svelte` already loads.

## 2. Layout / page structure

`WorldMap.svelte` today: vertical `[console][map]`, centered, fit-to-viewport. Change to:

```
.map-view (column, fit-to-viewport, no page scroll)
├─ .console            ← TimelineScrubber, FULL WIDTH across the top
└─ .appbody (row)
   ├─ TurfLeaderboard  ← fixed 172px column, left (cards right-aligned to the map edge)
   └─ .frame (map)     ← fills remaining width, right
```

- `fitMap()` must subtract the column width (172px) + gap from the available width before sizing the map, so it still fits the viewport with no page scroll. Map internals (base, territory canvas, MapFireLayer, icons, popups, datestamp) unchanged.
- **No "TURF WAR" title** on the column.
- Below ~760px the row stacks (column above the map at reduced scale); never force horizontal page scroll.

## 3. Card anatomy — every card is UNIFORM size

Rank is read from **top-to-bottom order** (and the number itself), **not** size. Cards do **not** shrink by rank.

| Property | Value |
|---|---|
| Card `.rp` | 160 × 110 px, `overflow: visible`, gap 10px, right-aligned (`align-items:flex-end`) |
| Column width | 172px |
| Colours | gub `#38bdf8` · aliias `#4ade80` · paul `#a78bfa` · luke `#f87171` · alex `#fbbf24` |
| Slab `.cf` | `#191a1d` + halftone: `radial-gradient(circle,var(--c) 1px,transparent 1.5px)` 7px, opacity .16 |
| Rotation (per player, fixed) | gub −1.6° · aliias 1.4° · paul −1.9° · luke 1.5° · alex −1.2° |

**Shape** — 5 janky torn `clip-path` polygons `j1..j5`, assigned **per player** (fixed; they do NOT change on reorder). The figure mask `m1..m5` = the same polygon with the **top edge opened to `(0 -40%),(100 -40%)`** so sides+bottom stay cut to the card but the head protrudes. Exact polygons: see reference `turf-card-design.html`.

**Two-sided colour border `.ck`** — `background:var(--c); opacity:.92`, offset behind `.cf` via `transform:translate(±ax, oy)` so it peeks on two edges (registration-off / cartoon border). `ax = 5`; `oy` per player (gub 5 · aliias 4 · paul 4 · luke 3 · alex 3). **The X sign follows the card's side** (L → `+ax`, R → `−ax`) so the border **mirrors to the opposite two edges on a side-swap** (and slides across during the swap animation).

**Figure** — the player's online card figure (`figureFor(name, true)`), **masked to the card shape** (`m1..m5`): contained on sides+bottom (cut to the border), only the **head pops over the top**. `bottom:-6px; height:138px` so the feet sit **below** the card bottom and are hidden behind the border (never a visible bottom edge). Horizontal side follows **slot parity** (L card → figure right; R card → figure left). Per-figure edge push `--fx` (aliias `12px`, others `0`): L → `right:calc(-1*--fx)`, R → `left:calc(-1*--fx)` so a wide figure hugs the border. **Aspect ratio is never distorted** (no squish).

**Percentage `.num`** — pinned **near the top, inset ~11px from the corner** (not jammed to the edge), side by slot parity (L → left, R → right). `Inter 900 italic 44px`, white, thin keyline `-webkit-text-stroke:2.2px #101114` (`paint-order:stroke fill`), cartoon colour drop shadow `text-shadow:3px 3px 0 var(--c),1px 2px 0 rgba(0,0,0,.5)`. **Per-digit "ransom" jank** — each digit its own transform, deterministic by index: `rotate` ∈ `[-4,5,-3,4,-5]`, `translateY` ∈ `[0,-5,3,-4,2]`. `%` sign `.pc`: `.5em`, `color:var(--c)`, stroke `1.2px #101114`. Single digits sit **naturally** (no special alignment). *(A slab-cutout treatment was explored and rejected — keep this.)*

**Name tag `.name`** — **right beneath** the %, same side. Solid `var(--c)` slab, `#101114` italic 900 11px, hard shadow. **No rank number.** **Natural case** (so `paul pork` stays lowercase; others `Gub`/`Aliias`/`Luke`/`Alex`).

**Muted 0%** — a card at 0 courses gets `filter:saturate(.32) brightness(.72)` (a hint of colour left), `transition:filter .45s`. Always shown (0% cards sit at the bottom).

**z-index** — `z = 10 + slot`, so **lower players sit on top** and their popped-out head overlaps the card above.

## 4. Data & reactivity

New pure helper **`web/src/lib/turf.js`**:

```
turfStandings(snapshot, colors, totalCourses=30) -> [{ player, color, courses, pct, rank }]  // sorted courses desc
```
- Count courses per player from `snapshot.owners`.
- **Include every roster player** (`Object.keys(colors)`), even at 0 → 0% cards at the bottom, muted.
- `pct = round(courses / totalCourses * 100)`; sort by courses desc, **tie-break player name asc**; `rank` 1-based.

Plus deterministic jank helpers (shape index, rotation, border offset, digit transforms) — **pure functions of (playerKey / rank / digit index), never `Math.random()`**, so nothing shimmers between frames.

Inputs already in `WorldMap.svelte`: `snapshots[tlIndex].owners`, `tlColors` (roster = its keys), `manifest.courses.length` (=30). Standings recompute when `tlIndex` (the shown frame) changes — LIVE = last snapshot; **scrub = hard cut (snap, no ticking)**.

## 5. Animation model (during PLAY only)

Rides the map's existing playback engine: `animateTransition(from,to)` sweeps a territory front at constant `FRONT_SPEED` over a computed `dur` (clamped 320–5000ms), coalescing adjoining same-owner captures via `runEnd()`. The column reads the **same progress**.

- **Numbers tick** — interpolate each player's course count `from→to` over the step's `dur` (same clock/`tau` as the front). `pct = round(courses/30*100)` updates on each integer change → the winner climbs (e.g. 40→41→42→43) as the loser ticks down, landing when the sweep finishes. **Scale-pop** on each change: `scale 1.16→1`, 230ms `cubic-bezier(.3,1.6,.4,1)`.
- **Reorder (slide)** — cards keyed by player; each card `transform:translateY(slot*120px) rotate(rot)`, `transition:transform .44s cubic-bezier(.5,.05,.15,1)`. Fires the instant the **interpolated counts cross** (so it's timed to the front reaching the deciding cell).
- **z-swap at slide midpoint** — update `z-index = 10+slot` at ~half the slide (`~220ms`), while the two cards are coincident, so the head-overlap z-flip is **invisible**.
- **Side-swap (SLIDE + streak — CHOSEN; no fade)** — when a card lands on the opposite parity: FLIP the header + figure across via WAAPI `translateX` with an **overshoot** ease `cubic-bezier(.3,1.55,.35,1)` (`SWAP≈420ms`); a **colour streak** sweeps across (`.streak b`: `translateX -160%→360%, skewX -14deg, opacity .9→0`); and the **colour border mirrors** by animating `.ck`'s translate old→new (same ease). *(A card-flip style was prototyped as the alternative and NOT chosen.)*
- **0%** — a player who loses their last course ticks to 0 and the card eases to muted (the `filter` transition).

Timings (tunable): `STEP=120`, `SWAP≈420ms`, slide `.44s`; the tick `dur` = the map step's real clamped `dur`. Full working logic: reference `turf-animation-prototype.html`.

## 6. Assets

- **Luke re-crop — DONE & baked.** `gen_player_figures.py` `FIGURE_CROP`: `("luke","on")=(0,0,1,299/354)` → **144×260** (was a thin 122); `("luke","off")=(0,0,1,272/340)` → **219×260**. Applied after the alpha-bbox crop, before the 260px normalise; survives a regenerate. Onpace untouched.
- **Aliias** — **no asset change** (his width is an angle-of-photo thing; do NOT squish/distort). Handled purely by the column's `--fx` edge push. A future honest fix would be re-cropping his source.
- **Figures** — the plain `__on` card figures via `figureFor`, **masked to the card in-component**. No baked borders/busts (those experiments were reverted).

## 7. Components & files

- **`web/src/WorldMap.svelte`** (edit) — layout restructure (full-width console; `.appbody` row = column + map); `fitMap()` subtracts the column width; compute `standings` from `snapshots[tlIndex]`/`tlColors`/course-count; expose the play animation progress (`tau` + `dur`, or emit per-frame) to the column.
- **`web/src/TurfLeaderboard.svelte`** (new) — the column + cards + all animation (ticking, slide, z-midpoint, side-swap slide+streak, border mirror, mute). `turf-animation-prototype.html` is the reference implementation.
- **`web/src/lib/turf.js`** (new) — pure `turfStandings(...)` + deterministic jank helpers.
- **`web/src/lib/turf.test.js`** (new) — unit tests.
- **Reused:** `src/lib/playerFigures.js` (`figureFor`), `src/lib/playerKey.js`.
- **`scripts/gen_player_figures.py`** — already edited (Luke crop).

## 8. Testing

- **`turf.test.js`** (vitest): course counting; 0-turf roster players included + last (muted); unowned courses excluded; `pct` rounding; sort + name tie-break; rank; **jank determinism** (same input → same transform).
- **Manual/visual:** the two committed references are the source of truth; verify the live column against `turf-card-design.html` and the motion against `turf-animation-prototype.html` (play the timeline; watch a rank swap for the border mirror, streak, and invisible z-flip).
- No backend/engine changes → no pi/cargo/engine test impact.

## 9. Deferred (post-implementation, all minor)

- Fine-tuning: border offset amount, Aliias's `--fx`, tick speed, streak intensity.
- The **% "cutout"** treatment (explored, currently reverted to the plain keyline+shadow).
- **Leader on fire** (reuse `Fire.svelte` on #1).
- **Hover → territory lens** (hovering a card isolates that player's territory on the map). v1 is **display-only**.

## 10. Resolved decisions (why it is the way it is)

- React to the shown timeline frame (not present-day only). ✓
- Figures **static** (no live gifs). ✓
- **Uniform** card/figure/number size — the earlier size-gradient was rejected. Rank = order + the number. ✓
- **% pinned top, name beneath, header alternates L/R** by slot; figure on the opposite side. ✓
- Figures **masked to the card** (sides/bottom cut to the border), **only the head pops the top**, feet hidden below the bottom border. ✓
- **Two-sided colour border mirrors on side-swap.** ✓
- Side-swap is **slide + streak** (kinetic), **not** a fade, not a card-flip. ✓
- **z by slot**, swapped at the slide midpoint so it's unseen. ✓
- Numbers **tick in sync with the territory sweep**, up and down. ✓
- Show **all** roster players; **0% muted** at the bottom. ✓
- **Display-only** for v1. ✓
- Luke re-cropped (asset-level, app-wide). Aliias **not** distorted. paul → **paul pork** (lowercase). ✓
