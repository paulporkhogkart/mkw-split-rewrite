# UI Restyle — Neutral Graphite (OBS-style) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the Tauri/Svelte frontend (`src/App.svelte`) from its navy/purple-black + cornflower-blue "AI dashboard" look into a restrained, neutral, professional **OBS-style monitor** (Variant A "Neutral Graphite"), and consolidate live status into a single bottom status bar.

**Architecture:** Extract a centralized design-token stylesheet (`src/theme.css`, imported once in `main.js`) holding the `:root` CSS custom properties + global rules. `App.svelte` keeps its structure but migrates every hardcoded color to `var(--token)`. Because a `<canvas>` (ROI editor) and inline SVG/JS cannot read CSS variables, a small JS palette object `C` in `App.svelte`'s `<script>` mirrors the same hex values for all JS-driven colors. One approved layout change: move the live-status widgets out of the title bar into a new bottom status bar.

**Tech Stack:** Svelte 4, Vite 5, Tauri 2. No frontend test runner exists.

---

## Verification approach (read before starting)

This is a **CSS/visual** change, so classic unit-test TDD does not apply — there is no test runner in this project (`package.json` has no `test` script). Each task is gated by **objective, automatable checks** plus a **visual checkpoint**, used test-first (verify the "before" state, make the change, verify the "after" state):

1. **Build gate** — `npm run build` (Vite) must succeed. This compiles `App.svelte` and catches Svelte/CSS syntax errors. This is the primary regression guard; run it after every task.
2. **Grep gate** — objective assertions that old colors are gone / new tokens are present. Run **before** a migration to see the "RED" count, and **after** to see it reach the target (usually 0). Use the `Grep` tool or `npm`-adjacent shell.
3. **Visual checkpoint** — see the real UI. Two ways:
   - **Quick (chrome/theme/startup only):** `npm run dev` → open the printed `localhost` URL. Without the Python backend the app stays on the **startup view** (view routing waits on the backend `ready` IPC message) and Tauri API calls are inert, but you can still confirm global theme, fonts, title bar, and the bottom status bar's disconnected state.
   - **Full (main view, editor, graph):** `npm run tauri dev` — builds the Rust shell, spawns the Python sidecar, opens the camera, and renders the real main view. This is the authoritative visual check. Screenshot it (or have the user eyeball it) at the marked checkpoints.

**Do not claim a task is done without showing the build output and grep counts** (evidence before assertions).

---

## Pre-flight

- [ ] **Confirm branch.** We start on `main`; all work goes on a feature branch. If an isolated worktree was already created by the using-git-worktrees skill, use it. Otherwise:

```bash
git checkout -b ui-restyle-neutral-graphite
git status   # expect: clean tree, on ui-restyle-neutral-graphite
```

- [ ] **Snapshot the "before" colors** (used as the RED baseline for later grep gates):

Run (Grep tool, `output_mode: count`, on `src/App.svelte`):
- `#7eb8f7` → expect **28**
- `#080810` → expect **1**
- `#4caf50` → expect **9**

Record these; later tasks assert they reach 0.

---

## File structure

```
src/
  main.js        MODIFY  — add `import "./theme.css";`
  theme.css      CREATE  — :root design tokens + global rules (body, scrollbars, selection)
  App.svelte     MODIFY  — <script>: add JS palette `C`; repoint scoreColor/statusDot
                          — markup: move tb-health → new bottom status bar; remove lang-badge
                          — <style>: migrate all hex → var(--token); font split; radius clamp
docs/
  ui-theme.md    MODIFY (Task 7) — sync if any token value drifted during implementation
```

No component split (deferred per spec). `theme.css` owns globals + tokens; `App.svelte`'s scoped `<style>` references the tokens.

---

## Task 1: Theme scaffold — tokens, globals, JS palette, font base

Creates the token system and flips the global base (graphite bg + sans font). After this task the app already looks dramatically different in the startup view.

