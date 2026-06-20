# Territory Map Polish — Motion, Model, Crispness, Layout — Design

**Status:** design approved by the user (this session). Ready for implementation plan.
**Part of:** the thekartoff.com territory map (SP1 foundation, SP2 overlay, SP3 popup, SP4 timeline — all built). This is a polish pass over SP2 + SP4.
**Scope:** `web/` only (the Vite+Svelte SPA). No server / endpoint / data-model changes.
**Touches:** `web/src/WorldMap.svelte`, `web/src/lib/territory.js`, `web/src/lib/territoryWorker.js`, `web/src/lib/timeline.js`, `web/src/TimelineScrubber.svelte`, and a small new animation module.

## 0. Already done (out of scope for the build)

Gub's map colour was reverted from teal `#2dd4bf` to Alex's old sky blue `#38bdf8` directly in `pi/mkw.db` (`players.color`, id 2) for the user's visual comparison. This is a transient local data edit, not code; `pi/mkw.db` is gitignored. Restore teal with the same one-line `UPDATE` if the comparison favours teal. The TS server boot does not re-seed colours, so the edit persists; only a manual Python re-import (`server/importer.py`) would overwrite it.

## 1. Partition model — a claim owns only its cell (#4)

### Problem
`territory.js:prepareOwners` seeds the Voronoi partition with **only claimed courses** (it `continue`s past any course with no colour). So with one owner claiming one course, every land pixel is "nearest" to that single seed and the **whole island** is painted that colour. Early-history snapshots are therefore wrong: a single claim paints everything.

### Design
Seed the partition with **all 30 courses, always**, and colour only the claimed cells:

- **Owner indexing.** Claimed courses sharing a colour share one owner index (so same-owner courses can merge — unchanged). All **unclaimed** courses collapse into **one** extra owner index flagged **non-paintable**.
- **`prepareOwners`** returns, in addition to `centers`/`ownerOf`/`ownerRgb`, a parallel `paintable: boolean[]` (true for real colours, false for the unclaimed index). Every course contributes a centre + an `ownerOf` entry (claimed → its colour index; unclaimed → the unclaimed index).
- **`gooeyPartition`** is unchanged in logic but now runs over all owner indices (claimed colours + the one unclaimed index). The unclaimed cells still occupy their Voronoi territory, so they **block colour bleed** between non-adjacent claims.
- **`paintLens`** skips any pixel whose resolved owner index is non-paintable (both the `ownerSm[p]` interior case and the `near[p]` coast-feather case) → that land renders as **plain terrain** (the muted base map shows through). This is the user's chosen "plain map terrain" look for unclaimed land.
- **`buildTerritory`** guard: return the empty layer when there are **no paintable owners** (start of history), not merely when `ownerRgb` is empty.

### Result
- One claim paints exactly that course's cell.
- Two same-owner claims merge into one region **only when their Voronoi cells touch** (the existing gooey blur-argmax merge) — the user's "shares an edge" rule, for free.
- Non-adjacent same-owner claims stay separate blobs (the unclaimed index sits between them).
- The **present** view (all 30 courses claimed in S1) is visually identical to today.

### Why unclaimed-as-one-index (not per-course singletons)
One merged unclaimed mask costs ~6 blur passes instead of ~30, and between two adjacent unclaimed courses the absent border is invisible anyway (both transparent). The small gooey erosion of a claimed cell against the large unclaimed mass is the same straight-boundary behaviour as a claimed-vs-claimed border (the blur radius `gooeyF·W ≈ 31px` is small relative to cell size), so claimed cells keep their shape.

## 2. Animation — borders that slide, no flash (#3, the hero)

### Problem (root cause)
Playback stacks **two semi-transparent territory canvases** (`terrA`/`terrB`) and cross-dissolves their `opacity`. Because each layer's territory alpha is the coast coverage (semi-transparent), at the 50% crossfade midpoint the whole territory composites at reduced effective opacity — tint and rims **dim then recover on every step**. That is the flash. And a captured region merely dissolves colour A → B instead of a border moving.

### Design — single canvas, static base, animate only the changed cells
Delete the dual-canvas opacity crossfade entirely. One visible `.territory` canvas. The territory for snapshot *i* is fully painted **once** as a static **base frame**. Advancing *i → i+1* animates **only the contested cells**; the rest of the canvas is never redrawn, so there is **zero flash** by construction.

