# Frontend Redesign — Native Monitor + Edit Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Tauri frontend as a native, OBS/Resolve-style live **Monitor** + on-demand **Edit mode**, decomposing the 3,268-line `src/App.svelte` into focused components/modules, adding an annotated feed (active ROIs + minimap recreation), a dual-feed Settings, and the backend/IPC needed to stream current-run minimap state — **without losing any existing functionality**.

**Architecture:** Extract shared logic out of `App.svelte` into `src/lib/` modules (IPC, state stores, camera, graph, palette, overlay drawing) and a tree of `src/components/`. `App.svelte` becomes a thin shell that mounts the current view (Monitor / Edit / Settings) and owns the IPC wiring. Backend gains four small additions in `mkw_tracker/ipc` + `mkw_tracker/main.py` (minimap stream, replay-path fetch, icon-sample fetch, selection candidate lists). Work is phased so the app builds and runs after every phase.

**Tech Stack:** Svelte 4 + Vite (frontend), Tauri 2 (Rust shell, `@tauri-apps/api`), Python sidecar (`mkw_tracker`, OpenCV/numpy), SQLite. Frontend has no unit-test harness today; backend uses pytest.

**Spec:** `docs/superpowers/specs/2026-06-01-frontend-redesign-monitor-design.md`
**Token reference:** `docs/ui-theme.md` (update as tokens change).
**Local visual references (gitignored, present on this machine):** `.superpowers/brainstorm/2866-1780248420/content/` — notably `full-v2.html` (monitor + edit), `mainview-grouping.html` (G1), `rail-refine.html` (rows/expansion), `rail-v2-editmode-v2.html` (edit mode), `grouping-settings-templates.html` + `rails-splits-settings.html` (settings + live-vs-template).

---

## Testing approach (read first)

This is a **UI refactor**, not greenfield feature code, and Svelte components here have no test harness. So the verification model is adapted:

- **Backend / IPC additions (Phases 4 only):** true TDD with `pytest` — failing test, implement, pass.
- **Frontend (all other phases):** the "test" per task is **`npm run build` succeeds with zero new `svelte-check` warnings** + a **manual smoke check** the executor performs by running the app (`npm run tauri dev`) and confirming the named behavior. Each task lists the exact build command and the manual check.
- **Regression guard ("don't lose functionality"):** Phase 1 Task 1.1 captures a written **Functionality Inventory** from the current `App.svelte`; the final task (7.x) walks that inventory item-by-item against the rebuilt app. Every IPC command in `docs/ipc-protocol.md` must still be reachable.
- **Commit after every task.** Conventional-commit messages provided.

If `svelte-check` isn't wired, add it once in Phase 1 Task 1.0.

---

## File structure (target)

```
src/
  main.js                       imports theme.css, mounts App
  theme.css                     refined tokens (v2) + globals + tabular-nums
  translations.js               (unchanged; may gain Settings strings)
  App.svelte                    THIN shell: IPC wiring + <MonitorView/> | <EditMode/> + <SettingsModal/>
  lib/
    ipc.js                      send(); subscribe to "tracker-event"; window controls; updater
    stores.js                   writable stores: connection, screen, score, candidates, selection, race, minimap, devices, setup, view…
    camera.js                   enumerate devices, getUserMedia, dual-feed source-check state, device-change/restart
    graph.js                    screen-graph layout + pan/zoom (extracted gZoom/gPan/onGraph*)
    palette.js                  JS mirror of theme tokens (the `C` object), for canvas/SVG
    overlay.js                  draw ROI boxes + minimap (sample/tracking/replays) onto a 2D canvas, given 1080p geometry + display rect
    format.js                   score→color, time/score formatting, human-readable screen names
  components/
    TitleBar.svelte             brand+version · Edit-screens toggle · ⚙ · window controls
    StatusBar.svelte            connection · screen · score · fps · resolution (sans, tabular)
    MonitorView.svelte          layout: <FeedOverlay/> + <Rail/> + <StatusBar/>
    FeedOverlay.svelte          <video bind:mainVideoEl> + <canvas> overlay (uses lib/overlay)
    Rail.svelte                 G1 sections wrapper
    RailSection.svelte          tinted header band + slot (edge-to-edge)
    ReadoutRow.svelte           value · color-coded score · click-to-expand
    CandidateList.svelte        expansion well: candidate rows w/ bars
    RaceSection.svelte          dynamic lap splits + Total + Coins + Mushrooms
    EventLog.svelte             collapsible debug log
    EditMode.svelte             layout: <ScreenGraph/> top strip + <RoiCanvas/> + <ToolsPanel/> + <StatusBar/>
    ScreenGraph.svelte          interactive pan/zoom node graph (navigator)
    RoiCanvas.svelte            zoomable feed + editable ROI boxes
    ToolsPanel.svelte           Detection / Readout tabs (Readout disabled when N/A)
    DetectionTree.svelte        boolean-tree editor (groups AND / regions OR)
    RegionInspector.svelte      live-crop vs template + match score + Capture/Test
    ReadoutRoiEditor.svelte     selection/HUD ROI editing (per-item template capture)
    SettingsModal.svelte        modal shell; first-run = stepped
    SourceCheck.svelte          dual feed (app <video> + engineFrame) + per-pane status
    DeviceSelectors.svelte      Video + Audio selects
    LanguageSelectors.svelte    Application + Switch-system selects
mkw_tracker/
  ipc/sidecar.py | broadcaster.py   + handlers: get_replay_paths, get_minimap_sample
  ipc/protocol.py (or where event types live)  + minimap_update event
  main.py                            emit minimap_update during RACING; include candidate lists in state
  minimap/tracker.py                 expose sample crop (data URL helper)
  detection/selection.py             expose ranked candidates per field
```