**Files:**
- Create: `src/theme.css`
- Modify: `src/main.js:1-5`
- Modify: `src/App.svelte` `<script>` (add palette `C`; repoint `scoreColor` ~L1692, `statusDot` ~L33)
- Modify: `src/App.svelte` `<style>` globals (`:global(html, body)` L2713-2718; `.app` base L2727-2732); remove scrollbar hexes (L2719-2725)

- [ ] **Step 1: Create `src/theme.css`** with the full token set + globals (moved out of App.svelte):

```css
/* Design tokens + globals — Neutral Graphite (OBS-style).
   Single source of truth for CSS colors. JS/canvas/SVG colors are mirrored
   in the `C` palette object in App.svelte (a <canvas> cannot read CSS vars). */
:root {
  /* Surfaces & borders */
  --bg:        #1b1c1e;
  --panel:     #232427;
  --panel-2:   #2a2b2f;
  --raised:    #303135;
  --bd:        #3a3b40;
  --bd-soft:   #2e2f33;
  --feed-bg:   #0c0d0f;
  --track:     #0e0f11;

  /* Text */
  --tx:        #d8d9dc;
  --tx-mut:    #9a9ca1;
  --tx-dim:    #6b6d73;

  /* Accent — the only decorative color (active / selected / primary action) */
  --accent:      #3d7cc2;
  --accent-soft: #2d5e94;
  --accent-bg:   #26303c;

  /* Status — functional (tracking health) */
  --ok:    #5aa86a;
  --warn:  #c89a3e;
  --err:   #cf5b4e;
  --idle:  #56585e;
  --close: #c4382a;

  /* Typography */
  --ui:   'Segoe UI', system-ui, -apple-system, sans-serif;
  --mono: 'Cascadia Code', Consolas, ui-monospace, monospace;

  /* Geometry */
  --r:    3px;
  --r-sm: 2px;
}

/* Universal reset — preserved from App.svelte's old :global(*) rule.
   box-sizing must stay global or panel/feed widths shift. */
* { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%;
  background: var(--bg); color: var(--tx);
  font-family: var(--ui); font-size: 13px;
  overflow: hidden;
}
#app { height: 100vh; }

* { scrollbar-width: thin; scrollbar-color: var(--bd) transparent; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--bd); border-radius: var(--r); }
::-webkit-scrollbar-thumb:hover { background: var(--raised); }
::-webkit-scrollbar-corner { background: transparent; }
```

> **Faithful to the original:** keeps the `box-sizing` reset, `font-size: 13px`, and 6px
> scrollbars; only the colors/font-family change. The single behavioral change is `body`
> font-family mono → sans (`--ui`).

- [ ] **Step 2: Import the stylesheet in `src/main.js`.** Replace the whole file with:

```js
import "./theme.css";
import App from "./App.svelte";

const app = new App({ target: document.getElementById("app") });

export default app;
```

- [ ] **Step 3: Remove the now-duplicated globals from `App.svelte`'s `<style>`.** Delete lines **2712-2724** — the `:global(*)` box-sizing reset, the `:global(body)` rule, the scrollbar comment, and all five `:global(::-webkit-scrollbar*)` rules (these now live in `theme.css`). Replace them with a single pointer comment:

```css
  /* ── Global ──────────────────────────────────────────────────────────────── */
  /* Universal reset, html/body base, #app, and scrollbar styling live in src/theme.css */
```

- [ ] **Step 4: Confirm the `.app` rule needs no change.** The current `.app` rule (L2727) is
  `.app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }` — it sets
  **no** background or font, so it already inherits the graphite bg + sans font from `body` via
  `theme.css`. Leave it as-is. *(The navy `#04040a` lives on `.titlebar` at L2732 and is handled
  in Task 2.)* No edit in this step — it's a verification checkpoint.

- [ ] **Step 5: Add the JS palette `C`** in `App.svelte`'s `<script>`, immediately after the imports (after `import { t } from "./translations.js";`, ~L9). This mirrors the tokens for canvas/SVG/inline-JS use:

```js
  // JS color palette — mirrors the CSS tokens in theme.css for places that
  // cannot read CSS vars (canvas 2D context, inline SVG fills, JS-returned styles).
  // Keep in sync with theme.css :root.
  const C = {
    bg:'#1b1c1e', panel:'#232427', panel2:'#2a2b2f', raised:'#303135',
    bd:'#3a3b40', bdSoft:'#2e2f33',
    tx:'#d8d9dc', txMut:'#9a9ca1', txDim:'#6b6d73',
    accent:'#3d7cc2', accentSoft:'#2d5e94', accentBg:'#26303c',
    ok:'#5aa86a', warn:'#c89a3e', err:'#cf5b4e', idle:'#56585e',
    roiCtx:'#8a8d93',   // neutral sibling/context ROI box on the feed overlay
  };
```

- [ ] **Step 6: Repoint `scoreColor`** (currently L1692-1696) to the palette:

```js
  function scoreColor(v) {
    if (v >= 0.8) return C.ok;
    if (v >= 0.5) return C.warn;
    return C.err;
  }
```

- [ ] **Step 7: Repoint `statusDot`** (currently L33):

```js
  $: statusDot = !trackerConnected ? C.idle : backendAlive ? C.ok : C.warn;
```

- [ ] **Step 8: Build gate.**

Run: `npm run build`
Expected: build succeeds, no errors. (Svelte may warn about unused CSS selectors — acceptable for now.)

- [ ] **Step 9: Visual checkpoint (quick).**

Run: `npm run dev`, open the localhost URL.
Expected: background is graphite `#1b1c1e` (not navy-black); the startup card text renders in **Segoe UI (sans)**, not monospace; the status dot uses the new grey/green/amber. Stop the dev server.

- [ ] **Step 10: Commit.**

```bash
git add src/theme.css src/main.js src/App.svelte
git commit -m "feat(ui): add theme.css design tokens + JS palette; flip global base to graphite/sans"
```

---

## Task 2: Migrate `<style>` color literals → tokens

Mechanical replacement of every hardcoded hex inside `App.svelte`'s `<style>` block with the matching `var(--token)`. Homogeneous change; each step is one color group + a grep check.

**Files:**
- Modify: `src/App.svelte` `<style>` block (~L2726-3279)

**Mapping (old → new):**

| Old hex(es) | → token |
|---|---|
| `#080810` `#04040a` `#06060e` `#05050e` `#040410` | `var(--panel)` *(see note)* |
| `#0c0c18` `#080a14` `#0a0a16` `#0a0a18` `#141428` | `var(--panel-2)` |
| `#0d0d1a` `#080818` `#0d0d1c` `#0d0d18` `#0d1424` | `var(--raised)` |
| `#111120` `#111122` `#14142a` `#1a1a2e` `#1a1a3a` `#1c2740` `#1e1e2e` `#1e1e3a` `#1e1e4a` `#2a2a3e` `#2a2a40` `#2a3a5a` `#1a3a7a` `#162a5a` `#3a3a52` `#1c1c2e` | `var(--bd)` |
| `#7eb8f7` `#9cf` | `var(--accent)` |
| `#0d1f40` `#0d2040` `#0d2a1f` `#26303c` `#0a0a18`(btn bg) | `var(--accent-bg)` |
| `#5a8ab0` `#5a7a9a` `#4a7ab0` `#33506a` | `var(--accent-soft)` |
| `#7ef7b8` `#a8d8a8` `#4a6a4a` | `var(--accent)` *(graph "selected" collapses to accent)* |
| `#4caf50` | `var(--ok)` |
| `#f59e0b` `#8a6a10` `#6a6040` | `var(--warn)` |
| `#ef4444` `#d9534f` `#c99` `#b66` `#7a3a3a` `#3a1e1e` `#4a2a2a` `#1a0d0d` | `var(--err)` |
| `#c42b1c` | `var(--close)` |
| `#e8e8f0` `#c8c8e0` `#cde` | `var(--tx)` |
| `#aab` `#9ab` `#8aa` `#888` `#678` `#567` `#566` | `var(--tx-mut)` |
| `#666` `#555` `#444` `#333` `#222` `#243` `#2a4a3a` | `var(--tx-dim)` |