**Border-push via partition interpolation.** For the transition:

1. **Flip set** `F` = courses whose owner index differs between snapshot *i* and *i+1* (covers capture A→B *and* first-claim unclaimed→owned). A pure helper `flippedCourses(snapA, snapB, manifest)`.
2. **Change region** `B` = the union of the flipped courses' Voronoi cells (pixels whose nearest course ∈ F), padded by the gooey blur radius. Computed from a nearest-**course** field (precomputed once per transition).
3. **Animate** τ: 0 → 1 over the transition, eased (smootherstep).
4. **Per frame, within `B` only:** build per-owner **weighted** masks — a flipping course contributes `(1−w)` to its old owner and `w` to its new owner, where `w` is a **centre-out growth front** of the new owner (`w = smoothstep(τ·reach − distFromCourseCentre)`); non-flipping pixels contribute weight 1 to their owner. Blur the weighted masks, argmax → local `ownerSm`, recompute the local border-distance field, run `paintLens` → an RGBA patch, composite the patch over the static base.

Because `paintLens` derives the bright owner-light rim from the live distance field each frame, **the gooey border carries its rim as a moving front** — a real border push. For an **adjacent capture**, the new owner already borders the cell, so the front reads as the boundary **sliding across** the cell. For an **isolated first-claim** (no adjacent same-owner land), the same centre-out front reads as the territory **expanding outward from the course**. One unified rule; both are "real borders expanding."

At τ=1 the visible canvas equals base frame *i+1* everywhere (unchanged region untouched = identical; changed region painted to final), and the current base advances to *i+1*.

### Rendering & compositing
- The **base frame** render stays in the existing Web Worker (heavier, once per snapshot). The **per-frame transition patch** computes on the **main thread over the small bbox** (cheap — typically one cell) and is composited onto the canvas, so there is no per-frame worker round-trip / latency.
- The transition patch is computed at the canvas **backing resolution** (×2 supersample over the small bbox, then downscaled) so it stays anti-aliased (per §3) **and** aligns seamlessly with the base frame. In timeline mode the base frame is rendered to the same backing resolution so base and patch match at the seam.
- **Multiple simultaneous flips** (same `t`, common in the dense early history) animate **together** under one τ — `B` is the union (or per-cell patches). This also removes the early-history "crawl"/repeat-date stutter where many courses flip on the same day.

### Playback & scrubbing
- **Play:** step through snapshots, each transition animated over a fixed beat (tunable, ~450–700 ms), eased; park at LIVE; restart from 0.
- **Scrub (drag):** instant hard-set to the target snapshot (re-establish the base frame, no animation). Single-step nudges may animate one transition.

### Out of scope
No change to which snapshots exist (`buildSnapshots` unchanged) or to strength/dominance colouring (still deferred).

## 3. Render crispness (#2)

### Problem
The canvas backing store is a **fixed `DW=1100`** regardless of device pixel ratio, so on any high-DPI display the territory is upscaled and softened. Timeline frames are worse: rendered at `TL_RENDER_W=1100` and painted **1:1** (no supersample), so the rims alias while scrubbing.

