# Site redesign — "KART-OFF print" language + Tracks/Players completion (umbrella spec)

**Date:** 2026-07-05
**Area:** `web/` (thekartoff.com) + additive `pi/` endpoints. Desktop app untouched.
**Status:** Umbrella design, user-approved section by section. This spec fixes direction,
requirements, and decomposition for THREE build projects; each project locks its pixel truth via
committed reference HTML (`docs/design/site-redesign/`) and gets its own plan.

---

## 1. Summary

The turf leaderboard cards (`TurfLeaderboard.svelte`, spec 2026-07-03) are the only part of the
site the user likes. Their visual system becomes the design language of the ENTIRE site (and,
later, the user's OBS assets — out of scope here but the kit must be exportable). Everything else
is rebuilt in that language: shell/nav, Live cards, Turf page chrome (with full-repaint license on
fire + territory, A/B-gated), and the Players and Tracks sections — which additionally get the
functionality that was cut from their v1s (track map with replays/heatmap/death spots, flavor
stats, trends, head-to-head, loadout).

Nothing is off-limits except: URL structure, the data model, the desktop app, and non-additive Pi
changes. Previously-dismissed ideas may be re-explored. The wordmark is NOT locked — it stays as-is
through the initial builds, but the user is open to alternatives if it stops fitting once the new
language lands (revisit after P1's mockups establish the look; its own small round).

## 2. The design language ("KART-OFF print")

Codified from the turf cards; all six rules apply site-wide:

1. **Ink & paper.** Near-black slabs (`#191a1d` card / `#101114` ink) carry everything; white
   type; the per-player brand colours (and per-track colours) do ALL colour work. No decorative
   gradients, no glass, no glow.
2. **Print physicality.** Registration-offset colour borders (mirroring by side), hard un-blurred
   shadows, keyline-stroked type (`-webkit-text-stroke` + `paint-order`), torn clip-path edges,
   fixed per-item rotations (±1–2°), halftone dot fields as texture.
3. **Type.** Inter only (the language is built on it; a wordmark revisit, if it happens, is its
   own later round). Inter 900 italic for hero numbers/identity; tabular figures for data. Hero
   numbers keep per-digit ransom jank.
4. **Deterministic jank.** All raggedness is a pure function of identity (player key / slug /
   index) — never `Math.random()`. Nothing wobbles between renders.
5. **Kinetics on state-change only.** Overshoot slams, FLIP slides, scale-pops on value change.
   No idle animation on static content. The side-swap streak (the "shimmer") is A/B'd in mockups:
   keep / harder print wipe / remove.
6. **Two voices, one DNA.** **Loud voice** (torn slabs, rotation, jank digits, figure pop-outs)
   for standings, heroes, identity moments. **Quiet voice** (straight-edged ink panels, same type,
   same colour rules, hard 1px rules, halftone accents) for data tables, splits, histories, logs.
   Front-page headline vs body copy of the same newspaper.

The site drops `src/theme.css` (the desktop graphite tokens) entirely; a self-contained kit
replaces it. This also ends the current "two visual worlds" split (token-driven vs hardcoded
pages, including the phantom `--line` token).

## 3. Design process (how pixel truth gets locked)

Reference-HTML rounds — the process that produced the turf cards, per surface family:
self-contained mockup HTML with REAL assets + REAL data, iterated in the user's browser, winners
committed to `docs/design/site-redesign/` and LOCKED; each project's spec/plan points at them as
pixel truth. Mockup rounds are the first task of each project's plan.

**Never-regress A/B rule:** for anything that already looks good (fire, territory fills/fronts,
streak), the current visual is the incumbent and only loses to a clearly better mockup. Change one
thing at a time.

Craft rules that survive the redesign: smooth AA always (render hi-res → downscale, never CSS
upscale); verify composited visuals in a REAL browser (headless Edge + CDP), never OpenCV; dev
site at `http://127.0.0.1:1430` (not localhost).

## 4. Project 1 — Design kit + shell, Live, Turf

**Kit (`web/src/kit/`):** `tokens.css` (ink scale, player colours, type scale, spacing, hard
shadow + keyline recipes) and primitives: `Slab` (torn/straight), `NameTag`, `HeroNum` (jank
digits, generalised from `lib/turf.js`), `HalftoneField`, `StatTile`, `InkTable`, `SectionHead`,
`FigureMask` (head-pop masking), `Chip`, motion utils (slam / pop / streak). Proof of the kit:
`TurfLeaderboard` refactors ONTO it with zero visual change.

**Shell/nav:** wordmark unchanged for now (alternatives explicitly allowed later — §1). Tabs
re-cut in the language; active marker becomes a colour slab / torn treatment (mockup decides).
The random per-load player accent stays.

**Sharpness requirement (kit-level, P1):** some turf-card content currently renders slightly
blurry rather than totally sharp. Audit and fix in the kit so every primitive renders crisp at any
`--s`: suspects are fractional-pixel transforms on rotated, `will-change`-promoted layers
(rasterized then resampled), subpixel translateY slots, and stroke-on-italic at fractional font
sizes. Kit rule: snap layout transforms to device pixels where possible and document the crisp
pattern. Honest boundary: edge antialiasing on rotated/clipped shapes is correct smooth AA and
stays; the target is eliminating *resampling* blur. If some residual softness proves inherent to
rotated rasterization, document why and minimise it.

**Live:** desktop `PlayerCard` retired from the site (desktop app keeps it). New site-native loud
card: torn slab, offset colour border, figure with head pop, name tag, big italic timer, halftone
progress fill, colour-tag PB deltas. Racing = full saturation + kinetics; idle/offline = the muted
filter from 0% turf cards. All realtime logic (presence stores, ~30fps clock, progress, deltas) is
reused — view layer only. **Chip choreography** (design intent, tuned in mockups): matte-chip slot
plays `spawn` on kart/character swap during selection screens (interruptible by the next swap),
`flourish` on a confirmed selection, `idle` loop while racing; `flourish` is a candidate for
race-finish celebration.

**Activity log:** quiet voice; PB rows get loud accents (colour slab + streak); chips become torn
cutouts.

**Turf page:** leaderboard cards stay as-is (kit-refactored). Chrome re-cut: scrubber → print
transport (ink rail, colour ticks, blade playhead), console slab, popups → kit popup, date stamp →
tag slab. Two full-repaint explorations, each strict A/B vs incumbent: **fire** (goo metaballs vs
cel/print fire — flat colour bands, ink edges; affects `MapFireLayer`, wordmark fire, new site
fire) and **territory** (terrain-derived fills vs halftone-screened print fills; white front glow
vs ink capture edge).

**Dev pages** (`/heat`, `/version`): light token sweep only.

## 5. Project 2 — Tracks (build order: after Project 1; worst offender first)

**Index (`/tracks`):** the CoursePopup-grid dies. Each track = a **selection-icon tile**: square
course icon artwork in a torn slab frame + ink keyline + course name tag + the full 5-row
mini-leaderboard (roster is 5 players — the index stays a true one-page leaderboard wall, now with
icon identity) + fire when lit. Overall total-time card = one special hero tile, pinned first.
Mockup decides tile arrangement (board under/beside/over icon); fallback if icon+board genuinely
doesn't work: icon-led tiles + pinned Overall board. Search stays, re-cut. Icons: mariowiki course
selection icons as placeholders (`web/public/tracks/icons/<slug>.png`), user swaps in own captures
later under the same filenames; fallback if sourcing is a pain: name-slab tiles.

**Hub (`/tracks/:slug`):** hero header (selection icon, name slab, WR jank digits, holder tag,
char/kart chips, video tag). Centerpiece: the **track map** with toggleable layers as tag slabs:

- **Outline** — course-model route polylines.
- **PB lines** — per-player colour trails.
- **Replay** — animated PB dots on the race clock (reuses `src/lib/overlay.js` interpolation).
- **Heatmap** — server-rasterized run-density grid (reuses the `lapGraphCV` splat kernel; cached).
- **Death spots** — where runs die. Data source verified at plan time: stored last trail points of
  unfinished runs if present, else completion-at-reset mapped through the course-model route.

**Map coordinate space:** the per-course minimap ROI from the desktop app (exported ONCE by a tiny
script from the desktop SQLite into a committed JSON). The map canvas = the ROI's bounding box in
1080p common-frame space; trails/models draw with a crop-offset transform only — no calibration.
v1 background = ink-ribbon render from the course model; the user's later ROI-exact screenshots
drop in pixel-registered by construction. (Mariowiki main-track map artwork: DROPPED, and with it
the whole registration/calibration tool.) If replacement artwork ever changes framing,
re-registration is per-course minutes, not a project.

Below the map, quiet-voice sections: leaderboard, lap splits + theoretical best, history
(progression / reigns / WR history), on-fire target as loud callout with the new fire, optional
recent-runs (see §8). **Loadout: DROPPED** (user 2026-07-05) — the WR line and WR history already
show the character/kart each record was set with, which answers the question.

## 6. Project 3 — Players

**Index (`/players`):** roster wall in the loud voice — large portrait-cut cards (figure head-pop,
offset colour border, name tag) with **total time** as the headline stat in jank digits (NOT turf %
— the turf page owns that). Fixed roster order stays.

**Profile (`/players/:slug`):** hero band — big figure, name tag, four standings tiles as mini
loud cards (Turf % / Total time / Golf / % off WR, value in jank digits + rank tag). **Flavor
band:** most-played character and kart WITH chip imagery (matte stills as placeholders; user's
face/kart screenshots later), coins, attempts / finishes / reset rate, driving time. **Trend
charts** in print style (ink axes, player-colour series, halftone fills; no chart-library chrome).
PB table in quiet voice.

**Strategy:** the GOLF/TURF/TIME kernel and its three uses are KEPT (they're important), but the
current display (segmented toggle + advice list) is REJECTED — presentation is fully rethought in
the mockup round, and where strategy surfaces (profile, head-to-head, its own view) is a mockup
decision.

**Head-to-head:** shareable route `/players/:a/vs/:b` — split hero (both figures facing off, VS
slab), standings tiles side-by-side, per-track PB table with winner-tinted rows, "snipeable
tracks" strip (strategy kernel). Composed CLIENT-SIDE from two existing player summaries — zero
new endpoints.

## 7. Server changes (pi/) — all additive except the auth flip

1. **Auth simplification (the first task of Project 1, its own commit):** reads become public — every GET drops the
   token gate (including `/v1/stats/*` and porker body metrics — user-confirmed the gating was
   temporary), CORS opens on all reads, the `PUBLIC_READS` whitelist + per-route regex exceptions
   are deleted. **Writes keep the token.** Existing gate tests flip (e.g. the 401 guard on
   two-segment `/v1/courses/*` paths becomes a 200 test).
2. **Track map data:** `GET /v1/courses/:slug/model | trails | heatmap` (heatmap server-rasterized
   + cached) + a death-spots source (own endpoint or a field on trails/summary — plan decides).
3. **Flavor:** `GET /v1/players/:slug/flavor` — one composed payload (most-played char/kart via
   breakdown, coins/attempts/finishes/reset-rate/driving-time values, weekly-activity +
   PB-improvement series) so a profile isn't 8 round-trips.
4. **Recent runs (conditional, see §8):** trivial curated reads, e.g.
   `GET /v1/courses/:slug/runs?limit=N` / `GET /v1/players/:slug/runs?limit=N`.

## 8. Assets

- **Matte chips** (`D:\kartoff\asset_chips\matte`, `<char>__<costume>__<kart>__{spawn,idle,flourish}_loop.webp`):
  placeholder hero imagery. Only roster-needed combos are copied into `web/public/chips/`
  (ordinary git binaries, NEVER LFS), lazy-loaded. Full captures for every char×costume(×kart)
  exist at `D:\kartoff\captures_sdr\en_uk\clips` with a processing pipeline — missing combos can
  be generated on demand. **Known concern (resolve before any mass generation):** the export ROI
  was fitted to a small character and is untested for big ones; chips should share ONE consistent
  ROI so characters stand in a consistent location regardless of size.
- **Course selection icons:** mariowiki placeholders now; user's captures later, same filenames.
- **Track map artwork:** NONE needed now (ink-ribbon from model); user's ROI-exact screenshots
  later, drop-in registered.
- **Recent-runs sections** (tracks + players) are OPTIONAL: include for both/either/neither purely
  on whether they earn their place in the mockups.

## 9. Testing

- **vitest:** kit helpers (jank determinism, torn-polygon generation, ROI transform math), flavor
  composition, overallBoard/turf helpers unchanged, strategy kernel untouched.
- **pi tests:** auth flip (all reads 200 token-less; writes 401 token-less), new endpoints
  (model/trails/heatmap/flavor shapes, heatmap cache behaviour, death-spots source).
- **Visual:** locked reference HTML is pixel truth; real-browser verification (headless Edge +
  CDP); A/B incumbent gates for fire/territory/streak; `svelte-check` + full web/pi suites green
  per project.

## 10. Risks / open items (all flagged for plan time)

- Death-spots data source (stored points vs completion-fraction mapping) — verify against pi
  schema before the Tracks plan.
- Chip export ROI consistency (small vs big characters) — user aside; resolve before bulk chip
  use; current chips fine as placeholders.
- Strategy presentation unresolved until its mockup round (kernel itself is locked).
- Streak/shimmer, fire, territory treatments: decided in A/B mockup rounds, incumbent wins ties.
- Icon sourcing from mariowiki (manual-ish, 30 files) — fallback name-slab tiles keeps the index
  unblocked.
- Turf-card sharpness audit (§4) may hit a rendering-engine floor on rotated layers — fix what's
  resampling blur, document any inherent remainder.
- Wordmark fit: revisit (alternatives round) only after the new language is live, if it clashes.

## 11. Deliverables / order

1. **Project 1:** auth flip (pi) + kit + shell/nav + new Live cards + activity log + turf chrome
   (+ fire/territory A/Bs) + dev-page sweep. Reference HTML: kit sampler, live card, turf chrome.
2. **Project 2:** Tracks — index tiles, hub, track map (model/trails/heatmap/death), optional
   recent-runs, ROI export script. Reference HTML: index tile/wall, hub, map.
3. **Project 3:** Players — index, profile (flavor + trends), strategy re-presentation,
   head-to-head, optional recent-runs. Reference HTML: index card, profile, strategy, versus.

Each project: mockup rounds → locked references → spec → plan → build (house SDD). After THIS spec
is approved, the implementation plan is written for Project 1 only.