> **Note on near-blacks:** `#080810` (body, already handled in Task 1) and the `#04040a`/`#06060e` family are used both for the app/panel bg *and* for slightly-darker insets. Map the panel/header/card backgrounds to `var(--panel)`; where the original is a darker inset behind content (log body, feed area, meter tracks), use `var(--track)` or `var(--feed-bg)` as fits — judge per occurrence. When unsure, `var(--panel)` is the safe default. The visual checkpoint will catch anything too flat.

- [ ] **Step 1: RED baseline.** Grep `src/App.svelte` (count) for `#7eb8f7` → expect 28; `#4caf50` → expect 9. These must reach 0 by Step 8.

- [ ] **Step 2: Migrate the accent.** Replace all `#7eb8f7` → `var(--accent)` (Edit with `replace_all: true`). Then `#9cf` → `var(--accent)`.

- [ ] **Step 3: Migrate accent-bg / accent-soft.** Replace (each `replace_all`): `#0d1f40`, `#0d2040`, `#0d2a1f` → `var(--accent-bg)`; `#5a8ab0`, `#5a7a9a` → `var(--accent-soft)`; `#7ef7b8` → `var(--accent)`.

- [ ] **Step 4: Migrate status colors.** `#4caf50` → `var(--ok)`; `#f59e0b` → `var(--warn)`; `#ef4444` and `#d9534f` → `var(--err)`; `#c42b1c` → `var(--close)`.

- [ ] **Step 5: Migrate surfaces & borders.** Working from the mapping table, replace the panel family (`#04040a` `#06060e` `#05050e` `#040410`) → `var(--panel)`; the panel-2 family → `var(--panel-2)`; the raised family → `var(--raised)`; the border family → `var(--bd)`. Use `var(--track)` for meter/progress track backgrounds (`.det-bar-wrap`, `.cand-bar-wrap`, `.sel-bar-wrap`, `.upd-track`) and `var(--feed-bg)` for `.feed-area`/feed `background:#000` if present.

- [ ] **Step 6: Migrate text greys.** Replace the primary-text set (`#e8e8f0` `#c8c8e0` `#cde`) → `var(--tx)`; the muted set (`#aab` `#9ab` `#8aa` `#888` `#678` `#567` `#566`) → `var(--tx-mut)`; the dim set (`#666` `#555` `#444` `#333` `#222`) → `var(--tx-dim)`. Replace remaining reddish error-tint borders/text (`#c99` `#b66` `#7a3a3a` `#3a1e1e` `#4a2a2a` `#1a0d0d`) → `var(--err)`, and remaining warn-tints (`#8a6a10` `#6a6040`) → `var(--warn)`.

- [ ] **Step 7: Handle `rgba(...)` accent tints.** Search the `<style>` for `rgba(126,184,247` (old accent at low alpha, e.g. `.cand-active` L3010) and `rgba(239,68,68` (old err, `.btn-reset-confirm` L3133). Replace with token-based equivalents: `rgba(61,124,194,.06)` for the accent tint, and keep red tints as `rgba(207,91,78,.12)` / `.35`. (These channel values match `--accent`/`--err`.)

- [ ] **Step 8: GREEN gate.** Grep `src/App.svelte` (count): `#7eb8f7` → **0**, `#4caf50` → **0**, `#f59e0b` → **0**, `#ef4444` → **0**, `#080810` → **0**. Any non-zero means a missed occurrence — find and convert it.

- [ ] **Step 9: Build gate.** Run: `npm run build` → succeeds.

- [ ] **Step 10: Visual checkpoint (full).** Run: `npm run tauri dev`. Confirm the main view reads as neutral graphite: panels, borders, the Detection meter (green/amber/red), the accent on the active graph node. Screenshot for the record. Stop the app.

- [ ] **Step 11: Commit.**

```bash
git add src/App.svelte
git commit -m "feat(ui): migrate all <style> color literals to design tokens"
```

---

## Task 3: Typography — mono only for data

Task 1 made the base font sans (via `body`/`.app`). Everything that used `font-family: inherit` is now sans automatically (correct for chrome). This task forces **data** elements back to `var(--mono)` so numbers/IDs/logs stay tabular, and converts the explicit `Consolas, monospace` rules to the token.