Component markup/styles follow the mockups; tokens come from `theme.css`/`palette.js`. Files that change together live together; each component owns one responsibility.

---

## PHASE 1 — Foundations (app unchanged, just restructured)

### Task 1.0: Wire `svelte-check` for verification

**Files:** Modify `package.json`

- [ ] **Step 1:** Confirm scripts. Run: `npm run` — note existing scripts (expect `build`, `tauri`). 
- [ ] **Step 2:** If absent, add devDep + script:
```bash
npm i -D svelte-check
npm pkg set scripts.check="svelte-check --tsconfig ./jsconfig.json || svelte-check"
```
(If no `jsconfig.json`, `svelte-check` runs standalone — the fallback covers it.)
- [ ] **Step 3:** Baseline. Run: `npm run check` — record the current warning count (the pre-existing `.cam-release-*` unused-CSS warnings are expected). Expected: completes, prints N warnings.
- [ ] **Step 4:** Commit. `git add package.json package-lock.json && git commit -m "build(ui): add svelte-check script for refactor verification"`

### Task 1.1: Capture the Functionality Inventory (regression guard)

**Files:** Create `docs/superpowers/plans/functionality-inventory.md`

- [ ] **Step 1:** Read `src/App.svelte` end-to-end and `docs/ipc-protocol.md`. Enumerate, as a checklist, every user-facing behavior + every IPC command sent/handled. Group: window controls; updater (pending/download/ready/install); connection/heartbeat; live readout (screen, score, candidates, selection, lap/coin/mushroom/splits/finish); event log; screen-graph pan/zoom/click; node editor (detection tree: add/remove region+group, kind change, capture, test, reset; selection/HUD ROI editing: drag/resize handles, per-item template capture, reset ROI); settings/wizard (language ×2, camera device, audio device, dual-feed verify, restart-needed); first-run setup gating; feed controls (mute/volume/hide).
- [ ] **Step 2:** For each, note the symbol/function in `App.svelte` and the IPC message(s). This is the contract the rebuild must preserve.
- [ ] **Step 3:** Commit. `git add docs/superpowers/plans/functionality-inventory.md && git commit -m "docs(ui): functionality inventory as refactor regression guard"`

### Task 1.2: Refined theme tokens

**Files:** Modify `src/theme.css`; Modify `docs/ui-theme.md`

