# UI Restyle — Professional Desktop Monitor (OBS-style)

**Date:** 2026-05-31
**Status:** Approved (design); pending implementation plan
**Scope:** Visual appearance of the Tauri frontend (`src/App.svelte`) only.

## Goal

Replace the current dark **navy/purple-black + cornflower-blue** theme — which reads as
"AI-generated dashboard" — with a restrained, neutral, **professional desktop-application**
look in the spirit of **OBS / OBS stats dock / DaVinci Resolve**.

The app's purpose is a **diagnostic monitor**: at a glance, confirm that visual ROI tracking
is healthy and the right events are firing. The visual design serves that and nothing else.

### Design principles
- **Neutral & flat.** Graphite greys, conventional widgets, hairline borders. No theming,
  no game styling, no novelty "instrument" metaphors, no decorative color.
- **Color is functional only.** Green / amber / red / grey communicate *tracking health*.
  A single muted blue **accent** marks the *active / selected* element and primary actions.
  Nothing else is colored.
- **Looks native, not templated.** Should feel built with a desktop toolkit, not a web theme.

## Scope guard

**Primarily a visual restyle, plus one explicitly-approved layout consolidation (below).**
The original layout is otherwise sound and stays put.

- **IN scope:** color values, status colors, the single accent, typography (font-family split),
  border-radius normalization, the color literals used for the ROI overlay (canvas) and the SVG
  screen-graph, **and the status-bar consolidation in the next section.**
- **OUT of scope:** panel order, the structure of the sidebar/editor/wizard, and any behavior
  change beyond relocating the status widgets named below. If a change requires touching
  layout/markup beyond the restyle + the approved consolidation, stop and confirm.

## Approved layout consolidation

Live status is currently shown in **three** places (top bar, feed overlay, and nowhere
unified). Consolidate into a single **bottom status bar**, OBS-style.