**Files:**
- Modify: `src/App.svelte` `<style>` (data-element selectors) and the SVG `font-family` attrs (~L2216, 2221, 2227)

- [ ] **Step 1: Convert explicit mono rules to the token.** Replace all `font-family: Consolas, monospace;` and `font-family: Consolas, 'Courier New', monospace;` occurrences in the `<style>` → `font-family: var(--mono);` (Edit `replace_all`). This covers `.edit-screen-id`, `.det-zoom-reset`, `.treg-score`, the log body, etc.

- [ ] **Step 2: Force data elements that currently rely on inherited mono to explicit mono.** For each selector below, ensure it declares `font-family: var(--mono);` (add the declaration if absent; if it currently says `font-family: inherit;`, change it):
  - `.hb-score` (status score) — L~2755
  - `.det-val` (detection score readout)
  - `.cand-score` (candidate score)
  - `.treg-score` (region score)
  - `.sel-conf` / any selection confidence number
  - `.log-body` / `.log-line` (event log)
  - `.upd-label` (version string)
  - `.feed-res` (resolution, if styled)

- [ ] **Step 3: Convert SVG graph `font-family`.** In the markup (~L2216, 2221, 2227), replace `font-family="Consolas, monospace"` → `font-family="var(--mono)"` on the three `<text>` elements (node label, prev-link, candidate score). *(SVG presentation attribute `font-family` accepts a var() reference in WebView2/Chromium.)*

- [ ] **Step 4: Build gate.** Run: `npm run build` → succeeds.

- [ ] **Step 5: Visual checkpoint (full).** Run `npm run tauri dev`. Confirm: section titles, labels, buttons render in **Segoe UI**; scores/log/lap-coin numbers and screen IDs render in **mono**. The graph node labels stay mono. Screenshot. Stop.

- [ ] **Step 6: Commit.**

```bash
git add src/App.svelte
git commit -m "feat(ui): split typography — sans for chrome, mono only for data"
```

---

## Task 4: Migrate inline-style + canvas + SVG color literals

The remaining colors live outside the `<style>` block: inline `style=` attributes in markup, the canvas ROI-overlay drawing constants, and the SVG graph node/edge fills. All move to the `C` palette (or `var()` for DOM inline styles).

**Files:**
- Modify: `src/App.svelte` markup inline styles (~L189, 262, 1818, 1880) and `<script>` canvas constants (`ROI_COLORS` L1176, `editRois` colors L189, handle color L1190) and SVG graph (~L2197, 2210-2228)

- [ ] **Step 1: `editRois()` colors** (L~189). Replace the color expression:

```js
                 color: active ? C.accent : (sameGrp ? C.roiCtx : C.warn) });
```

- [ ] **Step 2: `editTabRois()` color** (L~262). Replace:

```js
    return keys.map(k => ({ k, roi: rois[k], active: k===activeRoiName, color: k===activeRoiName ? C.accent : C.warn }));
```

- [ ] **Step 3: `ROI_COLORS` constant** (L~1176). Replace:

```js
  const ROI_COLORS={primary:C.tx, and:C.warn, or:C.accent};
```

- [ ] **Step 4: Active drag-handle color** (L~1190). Replace the handle fill:

```js
        ctx.fillStyle=active?C.accent:color;
```

- [ ] **Step 5: Region pip inline styles in markup.** At L~1818 replace the `treg-dot` background expression:

```svelte
                            <span class="treg-dot" style="background:{activeRegion.group===gi && activeRegion.region===ri ? C.accent : (gi===activeRegion.group ? C.roiCtx : C.warn)}"></span>
```
and at L~1880:
```svelte
                              <span class="treg-dot" style="background:{activeRoiName===k ? C.accent : C.warn}"></span>
```

- [ ] **Step 6: SVG edges** (L~2197). Replace the edge stroke:

```svelte
                      <line x1={a.x+NW/2} y1={a.y+NH/2} x2={b.x+NW/2} y2={b.y+NH/2}
                        stroke={C.bd} stroke-width="1" opacity={dimHome ? 0.12 : 1} />
```