- [ ] **Step 1:** Update `:root` to the v2 token set (see spec "Visual system / tokens"): `--bg #1b1c1e`, `--panel #202023`, `--raised #26272b`, `--feed #0b0c0e`, `--track #303135`, `--bd #34353a`, `--bd2 #27282b`, `--tx #d9dadd`, `--tx2 #9a9ca1`, `--tx3 #6b6d73`, `--acc #3d7cc2`, `--ok #5aa86a`, `--warn #c89a3e`, `--err #cf5b4e`, radius `--r 4px`. Keep existing token *names* used elsewhere as aliases if present (don't break current references yet — add new, alias old). Add a global rule: `body, .app { font-variant-numeric: tabular-nums; font-feature-settings:"tnum"; }` and ensure body font is the UI sans (no global monospace).
- [ ] **Step 2:** Build. Run: `npm run build` — Expected: success.
- [ ] **Step 3:** Manual: `npm run tauri dev`, confirm app still renders (colors shift slightly; no layout change). 
- [ ] **Step 4:** Update `docs/ui-theme.md` token table to match.
- [ ] **Step 5:** Commit. `git add src/theme.css docs/ui-theme.md && git commit -m "feat(ui): refined v2 graphite tokens + tabular figures; alias legacy tokens"`

### Task 1.3: Extract `lib/palette.js` (the `C` object)

**Files:** Create `src/lib/palette.js`; Modify `src/App.svelte`

- [ ] **Step 1:** Move the `const C = {…}` block (App.svelte:14) into `src/lib/palette.js` as `export const C = {…}`, updating values to the v2 tokens (ok/warn/err/acc/bg/panel/raised/bd/tx/tx2/tx3, plus minimap state colors: `mmRingFace`, `mmRingOnly`, `mmReacquire` per `overlay/minimap.py`). 
- [ ] **Step 2:** In `App.svelte`, replace the inline `C` with `import { C } from "./lib/palette.js";`.
- [ ] **Step 3:** Build + check. Run: `npm run build && npm run check` — Expected: success, warning count unchanged.
- [ ] **Step 4:** Commit. `git add src/lib/palette.js src/App.svelte && git commit -m "refactor(ui): extract JS color palette to lib/palette.js"`

### Task 1.4: Extract `lib/ipc.js` (transport) + `lib/stores.js` (state)

**Files:** Create `src/lib/ipc.js`, `src/lib/stores.js`; Modify `src/App.svelte`, `src/main.js`

- [ ] **Step 1:** Create `src/lib/stores.js` with `writable` stores mirroring current App state (one export each): `connection` (`{connected,spawned,fps,lastHeartbeat}`), `screen`, `liveScore`, `candidateScores`, `selection` (`{char,charConf,costume,…,course,courseConf}`), `race` (`{curLap,totLap,coins,mushrooms,splits,finishTime}`), `minimap` (`{cx,cy,radius,trackState}` — new, default null), `devices`, `setup` (`{complete,wizardOpen,step}`), `update` (updater state), `view` (`"monitor"|"edit"|"settings"`), `language` (`{app,switch2}`), `tells`, `rois`. 
- [ ] **Step 2:** Create `src/lib/ipc.js` exporting `send(msg)` (writes the outbound command the same way App.svelte does today), `initIpc()` (subscribes to the Tauri `"tracker-event"` listener and routes each event type to the matching store update — port the current event switch), and window/updater helpers (`winMinimize/winToggleMaximize/winClose`, updater calls). Keep the **exact** event handling logic from `App.svelte` (move, don't rewrite).
- [ ] **Step 3:** In `App.svelte`, replace the moved state/handlers with store subscriptions (`$store`) and `initIpc()` in `onMount`. App behavior unchanged.
- [ ] **Step 4:** Build + check + manual. Run: `npm run build && npm run check`; then `npm run tauri dev` and confirm: connects, live values update, window controls work. Expected: identical behavior.
- [ ] **Step 5:** Commit. `git add src/lib src/App.svelte src/main.js && git commit -m "refactor(ui): extract IPC transport + Svelte state stores"`

### Task 1.5: Extract `lib/format.js`

**Files:** Create `src/lib/format.js`; Modify `src/App.svelte`

- [ ] **Step 1:** Add `scoreColor(v)` (≥0.8→C.ok, ≥0.5→C.warn, else C.err), `screenLabel(name)` (UPPER_SNAKE → "Title Case", e.g. `RACING`→"Racing"), `fmtScore(v)` (`v.toFixed(2)`), `fmtSplit(s)`. 
- [ ] **Step 2:** Replace the equivalent inline logic in `App.svelte`.
- [ ] **Step 3:** Build + check. Run: `npm run build && npm run check`. Expected: success.
- [ ] **Step 4:** Commit. `git add src/lib/format.js src/App.svelte && git commit -m "refactor(ui): extract formatting/scoreColor helpers"`

---

## PHASE 2 — Monitor shell (TitleBar, StatusBar, Rail)

### Task 2.1: TitleBar + StatusBar components

**Files:** Create `src/components/TitleBar.svelte`, `src/components/StatusBar.svelte`; Modify `src/App.svelte`

- [ ] **Step 1:** `TitleBar.svelte`: brand + version (muted sans), a flat **Edit screens** toggle button (sets `view` store to `"edit"`; shown only in monitor view), `⚙` (opens settings), window controls (`winMinimize/…`). Props/stores: `version`, `view`. Markup/styles per `full-v2.html` `.tbar`.
- [ ] **Step 2:** `StatusBar.svelte`: dot + Connected/… · screen label · score (colored) · fps · resolution; sans + tabular; hairline top; `|` separators. Reads `connection`, `screen`, `liveScore` stores.
- [ ] **Step 3:** Mount both in `App.svelte` replacing the current title bar + bottom statusbar markup. Remove the now-dead markup.
- [ ] **Step 4:** Build + check + manual (`npm run tauri dev`): title bar + status bar render with live values; window controls + ⚙ work.
- [ ] **Step 5:** Commit. `git commit -am "feat(ui): TitleBar + StatusBar components"`

### Task 2.2: RailSection + ReadoutRow + CandidateList

**Files:** Create `src/components/RailSection.svelte`, `ReadoutRow.svelte`, `CandidateList.svelte`

- [ ] **Step 1:** `RailSection.svelte`: props `title`, optional `collapsible`/`open`; renders the G1 tinted header band (full-width, hairline above) + `<slot/>` edge-to-edge. Per `mainview-grouping.html` `.gBand`.
- [ ] **Step 2:** `ReadoutRow.svelte`: props `value`, `score`, `empty` (bool), `expanded` (bool), `on:toggle`. Layout grid `1fr auto` (no field label, no chevron, no bar at rest); value truncates (`ellipsis`); score colored via `scoreColor` (dim when `empty`, showing `0.00`). Empty filler text passed as `value` (e.g. "no costume").
- [ ] **Step 3:** `CandidateList.svelte`: prop `candidates: {name,score}[]`; recessed well (`--feed`-ish `#161718`); each row grid `1fr 50px auto` = name (truncate, dim) · flat bar (`scoreColor` fill, width=score%) · score. Bars appear **only here**.
- [ ] **Step 4:** Build + check. Run: `npm run build && npm run check`. Expected: success (components not yet mounted — that's fine).
- [ ] **Step 5:** Commit. `git commit -am "feat(ui): RailSection, ReadoutRow, CandidateList"`

### Task 2.3: RaceSection + EventLog

**Files:** Create `src/components/RaceSection.svelte`, `EventLog.svelte`

- [ ] **Step 1:** `RaceSection.svelte`: reads `race` store. Render dynamic splits `Lap 1..totLap` (each `fmtSplit(splits[n])`, in-progress lap’s value live/normal, unrun laps dim "– –"), a **Total** row (bold, hairline above) = `finishTime` or running total, then **Coins**, **Mushrooms**. Label→value grid; tabular numbers.
- [ ] **Step 2:** `EventLog.svelte`: collapsible (caret on right); reads `logs` store; rows = time (tabular) + event text; sans (no mono). Cap to last N lines.
- [ ] **Step 3:** Build + check. Expected: success.
- [ ] **Step 4:** Commit. `git commit -am "feat(ui): RaceSection (dynamic splits + total) + EventLog"`

### Task 2.4: Rail + MonitorView; replace old sidebar

**Files:** Create `src/components/Rail.svelte`, `MonitorView.svelte`; Modify `src/App.svelte`

- [ ] **Step 1:** `Rail.svelte`: a **Selection** `RailSection` containing `ReadoutRow`s for screen/character/kart/course/costume (each row’s value from `selection`/`screen` stores via `screenLabel`/names; `empty` + filler "no <field>" when null; score from confidence or `candidateScores`; expanding a row renders `CandidateList` from that field’s candidate list — wired in Phase 4, until then expansion shows the single current value). Then `RaceSection`, then `EventLog`. Manage which row is `expanded` (one at a time).
- [ ] **Step 2:** `MonitorView.svelte`: flex row = feed placeholder (real feed in Phase 3) + `<Rail/>`; `<StatusBar/>` at bottom. For now the feed area can host the existing `<video bind:this={mainVideoEl}>` markup moved over (keep camera working).
- [ ] **Step 3:** In `App.svelte`, when `view==="monitor"` render `<MonitorView/>`; delete the old sidebar panel markup (Detection/Candidates/Selection/Race/Event Log) and the old feed-area duplicate. Keep `mainVideoEl` binding reachable (pass via store or prop).
- [ ] **Step 4:** Build + check + manual: monitor shows feed + the new rail with live values; expanding a selection row works (single value for now); race splits update during a race (or with replayed data).
- [ ] **Step 5:** Commit. `git commit -am "feat(ui): assemble Rail + MonitorView; remove legacy sidebar"`

---

## PHASE 3 — Annotated feed (ROI overlays)

### Task 3.1: `lib/overlay.js` — ROI box drawing

**Files:** Create `src/lib/overlay.js`

- [ ] **Step 1:** Implement `drawOverlay(ctx, {displayRect, rois, minimap, replays, sample})` where `displayRect` maps the 1080p source onto the rendered `<video>` (object-fit: contain → compute scale + letterbox offsets). Provide `roiToScreen([x1,y1,x2,y2])`. Draw each ROI rect (stroke `C.ok` matching / `C.acc` for the tell), with a small flat tag (label + optional score) — colors from `palette`. Pure function of inputs; no DOM beyond the passed `ctx`.
- [ ] **Step 2:** Build + check. Expected: success (not yet used).
- [ ] **Step 3:** Commit. `git commit -am "feat(ui): overlay.js — 1080p→display ROI drawing"`

### Task 3.2: FeedOverlay component (ROIs live over the video)

**Files:** Create `src/components/FeedOverlay.svelte`; Modify `MonitorView.svelte`

- [ ] **Step 1:** `FeedOverlay.svelte`: the `<video bind:this={mainVideoEl}>` + an absolutely-positioned `<canvas>` sized to the video box (ResizeObserver). On each rAF (while connected) compute `displayRect` and call `drawOverlay` with the **active ROIs for the current screen** — derived from `tells`/`rois` stores + `screen` (tell region(s); plus Selection ROIs on selection screens or HUD ROIs on RACING). Keep the existing feed controls (mute/volume/hide).
- [ ] **Step 2:** Ensure `tells`/`rois` are fetched on connect (`send({type:"list_tells"})`, `send({type:"list_rois"})` → stored).
- [ ] **Step 3:** Mount in `MonitorView`, replacing the bare video.
- [ ] **Step 4:** Build + check + manual: with the engine running, green ROI boxes track the correct HUD/selection regions on the live `<video>`; the tell box shows on the matched screen; boxes scale correctly when the window resizes.
- [ ] **Step 5:** Commit. `git commit -am "feat(ui): FeedOverlay draws active ROIs over the live video"`

---

## PHASE 4 — Minimap recreation + backend/IPC additions

### Task 4.1 (TDD): Backend — current-run minimap stream

**Files:** Modify `mkw_tracker/main.py`, the event emitter in `mkw_tracker/ipc/`; Test `tests/test_minimap_stream.py`

- [ ] **Step 1: failing test** — assert a helper `minimap_update_payload(state, roi)` returns `{"type":"minimap_update","cx":int,"cy":int,"radius":int,"track_state":str}` in full-frame coords (ROI offset applied), and returns `None` when `track_state=="idle"`/no lock.
```python
def test_minimap_payload_maps_to_full_frame():
    from mkw_tracker.ipc.events import minimap_update_payload
    from mkw_tracker.minimap.tracker import MinimapState, MINIMAP_ROI
    st = MinimapState(track_state="tracking", cx=10, cy=20, radius=7)  # ROI-local
    p = minimap_update_payload(st, MINIMAP_ROI)
    assert p["type"] == "minimap_update"
    assert p["cx"] == MINIMAP_ROI[0] + 10 and p["cy"] == MINIMAP_ROI[1] + 20
    assert p["track_state"] == "tracking" and p["radius"] == 7

def test_minimap_payload_idle_is_none():
    from mkw_tracker.ipc.events import minimap_update_payload
    from mkw_tracker.minimap.tracker import MinimapState
    assert minimap_update_payload(MinimapState(track_state="idle"), (0,0,1,1)) is None
```
- [ ] **Step 2:** Run: `.venv\Scripts\python.exe -m pytest tests/test_minimap_stream.py -q` → FAIL.
- [ ] **Step 3:** Implement `minimap_update_payload` in `mkw_tracker/ipc/events.py` (or the existing event-types module — match where `screen_change` etc. are built). Confirm `MinimapState` exposes `cx/cy/radius/track_state` (adapt field names to the real dataclass; add a tiny accessor if needed without changing tracker logic).
- [ ] **Step 4:** Run pytest → PASS.
- [ ] **Step 5:** In `main.py`, during `RACING`, throttle-emit the payload (~15 Hz; skip when `None` or unchanged) to stdout alongside other events. Do **not** emit replays.
- [ ] **Step 6:** Commit. `git add -A && git commit -m "feat(ipc): stream current-run minimap_update during RACING"`

### Task 4.2 (TDD): Backend — replay paths + icon sample fetch

**Files:** Modify `mkw_tracker/ipc/sidecar.py` (or `broadcaster.py` handler table) + `database/replay_repo.py` + `minimap/tracker.py`; Test `tests/test_replay_paths.py`

- [ ] **Step 1: failing test** — `get_replay_paths(course)` returns `{"type":"replay_paths","course":...,"paths":[{"id":...,"points":[[x,y],…]}]}` from the DB; points in full-frame coords.
```python
def test_get_replay_paths_shape(tmp_db_with_one_replay):
    from mkw_tracker.database import replay_repo
    res = replay_repo.replay_paths(tmp_db_with_one_replay, course="Rainbow Road")
    assert res and all("points" in p and len(p["points"]) > 0 for p in res)
```
- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement `replay_repo.replay_paths(conn, course)` (read stored minimap traces for the course). Add IPC handlers `get_replay_paths {course}` → `replay_paths` event, and `get_minimap_sample {course}` → `minimap_sample {png_b64}` (the locked HSV template crop; add a `tracker.sample_png()` helper that returns the seed template as PNG bytes).
- [ ] **Step 4:** Run pytest → PASS. Also add a handler test that the dispatch returns the right `type`.
- [ ] **Step 5:** Update `docs/ipc-protocol.md` (new commands + events).
- [ ] **Step 6:** Commit. `git add -A && git commit -m "feat(ipc): get_replay_paths + get_minimap_sample handlers"`

### Task 4.3 (TDD): Backend — ranked selection candidates

**Files:** Modify `mkw_tracker/detection/selection.py` + state emission in `main.py`; Test `tests/test_selection_candidates.py`

- [ ] **Step 1: failing test** — the selection scan can return top-N `{name,score}` per field (character/kart/course/costume), sorted desc.
```python
def test_selection_topn_sorted():
    from mkw_tracker.detection.selection import top_candidates
    scores = {"Mario":0.95,"Luigi":0.40,"Peach":0.72}
    out = top_candidates(scores, n=3)
    assert [c["name"] for c in out] == ["Mario","Peach","Luigi"]
    assert out[0]["score"] == 0.95
```
- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement `top_candidates(score_map, n)` and have the selection tracker retain the per-field score map it already computes (it scores all templates; keep the ranked list, don't just keep the argmax). Include `candidates: {char:[…],kart:[…],course:[…],costume:[…]}` in the `state` snapshot (and/or `selection_update`). **If a field can't cheaply produce a map, omit it — its row simply won't expand** (spec open-item).
- [ ] **Step 4:** Run pytest → PASS.
- [ ] **Step 5:** Commit. `git add -A && git commit -m "feat(detection): expose ranked per-field selection candidates"`

### Task 4.4: Frontend — minimap drawing + wiring

**Files:** Modify `src/lib/overlay.js`, `src/lib/ipc.js`, `src/lib/stores.js`, `src/components/FeedOverlay.svelte`, `src/components/Rail.svelte`

- [ ] **Step 1:** `overlay.js`: extend `drawOverlay` to draw the minimap region box, the **icon sample** inset (decoded `<img>` from `minimap_sample`), the **live tracking** dot + Hough ring colored by `track_state` (`C.mmRingFace/mmRingOnly/mmReacquire`), and **replay trails** (polylines, muted hues per `id`). All mapped via `roiToScreen` from `MINIMAP_ROI`.
- [ ] **Step 2:** `ipc.js`: route `minimap_update` → `minimap` store; `replay_paths` → `replays` store; `minimap_sample` → `sample` store. On course known (from `selection`/state), `send({type:"get_replay_paths",course})` + `send({type:"get_minimap_sample",course})`.
- [ ] **Step 3:** `FeedOverlay`: pass `minimap`, `replays`, `sample` into `drawOverlay`.
- [ ] **Step 4:** `Rail`: wire `CandidateList` to the new `candidates` store per field (now expansions show real ranked candidates with bars).
- [ ] **Step 5:** Build + check + manual: during a race, the minimap shows the sample inset, a tracking dot/ring that follows the player and changes color with state, and faint replay trails for the course; rail expansions show real candidates.
- [ ] **Step 6:** Commit. `git commit -am "feat(ui): minimap recreation (sample/tracking/replays) + candidate expansions"`

---

## PHASE 5 — Edit mode

### Task 5.1: `lib/graph.js` + ScreenGraph (top strip)

**Files:** Create `src/lib/graph.js`, `src/components/ScreenGraph.svelte`; Modify `App.svelte`

- [ ] **Step 1:** Move graph layout + pan/zoom state/handlers (`gZoom,gPanX,gPanY,onGraphWheel,onGraphDown/Move/Up,fitGraph,nodeClick`, `GRAPH_W/H`, node positions, `TRANSITIONS` edges) into `lib/graph.js` (pure helpers) + `ScreenGraph.svelte` (the SVG, top-strip oriented). Clicking a node sets a `selectedNode` store and keeps `view==="edit"`. Current node highlighted (accent border).
- [ ] **Step 2:** Build + check. Expected: success.
- [ ] **Step 3:** Commit. `git commit -am "feat(ui): extract graph logic + ScreenGraph top-strip navigator"`

### Task 5.2: RoiCanvas (zoomable feed + editable ROIs)

**Files:** Create `src/components/RoiCanvas.svelte`; Modify `App.svelte` (move canvas logic)

- [ ] **Step 1:** Move the ROI canvas logic (`canvasEl`, `fZoom/fPanX/fPanY`, drag/resize handles `HANDLE_HIT_RADIUS`, `editRois()` coloring active/sibling/other, `liveRoiCrop`, the engine-frame poll while editing) into `RoiCanvas.svelte`. Active region accent, sibling neutral grey, other-group warn (from palette). Preserve drag/resize/handle behavior exactly.
- [ ] **Step 2:** Build + check. Expected: success.
- [ ] **Step 3:** Commit. `git commit -am "feat(ui): RoiCanvas component (zoom/pan + ROI handles)"`

### Task 5.3: DetectionTree + RegionInspector + ReadoutRoiEditor

**Files:** Create `src/components/DetectionTree.svelte`, `RegionInspector.svelte`, `ReadoutRoiEditor.svelte`

- [ ] **Step 1:** `DetectionTree.svelte`: render `editTell.groups` (AND of groups / OR of regions); port `selectRegion/addRegion/addGroup/removeActiveRegion/onKindChange/recaptureRegion/resetDetection`; active region marked by a thin accent edge. Flat hairline group blocks.
- [ ] **Step 2:** `RegionInspector.svelte`: the **Live** crop vs stored **Template** thumbnails (`liveCropImg`/`templateImg` via `get_region_images`/`test_region`), Match score + flat bar, **Capture** (`capture_region_template`) + **Test** (`test_region`). Costume → live crop shown as edges. Port the existing poll/lifecycle.
- [ ] **Step 3:** `ReadoutRoiEditor.svelte`: selection/HUD ROI list (`NODE_SELECTION`/`NODE_HUD`), per-item template capture (`capture_asset_template`/`get_asset_template`), reset ROI (`reset_roi`). 
- [ ] **Step 4:** Build + check. Expected: success.
- [ ] **Step 5:** Commit. `git commit -am "feat(ui): DetectionTree + RegionInspector + ReadoutRoiEditor"`

### Task 5.4: ToolsPanel (2 tabs) + EditMode assembly

**Files:** Create `src/components/ToolsPanel.svelte`, `EditMode.svelte`; Modify `App.svelte`

- [ ] **Step 1:** `ToolsPanel.svelte`: two tabs — **Detection** (always) + **Readout** (merged Selection/HUD; **disabled** when `NODE_SELECTION[node]` and `NODE_HUD[node]` are both empty). Inset-underline active tab. Renders `DetectionTree`+`RegionInspector` or `ReadoutRoiEditor`.
- [ ] **Step 2:** `EditMode.svelte`: top `<ScreenGraph/>` strip · center `<RoiCanvas/>` · right `<ToolsPanel/>` · `<StatusBar/>`. Title bar shows "Editing — <screen>" + "← Monitor" (sets `view="monitor"`).
- [ ] **Step 3:** In `App.svelte`, render `<EditMode/>` when `view==="edit"`. Remove the old in-place editor markup from the legacy file.
- [ ] **Step 4:** Build + check + manual: from monitor, "Edit screens" → graph strip; click a node → that screen loads in canvas + tools; Detection tree edits (add/remove/capture/test/reset) all work; Readout tab edits selection/HUD ROIs + per-item capture; Readout disabled on Title/Reset; "← Monitor" returns.
- [ ] **Step 5:** Commit. `git commit -am "feat(ui): ToolsPanel + EditMode assembly; remove legacy in-place editor"`

---

## PHASE 6 — Setup / Settings

### Task 6.1: `lib/camera.js` extraction

**Files:** Create `src/lib/camera.js`; Modify `App.svelte`

- [ ] **Step 1:** Move `loadBrowserDevices`, `startCamera`, `handleCameraDeviceChange`, device/audio enumeration + selection state, dual-feed status (`cameraOk/cameraStatus/pythonCameraOk/pythonCameraStatus/trackerCameraPaused/restartNeeded`), and `mainVideoEl`/`wizVideoEl` wiring into `lib/camera.js` (functions operating on the `devices`/camera stores) — **port exactly**; this is the riskiest extraction (don't lose functionality).
- [ ] **Step 2:** Build + check + manual: camera still opens, device switch still restarts the tracker, statuses still reflect reality.
- [ ] **Step 3:** Commit. `git commit -am "refactor(ui): extract camera/device logic to lib/camera.js"`

### Task 6.2: SourceCheck + DeviceSelectors + LanguageSelectors + SettingsModal

**Files:** Create `src/components/SourceCheck.svelte`, `DeviceSelectors.svelte`, `LanguageSelectors.svelte`, `SettingsModal.svelte`; Modify `App.svelte`

- [ ] **Step 1:** `SourceCheck.svelte`: two panes side-by-side — **App feed** (`<video bind:wizVideoEl>`) and **Python engine input** (`<img src={engineFrame}>`) — each with its status row (all existing states: Connected/Opening/Blocked/Released/Error). This is the **only** place `engineFrame` renders.
- [ ] **Step 2:** `DeviceSelectors.svelte`: **Video** (renamed from Camera) select bound to `browserDevices`/`handleCameraDeviceChange`; **Audio** select bound to `audioDevices`/`selectedAudioDeviceId`. `restartNeeded` → Restart button.
- [ ] **Step 3:** `LanguageSelectors.svelte`: **Application language** (`appLanguage`) + **Switch system language** (`switch2Language`), both from `LANGUAGES`, each persisted via the existing `update_config`/config path.
- [ ] **Step 4:** `SettingsModal.svelte`: modal shell (⚙ opens; ✕/Cancel/Done). First-run (`setupComplete===false`) renders the same fields stepped (Languages → Video → Done) with the existing gating; returning user sees them all at once.
- [ ] **Step 5:** In `App.svelte`, render `<SettingsModal/>` when `view==="settings"` or first-run; remove the legacy wizard/modal markup + the vestigial `lang-dialog`.
- [ ] **Step 6:** Build + check + manual: ⚙ opens settings; both feeds show and report status; switching Video device restarts + both panes update; Audio device persists; both languages persist; first-run flow completes and gates the monitor.
- [ ] **Step 7:** Commit. `git commit -am "feat(ui): SettingsModal — dual-feed source check, Video/Audio, dual languages"`

---

## PHASE 7 — Cleanup & regression sweep

### Task 7.1: Thin the shell + delete dead code/CSS

**Files:** Modify `src/App.svelte` (+ delete orphaned styles)

- [ ] **Step 1:** `App.svelte` should now be: imports, `initIpc()` in `onMount`, store-driven `view` switch rendering `<TitleBar/>` + (`<MonitorView/>`|`<EditMode/>`) + `<SettingsModal/>`. Delete every block superseded by a component, and all `<style>` rules no longer referenced.
- [ ] **Step 2:** Run: `npm run check` — drive new unused-CSS/var warnings to zero (only the documented pre-existing ones may remain, and ideally fix those too).
- [ ] **Step 3:** Build. Run: `npm run build`. Expected: success.
- [ ] **Step 4:** Commit. `git commit -am "refactor(ui): reduce App.svelte to a thin shell; remove dead code/CSS"`

### Task 7.2: Functionality regression walk

**Files:** none (verification) — update `functionality-inventory.md` checkboxes

- [ ] **Step 1:** With `npm run tauri dev` (engine running, or `--video temp/aiden.mp4` per CLAUDE.md), walk **every** item in `functionality-inventory.md`: window controls; updater; connection; readout (screen/score/candidates/selection/laps/coins/mushrooms/splits/finish); event log; graph nav; detection editing (add/remove region+group, kind, capture, test, reset); selection/HUD ROI editing + per-item capture + reset; settings (×2 language, video, audio, dual-feed verify, restart); first-run; feed controls. Tick each; file a fix task for any miss.
- [ ] **Step 2:** Confirm every IPC command in `docs/ipc-protocol.md` is still reachable from the UI (grep `send(` across `src/`).
- [ ] **Step 3:** Commit. `git commit -am "test(ui): functionality regression walk complete; inventory ticked"`

### Task 7.3: Docs + final build

**Files:** Modify `docs/architecture.md` (frontend/in-app editor section), `CLAUDE.md` (frontend description), `docs/ui-theme.md`

- [ ] **Step 1:** Update the frontend descriptions to the new component architecture + Monitor/Edit modes + annotated feed + dual-feed settings.
- [ ] **Step 2:** Run: `npm run build && npm run check`. Expected: success, warnings at/below baseline.
- [ ] **Step 3:** Commit. `git commit -am "docs: update architecture/CLAUDE/ui-theme for the redesigned frontend"`

---

## Self-review notes (author)

- **Spec coverage:** Monitor (2,3,4) · annotated feed+minimap (3,4) · rail incl. label-less rows/expansions/empty-0.00/splits/total (2.2–2.4,4.4) · Edit mode incl. graph-top-strip/2-tab/live-vs-template (5) · dual-feed settings + Video rename + dual languages + engineFrame-only-here (6) · de-monolith (1,5,6,7) · IPC additions (4) · de-slop styling (1.2 tokens, applied throughout) · "don't lose functionality" (1.1 inventory + 7.2 walk). All mapped.
- **Open item carried from spec:** ranked candidates per field (4.3) — graceful degradation specified (non-expanding row).
- **Naming consistency:** stores (`minimap`, `candidates`, `selection`, `race`, `view`), `drawOverlay`, `roiToScreen`, `scoreColor`, `screenLabel`, `minimap_update`/`replay_paths`/`minimap_sample` events used consistently across tasks.
- **Granularity caveat (deliberate):** Svelte component tasks specify files/props/store-wiring/verification rather than full markup, because exact markup/styles live in the spec + committed mockups and reproducing 3k lines inline would be error-prone. Backend/IPC tasks (Phase 4) carry full test+impl code per TDD.
```