1. **Add a bottom status bar** as a footer of the `.app` shell (full width, below the main
   grid / above nothing). It is the single home for live engine status.
   Content uses **only data we actually have**: connection state (dot + label) · current
   `backendScreen` · `liveScore` (colored via `scoreColor`) · `backendFps` · resolution
   (`pythonFrameW×pythonFrameH`). *(No uptime / error-count — those were illustrative in the
   mockup and aren't tracked. Do not invent them.)*
   Disconnected / starting / stalled states reuse the existing `statusDot` + health logic.
2. **Remove health from the top bar.** Delete the `.tb-health` block (`~L1721–1736`); its
   dot/screen/score/fps now live in the status bar.
3. **Keep the live feed free of a screen/fps overlay.** (The current `.feed-overlay` div at
   `~L1918` is already empty — there is nothing to remove. The redundant overlay was only in
   the mockup; do not port it in. The feed's volume/visibility controls stay.)
   *Note:* the sidebar **Detection** panel also shows screen + score, but it's the detailed
   panel (it hosts the input/audio device selectors) — leave it as-is; not a duplication to fix.
4. **Remove the top-bar language badge.** Delete the `.lang-badge` button (`~L1714–1719`).
   Language is already editable in Settings (the wizard's Language step), so access is
   preserved. `openLangDialog` becomes unused → remove it if nothing else references it.

**Resulting top bar:** brand + version (left) · spacer · update strip + **⚙ Settings** ·
window controls. The ⚙ stays in the top-bar actions (its exact position is not load-bearing;
user is impartial).

**Verify during implementation:** removing `.lang-badge` must not strand the language dialog —
confirm Settings still reaches it; if `langDialog*` state is now orphaned, leave it (out of
scope) but note it.

## Current state (measured, for accuracy)

- All styling is in `src/App.svelte`; `<style>` begins at line ~2711 (file ~3279 lines).
- **0** `linear/radial-gradient`, **0** `box-shadow`, **0** `backdrop-filter` — already flat.
- **43** `border-radius` declarations (mostly 3–6px; status dots use 50%).
- **~60** unique hardcoded hex colors; dominated by `#7eb8f7` (28×, the cornflower accent)
  and a family of near-black navy/purples (`#080810`, `#04040a`, `#06060e`, `#111120`,
  `#1a1a2e`, …).
- **The entire app currently renders in monospace** (`body { font-family: Consolas… }`).

## Target token system — Variant A "Neutral Graphite"

Define once in a `:root` block at the top of the `<style>`. All CSS migrates to these.

### Surfaces & borders
```
--bg        #1b1c1e   app background
--panel     #232427   panels · section headers · title bar
--panel-2   #2a2b2f   nested surfaces · graph nodes
--raised    #303135   hover / active control background
--bd        #3a3b40   hairline borders
--bd-soft   #2e2f33   subtle internal dividers
--feed-bg   #0c0d0f   camera-preview area (near-black)
--track     #0e0f11   meter / progress-bar track
```

### Text
```
--tx        #d8d9dc   primary
--tx-mut    #9a9ca1   secondary / labels
--tx-dim    #6b6d73   tertiary / disabled / hints
```

### Accent (the only decorative color — active / selected / primary action)
```
--accent      #3d7cc2
--accent-soft #2d5e94   pressed / darker
--accent-bg   #26303c   accent-tinted fill (selected node / active row bg)
```

### Status (functional — tracking health)
```
--ok    #5aa86a   healthy · high confidence · connected
--warn  #c89a3e   marginal · low confidence
--err   #cf5b4e   fail · error
--idle  #56585e   inactive · no signal · disconnected
--close #c4382a   window-close hover only
```

### Typography (deliberate change: split mono → sans for chrome)
```
--ui    'Segoe UI', system-ui, -apple-system, sans-serif   /* chrome, labels, buttons */
--mono  'Cascadia Code', Consolas, ui-monospace, monospace /* numbers, scores, log, IDs, ROI tags */
```
Rule: **sans for UI chrome; mono only for data** (confidence scores, timers, lap/coin counts,
event log, screen-name IDs, ROI tags on the feed). Today everything is mono — this is the
single biggest "feels native" upgrade after the palette.

### Geometry
```
--r   3px    panels · cards · buttons · inputs
--r-sm 2px   chips · meter bars · small controls
/* status dots stay round (border-radius: 50%) — conventional LED affordance */
```
Clamp the existing 5–6px radii to `--r` (3px); leave 50% dots as-is.

## Migration map (old role → new token)

| Current | Role | → New |
|---|---|---|
| `#080810` | body bg | `--bg` |
| `#04040a` `#06060e` `#05050e` `#040410` | panels / bars / cards | `--panel` |
| `#0c0c18` `#080a14` `#0a0a16` `#0a0a18` | inputs / nested | `--panel-2` |
| `#0d0d1a` `#080818` `#0d0d1c` `#0d0d18` | hover | `--raised` |
| `#111120` `#14142a` `#1a1a2e` `#1a1a3a` `#1c2740` `#1e1e2e`/`3a`/`4a` `#141428` `#111122` | borders | `--bd` / `--bd-soft` |
| `#7eb8f7` (×28) | accent (cornflower) | `--accent` |
| `#5a8ab0` `#5a7a9a` `#9cf` | secondary accent / blue text | `--tx-mut` (or `--accent-soft` where it denotes accent) |
| `#7ef7b8` | graph "selected" green | `--accent` (differentiate selected vs live by fill, not hue) |
| `#4caf50` | ok green | `--ok` |
| `#f59e0b` | warn amber | `--warn` |
| `#ef4444` `#d9534f` | error red | `--err` |
| `#c42b1c` | close-hover | `--close` |
| `#e8e8f0` `#c8c8e0` `#cde` | primary text | `--tx` |
| greys `#888` `#666` `#555` `#444` `#333` `#222` | muted/dim text | `--tx-mut` / `--tx-dim` |

### Non-CSS color sites (must also migrate)
1. **Inline `style=` attributes** in markup — status dots, region pips, det/cand/sel bars
   (lines ~33, 189, 262, 1722, 1818, 1880, 2009, 2062, 2094, 2422, 2448, 2464).
2. **JS canvas constants** for the ROI editor overlay — `ROI_COLORS` (line 1176),
   `editRois()` color logic (line 189), handle color (1190), `scoreColor()` (1695–1697).
   These are drawn to `<canvas>` and **cannot read CSS vars directly** → introduce a single
   JS palette object mirroring the tokens (or read once via `getComputedStyle`).
3. **SVG screen-graph** node/edge fills & strokes (lines ~2197, 2210–2227).

### Functional color semantics to preserve
- `scoreColor(v)`: `≥0.8 → --ok`, `≥0.5 → --warn`, else `--err`.
- `statusDot`: disconnected `→ --idle`, alive `→ --ok`, stale/warming `→ --warn`.
- **ROI overlay (editor)** is a functional affordance needing distinguishable boxes over
  arbitrary video: active region `--accent`; sibling-in-group neutral light grey (`#8a8d93`);
  other-group `--warn`; drag handles `--accent`. (Final overlay shades may be fine-tuned during
  implementation/visual review — principle: accent = active, neutral/amber = context.)
- **ROI status tags on the live feed** (match indicators) use `--ok`/`--warn`/`--accent`.

## File structure (decided: extract theme only)

Keep `App.svelte` as one file (its structure is fine); extract **only the theme** into a
dedicated stylesheet so the palette is a real, centralized artifact.

```
src/
  main.js        → add: import "./theme.css";
  theme.css      NEW — :root tokens, global resets, body font, scrollbar styling
  App.svelte     structure unchanged; its <style> migrates every hardcoded hex to var(--token)
```

- `theme.css` owns the `:root` token block (all tokens above) + the truly global rules
  currently living at the top of App.svelte's `<style>` (body bg/font, `::selection`,
  `:global(::-webkit-scrollbar*)`). Component-scoped rules stay in `App.svelte`.
- `App.svelte`'s `<style>` keeps its (non-global) rules but references `var(--…)` throughout.
- **Note on Svelte scoping:** CSS custom properties cascade fine into Svelte's scoped styles,
  so `var(--accent)` works inside `App.svelte` even though the vars are defined in `theme.css`.
  (Only *selectors* are scoped, not inherited properties.)
- A full component split (TitleBar/StatusBar/Sidebar/…) is explicitly **deferred** — out of
  scope for this restyle to keep the diff reviewable and isolate restyle bugs from refactor bugs.

## Rollout

Vertical slice first, to validate the token system on real pixels before mass migration:
1. Create `src/theme.css` with the `:root` tokens + global rules; import it in `main.js`.
   Apply the `--ui`/`--mono` split; restyle **title bar + Detection panel** as the slice.
2. Run the app (`npm run tauri dev` or the built shell), screenshot, confirm the register.
3. Build the **bottom status bar** and perform the approved consolidation (remove `.tb-health`
   + `.lang-badge` from the top bar).
4. Propagate tokens to the remaining sidebar panels, feed controls, screen-graph footer,
   editor, modals/wizard.
5. Migrate the JS/canvas/SVG color literals to the JS palette mirror.
6. Final pass: clamp border-radii; grep for any surviving raw hex and convert.

## Acceptance
- No raw navy/purple hex or `#7eb8f7` remains; all colors come from tokens (CSS) or the single
  JS palette mirror (canvas/SVG).
- UI chrome is sans; only data is mono.
- Health states read instantly via green/amber/red/grey; exactly one accent hue in use.
- Live status appears **only** in the new bottom status bar — not in the top bar or on the feed.
- Top bar no longer shows the language badge; language remains reachable via Settings.
- Layout otherwise unchanged vs. before the restyle (only the approved consolidation differs).
```