- [ ] **Step 7: SVG node rect** (L~2209-2213). Distinguish *selected-for-edit* (white ring) from *engine-active* (accent ring); both use the accent-tinted fill:

```svelte
                      <rect width={NW} height={NH} rx="3" ry="3"
                        fill={isSel || isActive ? C.accentBg : C.panel2}
                        stroke={isSel ? C.tx : (isActive ? C.accent : (candScore ? C.bd : C.bdSoft))}
                        stroke-width={isSel || isActive ? 1.5 : 1}
                        opacity={dimmed ? 0.45 : 1} />
```

- [ ] **Step 8: SVG node label + sublabels** (L~2214-2229). Replace the three `fill=` expressions:

```svelte
                        fill={isSel ? C.tx : (isActive ? C.accent : (candScore ? C.txMut : (dimmed ? C.txDim : C.txMut)))}
```
prev-link text (L~2222):
```svelte
                          fill={isActive ? C.accentSoft : C.txDim} opacity="0.85"
```
candidate-score text keeps `fill={scoreColor(candScore)}` (already token-backed).

- [ ] **Step 9: Startup/connecting inline dots.** Any remaining `style="background:..."` using old hex in the startup/conn views (~L2417 uses `{statusDot}` already — fine). Grep for `style="background:#` and `style="color:#` in markup and convert stragglers to `C.*` or `var(--token)`.

- [ ] **Step 10: GREEN gate.** Grep `src/App.svelte` for raw hex in JS/markup contexts: search `: "#` and `={"#` and `background:#` — expect **0** functional matches (only the `const C = {…}` palette lines should contain hex). Build gate: `npm run build` → succeeds.

- [ ] **Step 11: Visual checkpoint (full).** Run `npm run tauri dev`, open a screen node in the editor (click a graph node) → confirm ROI boxes: active = accent blue, sibling = neutral grey, other-group = amber; drag handles = accent. Confirm the graph: active node = accent ring, selected node = white ring. Screenshot. Stop.

- [ ] **Step 12: Commit.**

```bash
git add src/App.svelte
git commit -m "feat(ui): migrate canvas/SVG/inline color literals to JS palette"
```

---

## Task 5: Bottom status bar + consolidation

Move the live-status widgets out of the title bar into a new OBS-style bottom status bar (a footer of `.app`, visible in all views). Remove the redundant title-bar language badge.

**Files:**
- Modify: `src/App.svelte` markup — title bar (remove `.lang-badge` L1714-1719 and `.tb-health` L1721-1736); insert `.statusbar` before `</div><!-- /app -->` (**L2491**, after the view-router `{/if}`; the modals at L2499+ are outside `.app` and unaffected)
- Modify: `src/App.svelte` `<style>` — add `.statusbar` rules; remove dead `.lang-badge`/`.lang-*` rules
- Modify: `src/App.svelte` `<script>` — remove `openLangDialog` if now unreferenced

- [ ] **Step 1: Remove the language badge** from the title bar. Delete L1714-1719 (the `<!-- Language badge -->` comment through its closing `</button>`).

- [ ] **Step 2: Remove the `.tb-health` block** from the title bar. Delete L1721-1736 (the `<div class="tb-health" …>` through its `</div>`). The title bar now contains: brand, spacer (`tb-actions`/update strip + Settings), window controls.