### Design
- Size the canvas **backing store to real device pixels**: `backingW = round(displayCssWidth × devicePixelRatio)`, capped at the asset width (2200); `backingH` by aspect.
- Always render the source at **≥ the backing store** and **downscale** into it — never upscale a smaller canvas (the project's "hi-res → downscale" rule). The present already renders at 2200 → it now downscales into a correctly-sized backing. **Timeline frames render supersampled** to ≥ backing (same AA as the present).
- **Rebalance the frame cache** for the new (smaller, per §4) display size: with the map sized to fit the viewport its CSS width drops, so the backing (≈1400–1700 at DPR 2) and per-frame bitmap bytes are bounded; set `TL_CACHE_CAP` so `cap × frameBytes ≲ 150 MB`. Recompute exact numbers at build against the chosen map size.
- §2 transition patches inherit this crispness (they compute at backing res ×2).

## 4. Page layout — fit on screen, composed (#5, frontend-design)

### Problem
The 1100px map frame plus the scrubber stacked **beneath** overflows the viewport, forcing a scroll to reach the controls, and the view reads as an unframed dropped image ("dumped the map and moved on").

### Design — controls on top, smaller map, fit to viewport (user's pick)
A composed column under the sticky site header (`web/src/App.svelte` header stays):

- **Title row** — a restrained "Territory" heading + season label (and the date / LIVE echo).
- **Transport / scrubber** — the existing `TimelineScrubber`, relocated **above** the map and restyled into the composition (play/pause + scrub + date readout).
- **Legend / standings** — a compact strip of player colour swatches + names + current PB-count standings, so the map reads as a deliberate panel and colours are legible. Kept minimal and restrained.
- **Map** — sized to **fill the remaining viewport height** so the whole view fits without scrolling: `mapH = 100vh − header − title − transport − legend − gaps`, `mapW = mapH × (2200/1775)`, capped at a sensible max; centred. The map is smaller than today, which §3 leverages for cache headroom.
- **Chrome** — graphite tokens from `web/src/theme.css`, consistent with the rest of the site; calm-at-rest base preserved.
- **Responsive** — on narrow viewports the pieces stack and vertical scrolling is allowed.

The **frontend-design skill** drives the visual specifics (spacing, type scale, legend treatment, how the transport and map frame relate) at build time.

## 5. Module boundaries

- `web/src/lib/territory.js` — pure partition + paint. Gains the all-courses seeding + `paintable` mask (§1). Stays DOM-free + unit-tested.
- `web/src/lib/timeline.js` — `buildSnapshots` unchanged; add a pure `flippedCourses(a, b)` helper (§2.1) — unit-tested.
- new `web/src/lib/territoryAnim.js` — pure transition math: flip set → change bbox → per-frame weighted-mask interpolation → RGBA patch. DOM-free, unit-tested. (The rAF loop + canvas compositing live in `WorldMap.svelte`.)
- `web/src/lib/territoryWorker.js` — base-frame render; minor wiring for backing-res output (§3).
- `web/src/WorldMap.svelte` — single territory canvas, DPR-aware sizing (§3), the transition rAF loop + compositing (§2), new fit-to-viewport layout (§4). The dual-canvas crossfade is removed.
- `web/src/TimelineScrubber.svelte` — relocated above the map; restyled into the composition (§4). Behaviour unchanged.

## 6. Testing & verification

- **Unit (vitest):**
  - `territory.test.js` — extend: all-courses seeding; one claim paints only its cell (sample a pixel in a *different* course's cell → transparent); two adjacent same-owner claims merge, two non-adjacent do not; present (all claimed) unchanged.
  - `timeline.test.js` — `flippedCourses` returns exactly the changed slugs (capture + first-claim + no-change).
  - `territoryAnim.test.js` — bbox covers the flipped cells (+margin); at τ=0 the patch equals base *i* in `B`, at τ=1 equals base *i+1*; an unchanged owner index never appears/disappears mid-interpolation.
- **Visual (headless Edge / CDP, never OpenCV — the project's standard):** drive the SPA on vite dev (:1430) with pi dev (:8787); wait on a DOM predicate (date readout / a "transition done" sentinel) before `Page.captureScreenshot`. Confirm: (a) a mid-transition frame shows the border **moved partway** with the rest of the map **identical** to the pre-frame (no global brightness dip = no flash); (b) crisp rims at DPR 2; (c) the whole view fits a 1080p viewport with no scroll; (d) early-history single claim colours only its cell.
- **Smoke:** `svelte-check` 0/0; `vite build` of `/#/map` (module-worker bundling only verified under dev historically).

## 7. Risks

- **Patch/base seam.** A visible seam at `B`'s edge if patch and base resolutions/looks diverge. Mitigated by computing both at the same backing resolution in timeline mode (§2). Verify at the seam in CDP screenshots.
- **Simultaneous-flip bbox size.** Many same-`t` flips spread across the map could make `B` large. Mitigated by per-cell patches if the union is too big; the early dense churn is mostly clustered.
- **Isolated-claim pop.** The centre-out growth front must be tuned so a first-claim grows rather than snaps at τ≈0.5. A tuning item, validated visually.
