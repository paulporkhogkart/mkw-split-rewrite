# WR Client Dots (Plan 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw world-record ghost dots on the minimap reconstruction: the current WR as a pulsing grey dot, optional grey history, fed by `GET /v1/wr-trails` through the existing course-reads cache.

**Architecture:** The WR renders as one more (grey) player. `sync_course_reads` gains a fourth GET whose rows land in the cached payload under `wr_trails`; `buildTrailRuns` maps them to ordinary runs (the current WR is the WR player's "PB": `is_pb` drives the existing overlay breathe, so `overlay.js` is untouched); an explicit band formula implements the two-tier z-order (every alive run above every abandoned run; within a tier the WR yields to players of its rank). One new settings key (`wr.mode`), one new row in the Trails tab.

**Tech Stack:** Rust (tauri v2, reqwest, serde_json) in `src-tauri/src/sync.rs`; Svelte 4 + vitest 4 in `src/`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-18-wr-client-dots-design.md` — read it first; the decisions table is binding.

## Global Constraints

- **NEVER modify `mkw_tracker/`** (the Python engine). Plan 4 is client-only; **no `pi/` changes** either.
- **No `[dev-dependencies]`** may be added to `src-tauri/Cargo.toml`. No new npm packages.
- **No em dashes in any user-facing string** (tooltips, hints, labels, errors). Docs and code comments are fine.
- **`WR_COLOR = "#a7adb5"` is locked** and never user-configurable (the `trailSettings.js:5` rule). Same for the band hierarchy: copy it from the spec verbatim, do not "improve" it.
- **Tauri camelCase mapping:** JS invoke arg `wrMode` ↔ Rust param `wr_mode`. Getting this wrong rejects the whole command silently (see `src/lib/sync.js:7`).
- Suites at plan start: `cd src-tauri && cargo test` → **118 passed + 1 ignored, zero warnings both profiles**; root `npm run test:js` → **166**; `npm run check` (svelte-check) → 0 errors 0 warnings; `cd pi && npx vitest run` → 610 (must stay untouched). The ~77s ignored fixture test is FINAL-GATE ONLY; never run it mid-task.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Stage ONLY your named files (`git add <paths>`, never `-A`). Never push or tag.

---

### Task 1: `trailSettings.js` — WR pseudo-player model + two-tier band sort + legend row

**Files:**
- Modify: `src/lib/trailSettings.js`
- Test: `src/lib/trailSettings.test.js`

**Interfaces:**
- Consumes: the existing exports (`playerColor`, `playerCfg`, `rankOpacity`, `buildTrailRuns`, `trailLegendRows`) and the wire row shape of `courseReads.wr_trails`: `{ wr_id, holder_name, record_ms, record_str, achieved_at, is_current (0/1), video_url, points: [[t_ms,cx,cy,score],...] }`, fastest-first (`pi/src/db/wrTrails.ts:30`).
- Produces (later tasks rely on these exact names):
  - `export const WR_COLOR = "#a7adb5"` — locked grey (Task 3 chip).
  - `export const WR_MODES = ["off", "current", "all"]`.
  - `export function wrCfg(settings) -> { mode }` — defaulted + validated (Task 3 UI reads it, Task 3 App.svelte sends `wrCfg(settings).mode` as `wrMode`).
  - `export function bandOf(run) -> number` — paint band, exported for table tests.
  - `buildTrailRuns` (same signature) now also consumes `courseReads.wr_trails` and `settings.wr`; run objects gain a `wr: null | "current" | "historic"` field.
  - `trailLegendRows` (same signature) appends a `{ name: "WR", color: WR_COLOR, mode, n: 1 }` row iff `wrCfg(settings).mode !== "off"`.

- [ ] **Step 1: Write the failing tests**

Append a new describe block to `src/lib/trailSettings.test.js`, and extend the import line to pull the new exports:

```js
import { playerColor, playerCfg, activeConfig, rankOpacity, buildTrailRuns, trailLegendRows, TRAIL_PRESETS, wrCfg, bandOf, WR_COLOR } from "./trailSettings.js";
```

```js
describe("WR trails (the WR is one more grey player; spec 2026-07-18)", () => {
  // Wire rows as the Pi serves them: fastest-first, is_current 0/1 integers.
  const wrRows = [
    { wr_id: 9, holder_name: "JaK", record_ms: 62934, is_current: 1, points: [[0, 90, 0, 1]] },
    { wr_id: 7, holder_name: "Old", record_ms: 64000, is_current: 0, points: [[0, 91, 0, 1]] },
  ];

  it("wrCfg defaults to current; unknown stored values fall back to current", () => {
    expect(wrCfg({})).toEqual({ mode: "current" });
    expect(wrCfg(undefined)).toEqual({ mode: "current" });
    expect(wrCfg({ wr: { mode: "off" } })).toEqual({ mode: "off" });
    expect(wrCfg({ wr: { mode: "all" } })).toEqual({ mode: "all" });
    expect(wrCfg({ wr: { mode: "garbage" } })).toEqual({ mode: "current" });
  });

  it("mode current shows only the current WR; all shows history too; off shows none", () => {
    const reads = { trails: [], wr_trails: wrRows };
    expect(buildTrailRuns(reads, { players: {}, wr: { mode: "current" } }, roster)
      .map((r) => r.points[0][1])).toEqual([90]);
    // all: historic band sits below the current WR's band.
    expect(buildTrailRuns(reads, { players: {}, wr: { mode: "all" } }, roster)
      .map((r) => r.points[0][1])).toEqual([91, 90]);
    expect(buildTrailRuns(reads, { players: {}, wr: { mode: "off" } }, roster)).toEqual([]);
  });

  it("the current WR is grey, pulses like a PB, and paints directly UNDER player PBs", () => {
    const reads = {
      trails: [
        { player_id: 2, status: "finished", is_pb: true,  total_ms: 100000, points: [[0, 1, 0, 1]] },
        { player_id: 2, status: "finished", is_pb: false, total_ms: 110000, points: [[0, 2, 0, 1]] },
      ],
      wr_trails: [wrRows[0]],
    };
    const out = buildTrailRuns(reads, { players: {}, wr: { mode: "current" } }, roster);
    // Bottom -> top: player ghost, current WR, player PB. The WR yields to players
    // within its rank (decided 2026-07-18, supersedes the earlier above-PBs call).
    expect(out.map((r) => r.points[0][1])).toEqual([2, 90, 1]);
    expect(out[1]).toMatchObject({ color: WR_COLOR, is_pb: true, abandoned: false, wr: "current" });
  });

  it("historic WRs sort under all alive player past runs and obey the fade toggle", () => {
    const reads = {
      trails: [{ player_id: 2, status: "finished", is_pb: false, total_ms: 110000, points: [[0, 2, 0, 1]] }],
      wr_trails: wrRows,
    };
    const out = buildTrailRuns(reads, { fadeByRank: true, players: {}, wr: { mode: "all" } }, roster);
    // historic WR < alive player past run < current WR
    expect(out.map((r) => r.points[0][1])).toEqual([91, 2, 90]);
    // Fade parity: the historic row is index 1 of the fastest-first WR set, exactly
    // like a player's non-PB run at rank 1 of 2 (no special dimming anywhere).
    expect(out[0].opacity).toBe(rankOpacity(1, 2, true));
    expect(out[0]).toMatchObject({ is_pb: false, wr: "historic", color: WR_COLOR });
  });

  it("two-tier band formula: every alive run outranks every abandoned one", () => {
    // Paul's canonical impossible case: a dead current WR sits under an alive player past run.
    expect(bandOf({ wr: "current", is_pb: true, abandoned: true }))
      .toBeLessThan(bandOf({ wr: null, is_pb: false, abandoned: false }));
    expect(bandOf({ wr: null, is_pb: false, abandoned: true }))
      .toBeLessThan(bandOf({ wr: "historic", is_pb: false, abandoned: false }));
    // Within a tier: historic WR < player past run < current WR < player PB.
    expect([
      bandOf({ wr: "historic", is_pb: false, abandoned: false }),
      bandOf({ wr: null, is_pb: false, abandoned: false }),
      bandOf({ wr: "current", is_pb: true, abandoned: false }),
      bandOf({ wr: null, is_pb: true, abandoned: false }),
    ]).toEqual([4, 5, 6, 7]);
    // The abandoned tier mirrors it 4 lower.
    expect(bandOf({ wr: null, is_pb: false, abandoned: true })).toBe(1);
    expect(bandOf({ wr: null, is_pb: true, abandoned: true })).toBe(3);
  });

  it("a stale cached payload without wr_trails yields no WR runs and no crash", () => {
    const reads = { trails: [{ player_id: 2, status: "finished", is_pb: false, points: [[0, 2, 0, 1]] }] };
    const out = buildTrailRuns(reads, { players: {}, wr: { mode: "current" } }, roster);
    expect(out).toHaveLength(1);
    expect(out[0].wr).toBe(null);
  });

  it("legend gains a grey WR row iff mode != off", () => {
    const rows = trailLegendRows({ players: {}, wr: { mode: "all" } }, roster);
    expect(rows[rows.length - 1]).toMatchObject({ name: "WR", color: WR_COLOR, mode: "all" });
    expect(trailLegendRows({ players: {}, wr: { mode: "off" } }, roster)
      .map((r) => r.name)).not.toContain("WR");
  });
});
```

Also UPDATE the existing legend test (it pins the pre-WR behavior; default mode is now
`current`, so the WR row legitimately appears — this is the product default, not test
breakage):

```js
  it("trailLegendRows lists active players with colour + mode, plus the default-on WR row", () => {
    const s = { players: { 2: { mode: "none" } } };
    expect(trailLegendRows(s, roster).map((r) => [r.name, r.mode, r.color])).toEqual([
      ["Paul", "last_pb", playerColor(roster[0])],
      ["Alex", "last_pb", playerColor(roster[2])],
      ["WR", "current", WR_COLOR],
    ]);
  });
```

The five other existing tests must NOT be edited — their inputs lack `wr_trails`, so
their outputs are unchanged by construction (that invariance is part of what this task
proves).

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:js -- src/lib/trailSettings.test.js`
Expected: FAIL — the import line errors (`wrCfg` is not exported) or every new test fails; the updated legend test fails with a missing WR row.

- [ ] **Step 3: Implement in `src/lib/trailSettings.js`**

3a. Below `TRAIL_MODES` (line 8), add:

```js
// The world record renders as one more (grey) player: its "PB" is the current WR, its
// ghosts are the historic ones. Colour locked like the player palette above.
export const WR_COLOR = "#a7adb5";
export const WR_MODES = ["off", "current", "all"];
```

3b. Change `DEFAULTS` (line 13) to:

```js
const DEFAULTS = { fadeByRank: false, players: {}, wr: { mode: "current" } };   // players: { [playerId]: {mode, n} }
```

(`loadSettings`'s `{ ...DEFAULTS, ...stored }` spread and `resetTrailSettings`'s
`{ ...DEFAULTS, players: {} }` both pick the new key up unchanged.)

3c. Below `playerCfg`, add:

```js
/** The WR pseudo-player's effective config. Default: the current WR only, on
 *  (spec 2026-07-18). Unknown stored values fall back to the default. */
export function wrCfg(settings) {
  const m = settings?.wr?.mode;
  return { mode: WR_MODES.includes(m) ? m : "current" };
}
```

3d. Below `rankOpacity`, add:

```js
/** Paint band (ascending = bottom to top). Two tiers - every alive run outranks every
 *  abandoned (X-ending) one - and within a tier the WR yields to players of its rank:
 *  historic WR < player past run < current WR < player PB (decided 2026-07-18). A
 *  run's tier is its static abandoned flag, so the paint order never reshuffles
 *  mid-race when a dot visually becomes its X. */
export function bandOf(run) {
  const rank = run.wr === "historic" ? 0 : run.wr === "current" ? 2 : run.is_pb ? 3 : 1;
  return (run.abandoned ? 0 : 4) + rank;
}
```

3e. In `buildTrailRuns`: add `wr: null` to the player-run object literal (after
`total_ms`), then REPLACE the whole sort block (the `// Global paint order = ...`
comment through `out.sort(...)`) with WR-row mapping plus the band sort:

```js
  // The WR is one more (grey) player. Same opacity rules as everyone (rankOpacity over
  // the fastest-first rows; the fade toggle applies); a stored WR trail is by
  // construction a verified finished run, so abandoned is always false.
  const wrMode = wrCfg(settings).mode;
  if (wrMode !== "off") {
    const rows = (courseReads?.wr_trails ?? []).filter((w) => wrMode === "all" || w.is_current);
    rows.forEach((w, i) => {
      out.push({
        points: w.points ?? [],
        color: WR_COLOR,
        opacity: w.is_current ? 1 : rankOpacity(i, rows.length, settings.fadeByRank),
        abandoned: false,
        is_pb: !!w.is_current,                    // the current WR breathes like a PB
        total_ms: w.record_ms ?? null,
        wr: w.is_current ? "current" : "historic",
      });
    });
  }
  // Global paint order = z-order (last = on top): the two-tier band hierarchy (bandOf),
  // then the existing tiebreaks - fainter runs lower, faster runs higher.
  out.sort((a, b) => {
    if (bandOf(a) !== bandOf(b)) return bandOf(a) - bandOf(b);
    if (a.opacity !== b.opacity) return a.opacity - b.opacity;
    const at = a.total_ms ?? Infinity, bt = b.total_ms ?? Infinity;
    return at === bt ? 0 : bt - at;
  });
  return out;
```

(The old `is_pb` sort clause is subsumed: player PBs are band 7, player ghosts band 5,
so both prior z-order tests keep passing with byte-identical expected outputs.)

3f. In `trailLegendRows`, append the WR row before returning:

```js
export function trailLegendRows(settings, rosterList) {
  const rows = (rosterList ?? [])
    .map((p) => ({ name: p.display_name, color: playerColor(p), ...playerCfg(settings, p) }))
    .filter((r) => r.mode !== "none");
  const wr = wrCfg(settings);
  if (wr.mode !== "off") rows.push({ name: "WR", color: WR_COLOR, mode: wr.mode, n: 1 });
  return rows;
}
```

- [ ] **Step 4: Run the whole JS suite**

Run: `npm run test:js`
Expected: **173 passed** (166 baseline + 7 new; the rewritten legend test replaces its
old self). Zero failures — especially the two untouched `buildTrailRuns` z-order tests.

- [ ] **Step 5: Commit**

```bash
git add src/lib/trailSettings.js src/lib/trailSettings.test.js
git commit -m "feat(trails): WR renders as one more grey player + two-tier band z-order

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `sync.rs` — `wr_mode` param, `/v1/wr-trails` read, payload key

**Files:**
- Modify: `src-tauri/src/sync.rs` (the course-reads section, currently lines ~588-658, and the `#[cfg(test)] mod tests` at ~805)

**Interfaces:**
- Consumes: nothing new; the Pi endpoint `GET {base}/v1/wr-trails?course=&cc=150` already exists (public, `pi/src/api/reads.ts:57`).
- Produces: `sync_course_reads(course, config, wr_mode: Option<String>)` — the JS side (Task 3) sends the third arg as `wrMode`. Payload JSON gains `"wr_trails": [...]`. Private helper `fn resolve_wr_mode(raw: Option<&str>) -> &'static str`.

- [ ] **Step 1: Write the failing tests**

Inside `mod tests` in `src-tauri/src/sync.rs` (convention: plain `#[test]` fns using
`super::*`), add:

```rust
    #[test]
    fn empty_course_reads_carries_every_payload_key() {
        // The offline/unconfigured fallback must have the same shape as a live payload,
        // or the frontend's key reads differ between online and offline.
        let v: serde_json::Value = serde_json::from_str(EMPTY_COURSE_READS).unwrap();
        for key in ["pb_splits", "trails", "friends_pbs", "wr_trails"] {
            assert!(v.get(key).is_some(), "EMPTY_COURSE_READS missing {key}");
        }
        assert!(v["wr_trails"].as_array().unwrap().is_empty());
    }

    #[test]
    fn wr_mode_resolution_defaults_unknowns_to_current() {
        // Missing or unrecognized means the product default, one validation point
        // (spec 2026-07-18 §1). "off" is what gates the GET entirely.
        assert_eq!(resolve_wr_mode(None), "current");
        assert_eq!(resolve_wr_mode(Some("current")), "current");
        assert_eq!(resolve_wr_mode(Some("off")), "off");
        assert_eq!(resolve_wr_mode(Some("all")), "all");
        assert_eq!(resolve_wr_mode(Some("garbage")), "current");
        assert_eq!(resolve_wr_mode(Some("")), "current");
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src-tauri && cargo test --lib 2>&1 | tail -5`
(Note: `cargo test` takes a single filter argument, so run the whole lib rather than
trying to name both tests.)
Expected: COMPILE ERROR — `resolve_wr_mode` not found (E0425 is the honest RED for a
signature-driven change). After Step 3a alone compiles it, the shape test must fail
until 3a is applied and the resolution test until 3b is.

- [ ] **Step 3: Implement**

3a. Change the `EMPTY_COURSE_READS` const (line ~588) to:

```rust
const EMPTY_COURSE_READS: &str = r#"{"pb_splits":{"total_ms":null,"splits":{}},"trails":[],"friends_pbs":[],"wr_trails":[]}"#;
```

3b. Below the `PlayerTrailCfg` struct, add:

```rust
/// The WR display mode from the frontend's Trails settings. Missing or unrecognized
/// values mean "current" (the product default; spec 2026-07-18 §1) - one validation
/// point, so a typo can never silently disable the fetch OR invent a mode.
fn resolve_wr_mode(raw: Option<&str>) -> &'static str {
    match raw {
        Some("off") => "off",
        Some("all") => "all",
        _ => "current",
    }
}
```

3c. Extend `fetch_course_reads` — new parameter and a fourth read. The signature becomes:

```rust
async fn fetch_course_reads(cfg: &Config, course: &str, players: &[PlayerTrailCfg], wr_mode: &str) -> Result<String, String> {
```

and immediately before the final `Ok(serde_json::json!({...}))`, add:

```rust
    // WR trails: public read, no token needed. Degrade, never fail - a Pi that
    // predates the endpoint (or a transient error) must not take PB splits and
    // player trails down with it; the dots just sit out this fetch.
    let wr = if wr_mode == "off" {
        serde_json::json!([])
    } else {
        get_json(client.get(format!("{base}/v1/wr-trails")).query(&q), "wr-trails")
            .await
            .unwrap_or_else(|e| { log::debug!("[sync] wr-trails: {e}"); serde_json::json!([]) })
    };
```

and change the final line to include the key:

```rust
    Ok(serde_json::json!({ "pb_splits": pb, "trails": trails, "friends_pbs": fp, "wr_trails": wr }).to_string())
```

3d. Extend the command (line ~637):

```rust
#[tauri::command]
pub async fn sync_course_reads(course: String, config: Option<Vec<PlayerTrailCfg>>, wr_mode: Option<String>) -> String {
```

and pass it through where `fetch_course_reads` is called:

```rust
    match fetch_course_reads(&cfg, &course, &players, resolve_wr_mode(wr_mode.as_deref())).await {
```

- [ ] **Step 4: Run the suite, both profiles warning-clean**

Run: `cd src-tauri && cargo test 2>&1 | tail -3`
Expected: **120 passed; 0 failed; 1 ignored** (118 + the 2 new).
Run: `cargo check --release 2>&1 | tail -2` and confirm zero warnings in both invocations' output.

- [ ] **Step 5: Commit**

```bash
git add src/sync.rs
git commit -m "feat(sync): wr_trails ride the course-reads payload behind a wrMode gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Run the `git` commands from `src-tauri/`; the path staged is `src-tauri/src/sync.rs`
from the repo root.)

---

### Task 3: Trails tab WR row + App.svelte wiring

**Files:**
- Modify: `src/components/TrailSettings.svelte`
- Modify: `src/App.svelte` (two lines: the import at line ~38, the invoke at line ~730)

**Interfaces:**
- Consumes: `wrCfg`, `WR_COLOR` from Task 1; the `wrMode` arg of Task 2's command.
- Produces: nothing downstream; this is the last functional wiring.

There are NO honest unit tests for a settings row or an invoke argument (house rule:
window/UI lifecycle tasks gate on build + check + the untouched suites; do not write
tests that assert nothing).

- [ ] **Step 1: TrailSettings.svelte — WR block**

1a. Extend the import (line 7) to:

```js
  import { trailSettings, roster, cacheRoster, playerCfg, playerColor, resetTrailSettings, wrCfg, WR_COLOR } from "../lib/trailSettings.js";
```

1b. Below the `usesN` helper (line 15), add:

```js
  const WR_MODE_OPTS = [["off", "Off"], ["current", "Current WR"], ["all", "All WRs"]];
  const setWrMode = (m) => trailSettings.update((s) => ({ ...s, wr: { mode: m } }));
  $: wrmode = wrCfg($trailSettings).mode;
```

(A reactive statement, NOT a template `{@const}` — Svelte 4 only allows `{@const}` as
the immediate child of block constructs, and this sits in a plain `<div>`.)

1c. Between the closing `</label>` of the fade toggle (line 40) and the
`{#if $roster.length === 0}` block, insert (deliberately OUTSIDE the roster
conditional — the WR row needs no roster and must work for a fresh install):

```svelte
  <div class="grid">
    <div class="row head"><span>World record</span><span>Show</span><span class="ralign"></span><span class="calign">Colour</span></div>
    <div class="row" class:off={wrmode === "off"}>
      <span class="pn">WR</span>
      <select value={wrmode} on:change={(e) => setWrMode(e.target.value)}>
        {#each WR_MODE_OPTS as [v, l]}<option value={v}>{l}</option>{/each}
      </select>
      <span></span>
      <span class="chip" class:off={wrmode === "off"}
        style={wrmode === "off" ? "" : `background:${WR_COLOR}`} title="Locked colour"></span>
    </div>
  </div>
  <p class="note">The current world record replays as a grey dot. It pulses so you can tell it apart from player ghosts. All WRs adds the older records this track has had.</p>
```

Copy is em-dash-free by rule; keep it verbatim.

- [ ] **Step 2: App.svelte — send the mode**

2a. Line ~38, extend the trailSettings import with `wrCfg`:

```js
  import { trailSettings as trailSettingsStore, roster as rosterStore, cacheRoster,
           activeConfig, buildTrailRuns, trailLegendRows, wrCfg } from "./lib/trailSettings.js";
```

(Match the file's ACTUAL current import line — add `wrCfg` to it, do not reshape it.)

2b. Line ~730, the invoke gains the third arg:

```js
      const r = JSON.parse(await invoke("sync_course_reads", { course, config: activeConfig(settings, rosterList), wrMode: wrCfg(settings).mode }));
```

- [ ] **Step 3: Gates**

Run: `npm run test:js` → 173 passed (unchanged from Task 1).
Run: `npm run check` → 0 errors, 0 warnings.
Run: `npm run build` → completes clean.

- [ ] **Step 4: Commit**

```bash
git add src/components/TrailSettings.svelte src/App.svelte
git commit -m "feat(trails): World record row in the Trails tab + wrMode wired to course reads

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Docs sync

**Files:**
- Modify: `CLAUDE.md` (the Monitor view bullet in the Frontend section)
- Modify: `docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md` (§7, §9)

**Interfaces:** none — prose only, keep each edit to the sentence(s) shown.

- [ ] **Step 1: CLAUDE.md**

In the Monitor view bullet, change

```
minimap reconstruction (icon sample / live tracking dot / historical replay trails)
```

to

```
minimap reconstruction (icon sample / live tracking dot / historical replay trails / WR ghost dots — grey, the current WR pulsing)
```

- [ ] **Step 2: Parent spec**

§7 (`## 7. pbenguin client`): append one line to the section:

```
Implemented by Plan 4 (2026-07-18-wr-client-dots-design.md): wr_trails ride the
sync_course_reads payload behind a wrMode gate; the WR renders as one more grey player
(current = its pulsing PB under the player-PB band, historic = its ghosts under player
past runs; two-tier alive/abandoned hierarchy).
```

§9's plan list, item 4: change `4. **Client display** — §7. Depends on ...` to note the
status, e.g. append `**DONE** — see 2026-07-18-wr-client-dots-design.md.` after the
existing sentence (mirror how items 1-3 carry their status).

- [ ] **Step 3: Verify docs claims + commit**

Re-read both edited sections and check every claim against the shipped code (file
names, band wording, default). Then:

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md
git commit -m "docs(wr): sync CLAUDE.md + parent spec to the shipped WR client dots

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Final gate (controller, not a task)

1. Full suites: `cd src-tauri && cargo test` (120 + 1 ignored, zero warnings both
   profiles), root `npm run test:js` (173), `npm run check`, `npm run build`,
   `cd pi && npx vitest run` (610 — must be untouched by this plan).
2. Fixture: `cd src-tauri && cargo test wr::engine::tests::fixture -- --ignored --nocapture`
   → PASS unrelaxed (~77s; guards cross-module breakage even though Plan 4 avoids the
   service path).
3. Mutations (restore tree after each): (a) flip `bandOf`'s tier addend (`abandoned ? 0 : 4`
   → `abandoned ? 4 : 0`) → the two-tier test fails; (b) drop the `is_current` filter in
   the `"current"` branch → the mode-selection test fails; (c) revert `EMPTY_COURSE_READS`
   → the shape test fails.
4. Scratch-Pi E2E smoke (spec §6): seed a scratch Pi (fresh DB + `seed_courses` + real
   scrape; pin the target job via `attempts=5` on the others, NEVER row deletion — boot
   `seedWrJobs` re-enqueues), produce the Mario Circuit trail via the temporary
   `#[ignore]` `service::process_one` test from the fix-wave ledger
   (`progress-wr-fix-wave.bak.md`, E2E SMOKE entry), then point the app at the scratch
   Pi and replay `temp/wr_mario_circuit.mp4` through the LIVE app: the grey pulsing dot
   must shadow the live tracked marker for the whole race (the video IS the current WR).
5. Paul's browser eyeball: Trails tab row renders in-idiom; dot visible in a real race.