- [ ] **Step 3: Insert the bottom status bar.** Immediately before `</div><!-- /app -->` (currently **L2491**, i.e. just after the view-router's closing `{/if}` and before the `.app` div closes), add:

```svelte
  <!-- ── Bottom status bar (single home for live engine status) ─────────────── -->
  <footer class="statusbar">
    <span class="hb-dot" style="background:{statusDot}"></span>
    {#if trackerConnected && backendAlive}
      <span class="sb-screen">{backendScreen}</span>
      <span class="sb-sep">·</span>
      <span class="sb-score" style="color:{scoreColor(liveScore)}">{liveScore.toFixed(3)}</span>
      <span class="sb-sep">·</span>
      <span class="sb-fps">{backendFps} fps</span>
      <span class="sb-spacer"></span>
      <span class="sb-res">{pythonFrameW}×{pythonFrameH}</span>
    {:else if trackerConnected}
      <span class="sb-warn">backend stalled</span>
      <span class="sb-spacer"></span>
    {:else if trackerSpawned}
      <span class="sb-idle">engine starting…</span>
      <span class="sb-spacer"></span>
    {:else}
      <span class="sb-idle">launching…</span>
      <span class="sb-spacer"></span>
    {/if}
  </footer>
```

- [ ] **Step 4: Add `.statusbar` styles** to the `<style>` block (place near the title-bar rules). Reuses the existing round `.hb-dot`:

```css
  /* Bottom status bar */
  .statusbar {
    flex: none; display: flex; align-items: center; gap: 8px;
    height: 24px; padding: 0 12px;
    background: var(--panel); border-top: 1px solid var(--bd);
    font-family: var(--mono); font-size: .68rem; color: var(--tx-mut);
  }
  .statusbar .sb-screen { color: var(--tx); }
  .statusbar .sb-sep    { color: var(--tx-dim); }
  .statusbar .sb-fps,
  .statusbar .sb-res    { color: var(--tx-mut); }
  .statusbar .sb-warn   { color: var(--warn); }
  .statusbar .sb-idle   { color: var(--tx-dim); font-style: italic; }
  .statusbar .sb-spacer { flex: 1; }
```

- [ ] **Step 5: Remove dead title-bar status CSS.** Delete the now-unused `.lang-badge`, `.lang-app`, `.lang-sep`, `.lang-sw2` rules (~L2742-2750) and the `.tb-health` rule plus any `.hb-screen/.hb-fps/.hb-sep/.hb-warn/.hb-idle` rules that are no longer referenced (~L2752-2759). **Keep `.hb-dot`** — it is still used by the status bar and the startup card. **Keep `.hb-score`** only if still referenced; the status bar uses `.sb-score`, so remove `.hb-score` if grep shows no other use.

- [ ] **Step 6: Remove orphaned `openLangDialog`.** Grep for `openLangDialog`. If the only definition is its declaration (the badge that called it is gone), delete the function. If `langDialogEl`/`langDlg*` state is now unused, leave it (out of scope) — note it in the commit body. *(Do not remove the wizard's own Language step — language stays editable there.)*

- [ ] **Step 7: Build gate.** Run: `npm run build` → succeeds (and no "openLangDialog is not defined" / unused-export errors).

- [ ] **Step 8: GREEN gate.** Grep `src/App.svelte`: `class="tb-health"` → 0; `class="lang-badge"` → 0; `class="statusbar"` → 1.

- [ ] **Step 9: Visual checkpoint (full).** Run `npm run tauri dev`. Confirm: title bar shows only brand + Settings + window controls (no screen/fps/score, no EN-UK badge); the **bottom status bar** shows dot · screen · score · fps · resolution; Settings ⚙ still opens and still reaches the Language selector. Screenshot. Stop.

- [ ] **Step 10: Commit.**

```bash
git add src/App.svelte
git commit -m "feat(ui): consolidate live status into bottom status bar; drop title-bar health + lang badge"
```

---

## Task 6: Geometry pass + final hex sweep

Normalize corner radii to the two-step scale and confirm no stray raw colors remain.

**Files:**
- Modify: `src/App.svelte` `<style>` (border-radius values)

- [ ] **Step 1: Clamp radii.** In the `<style>` block replace radius values (Edit `replace_all` per value): `border-radius: 6px` → `border-radius: var(--r)`; `border-radius: 5px` → `border-radius: var(--r)`; `border-radius: 4px` → `border-radius: var(--r)`; `border-radius: 3px` → `border-radius: var(--r)`; `border-radius: 2px` → `border-radius: var(--r-sm)`. **Do not touch `border-radius: 50%`** (round dots). The SVG `rx="3" ry="3"` on nodes may stay as-is (numeric SVG attr).

- [ ] **Step 2: Build gate.** Run: `npm run build` → succeeds.

- [ ] **Step 3: Final hex sweep.** Grep `src/App.svelte` with pattern `#[0-9a-fA-F]{6}` (content mode). Expected: the **only** matches are the lines inside the `const C = {…}` palette block (Task 1, Step 5). If any other line matches, convert it (token in `<style>`, `C.*` in JS/SVG) and re-run.

- [ ] **Step 4: Three-digit hex sweep.** Grep pattern `#[0-9a-fA-F]{3}\b`. Expected: 0 matches (all shorthand greys were migrated in Task 2/3). Convert any stragglers.

- [ ] **Step 5: Commit.**

```bash
git add src/App.svelte
git commit -m "refactor(ui): clamp border-radii to token scale; final hex sweep"
```

---

## Task 7: Final verification, docs sync, wrap-up

**Files:**
- Modify (if needed): `docs/ui-theme.md`

- [ ] **Step 1: Full build.** Run: `npm run build`. Expected: clean success. Capture the output.

- [ ] **Step 2: Unused-CSS check.** Review Svelte's build warnings for "Unused CSS selector". Remove any selectors that are now genuinely dead (e.g. leftover `.hb-*`/`.lang-*` you didn't catch in Task 5). Re-run `npm run build`.

- [ ] **Step 3: Authoritative visual review.** Run `npm run tauri dev` and walk the surfaces: main view (feed + sidebar panels + graph footer + new status bar), a screen-editor (graph node → ROI overlay + tabs), the Settings modal, and (if reproducible) the first-run setup view. Capture screenshots. Confirm against the spec's acceptance list:
  - no navy/purple or cornflower anywhere; one accent hue (blue) only;
  - health reads via green/amber/red/grey;
  - chrome = sans, data = mono;
  - live status only in the bottom bar (not title bar / feed);
  - language reachable via Settings; layout otherwise unchanged.

- [ ] **Step 4: Sync docs.** If any token value changed during implementation, update the table in `docs/ui-theme.md` so it matches `theme.css`. If nothing drifted, confirm they agree (no edit needed).

- [ ] **Step 5: Commit any docs/cleanup.**

```bash
git add -A
git commit -m "chore(ui): finalize neutral-graphite restyle; sync theme docs"
```

- [ ] **Step 6: Finish the branch.** Use the superpowers:finishing-a-development-branch skill to choose merge/PR/cleanup. (Do not merge to `main` without the user's go-ahead.)

---

## Self-review (completed during planning)

**1. Spec coverage** — every spec section maps to a task:
- Token system / palette → Task 1 (theme.css) + Task 2 (CSS migration).
- Typography split → Task 1 (base) + Task 3 (data-mono).
- Non-CSS color sites (inline/canvas/SVG) → Task 4.
- Approved consolidation (status bar, remove tb-health + lang-badge) → Task 5.
- Geometry / border-radius + final hex sweep → Task 6.
- File structure "extract theme only" → Task 1.
- Acceptance criteria → verified in Task 7, Step 3.

**2. Placeholder scan** — no "TBD"/"handle appropriately"; every code step shows code; grep targets have concrete expected counts. The one judgment call (near-black bg vs inset) is explicitly bounded with a safe default + visual catch (Task 2 note).

**3. Type/name consistency** — `C` palette keys (`accent`, `accentBg`, `accentSoft`, `roiCtx`, `panel2`, `bdSoft`, `ok/warn/err/idle`, `tx/txMut/txDim`) are defined once in Task 1 Step 5 and used identically in Tasks 4. CSS tokens (`--accent`, `--accent-bg`, `--panel-2`, `--bd-soft`, …) match `docs/ui-theme.md` and the spec exactly. Status-bar classes (`.sb-*`) are defined in Task 5 Step 4 and used in Step 3; `.hb-dot` is explicitly preserved.

**4. Known caveat** — `var()` inside SVG presentation attributes (`fill="var(--mono)"` in Task 3 Step 3; `font-family`) relies on Chromium/WebView2 support. The graph node *fills* in Task 4 use resolved `C.*` hex (safe). If a future non-Chromium webview is targeted, the SVG `font-family` var() may need inlining — noted for the implementer.
