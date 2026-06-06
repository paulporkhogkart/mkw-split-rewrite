# Run Review — Phase B3 (frontend wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface held (incomplete) runs in a review popup the user fills in, then release them to the upload outbox — completing the run-review feature end to end.

**Architecture:** The Rust gating (B2) already routes incomplete runs to outbox `status='pending_review'` and emits a `run_needs_review` tracker-event; complete runs go `ready` and drain. This phase wires the frontend: App.svelte keeps a review queue (fed by `run_needs_review` live + `sync_list_pending` on launch), renders `RunReviewModal` for the head, and on submit/discard calls `sync_resolve_pending` / `sync_discard_pending`. The popup gains per-lap **coins + mushrooms** inputs for PB runs (pure validation logic extracted to a tested `src/lib/runReview.js`). The engine emits a canonical `option_lists` event so the popup dropdowns always contain the detected value. Rust persists `is_pb` on the held row so resurfaced runs know whether to require per-lap data.

**Tech Stack:** Svelte 4 + Vite, Tauri v2 (`invoke`), rusqlite, Python engine (stdio IPC), vitest (frontend pure-lib tests), pytest (engine), `cargo test` (Rust).

---

## Background the engineer needs

**Tauri invoke key casing (critical):** Tauri v2 commands default to `rename_all = "camelCase"`, so a Rust param `attempt_id: String` is invoked from JS with key `attemptId`. `src/lib/sync.js` documents this; getting it wrong silently rejects the whole command. The relevant commands (already registered in `src-tauri/src/lib.rs`):
- `sync_resolve_pending(attempt_id: String, filled: serde_json::Value)` → JS: `invoke("sync_resolve_pending", { attemptId, filled })`
- `sync_discard_pending(attempt_id: String)` → JS: `invoke("sync_discard_pending", { attemptId })`
- `sync_list_pending() -> String` (JSON string) → JS: `await invoke("sync_list_pending")` then `JSON.parse`.

**The `run_needs_review` event (emitted by `sync.rs::route_line`, forwarded as a `tracker-event` in `lib.rs`):**
```json
{ "type": "run_needs_review", "attempt_id": "…", "is_pb": false, "missing": ["kart"], "run": { …run fields, no `type`… } }
```
Note `is_pb` / `attempt_id` are **snake_case** here (hand-built `serde_json::json!`, not a command return). The modal prop is `isPb` (camelCase) — map when enqueuing. The embedded `run` carries: `attempt_id, course, status, character, kart, costume, total_time, total_laps, started_at, ended_at, points, laps[{lap, time_ms, time_str, coins, shrooms}]`.

**Server lap ingest requires `time_ms`** (`pi/src/db/ingest.ts:40` inserts `lap.time_ms` directly — no derive-from-string fallback for laps). So when the popup rebuilds the laps array on submit it MUST include `time_ms` (derive it from the edited `time_str`, which is lossless for `M:SS.mmm`). The current modal's `submit()` drops `time_ms` — this plan fixes that.

**`resolve_in_outbox` (sync.rs) merges top-level keys** of `filled` into the stored body, replacing whole values (so a `laps` array in `filled` replaces the stored array). It then flips `status='ready'`; it does NOT re-validate completeness — the popup's submit gate is the guarantee.

**Modal options come from the engine, not the hardcoded `ASSET_ITEMS`.** The detector emits selection names that are template-dict keys (filename → `_`→space → `.title()`, costumes canonicalized to `KNOWN_COSTUMES`). Building the dropdown options from those exact keys guarantees a detected value is always selectable and is language-correct. Hence the new `option_lists` emit.

**Out of scope (Phase C):** `set_selection` engine command + setting the engine's live selection state from a just-finished popup submit. Do NOT implement live-state in B3.

## File Structure

- **Engine** `mkw_tracker/detection/selection.py` — add `SelectionTracker.option_lists()` (canonical names per category).
- **Engine** `mkw_tracker/ipc/protocol.py` — add `emit_option_lists(...)`.
- **Engine** `mkw_tracker/main.py` — emit `option_lists` after `ready` and on `switch2_language` reload.
- **Engine test** `tests/test_option_lists.py` (new).
- **Rust** `src-tauri/src/sync.rs` — persist `is_pb` on the held row; return it from `sync_list_pending`. Tests inline in `#[cfg(test)] mod tests`.
- **Frontend lib** `src/lib/runReview.js` (new) — pure time/int validators + `parseTimeMs` + `buildLaps`.
- **Frontend test** `src/lib/runReview.test.js` (new) — vitest, mirrors `src/lib/discordFormat.test.js`.
- **Frontend** `src/components/RunReviewModal.svelte` — per-lap coins/mushrooms grid; use lib helpers; send `time_ms`; ensure `Base` costume option.
- **Frontend** `src/App.svelte` — review-queue state, `run_needs_review` + `option_lists` handlers, modal render, submit/discard invokes, launch resurface.

---

### Task 1: Engine `option_lists` — canonical dropdown options

**Files:**
- Modify: `mkw_tracker/detection/selection.py` (add method near the `score_maps` property, ~line 248)
- Modify: `mkw_tracker/ipc/protocol.py` (add emit near the other `emit_*`, ~line 135)
- Modify: `mkw_tracker/main.py` (after the `emit_ready` block ~line 904; in the `switch2_language` branch ~line 135)
- Test: `tests/test_option_lists.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_option_lists.py`:

```python
import json

from mkw_tracker.detection.selection import SelectionTracker
from mkw_tracker.ipc.protocol import emit_option_lists


def test_option_lists_returns_sorted_unique_keys():
    # __new__ skips __init__ so the test never depends on which template PNGs exist.
    t = SelectionTracker.__new__(SelectionTracker)
    t._char_templates    = {"Mario": [], "Luigi": []}
    t._kart_templates    = {"Pipe Frame": []}
    t._course_templates  = {"Rainbow Road": [], "Mario Circuit": []}
    t._costume_templates = {"Aero": []}
    assert t.option_lists() == {
        "characters": ["Luigi", "Mario"],
        "karts":      ["Pipe Frame"],
        "courses":    ["Mario Circuit", "Rainbow Road"],
        "costumes":   ["Aero"],
    }


def test_emit_option_lists_shape():
    msg = json.loads(emit_option_lists(
        characters=["Mario"], karts=["K"], courses=["RR"], costumes=["Base"]))
    assert msg["type"] == "option_lists"
    assert msg["characters"] == ["Mario"]
    assert msg["karts"] == ["K"]
    assert msg["courses"] == ["RR"]
    assert msg["costumes"] == ["Base"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_option_lists.py -q`
Expected: FAIL — `AttributeError: 'SelectionTracker' object has no attribute 'option_lists'` and `ImportError: cannot import name 'emit_option_lists'`.

- [ ] **Step 3: Add `emit_option_lists` to `mkw_tracker/ipc/protocol.py`**

Insert after `emit_pb_splits` (~line 135):

```python
def emit_option_lists(characters: List[str], karts: List[str],
                      courses: List[str], costumes: List[str]) -> str:
    """Canonical selection names per category for the run-review popup dropdowns.
    These are the exact names the detector emits in ``selection_update`` (template
    keys), so a detected value is always present as an option."""
    return _emit("option_lists", characters=characters, karts=karts,
                 courses=courses, costumes=costumes)
```

- [ ] **Step 4: Add `option_lists()` to `SelectionTracker` in `mkw_tracker/detection/selection.py`**

Insert immediately after the `score_maps` property (after its `return {...}`, ~line 270):

```python
    # ------------------------------------------------------------------
    def option_lists(self) -> dict:
        """Sorted canonical names per category, as emitted in ``selection_update``.

        Built from the loaded template-dict keys (costumes already canonicalized to
        KNOWN_COSTUMES names) so the run-review popup's dropdowns always contain the
        value the detector reported, in the active Switch language.
        """
        return {
            "characters": sorted(self._char_templates.keys()),
            "karts":      sorted(self._kart_templates.keys()),
            "courses":    sorted(self._course_templates.keys()),
            "costumes":   sorted(self._costume_templates.keys()),
        }
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `python -m pytest tests/test_option_lists.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Emit `option_lists` from `main.py` (after `ready`)**

In `mkw_tracker/main.py`, add `emit_option_lists` to the existing protocol import (the `from .ipc.protocol import (...)` block near line 52), then immediately after the `ipc.emit(emit_ready(...))` call (the block ending ~line 904) add:

```python
    ipc.emit(emit_option_lists(**tracker.option_lists()))
```

- [ ] **Step 7: Re-emit `option_lists` on language change**

In `mkw_tracker/main.py`, inside `_handle_ipc_command`, in the `if key == "switch2_language":` branch (~line 131-137), after the `tracker.reload_language(_lang)` line, add (keep the existing `if tracker is not None:` guard — fold the emit into it):

```python
            if tracker is not None:
                tracker.reload_language(_lang)
                ipc.emit(emit_option_lists(**tracker.option_lists()))
```

- [ ] **Step 8: Run the full engine suite**

Run: `python -m pytest -q`
Expected: PASS (previous green count + 2).

- [ ] **Step 9: Commit**

```bash
git add mkw_tracker/detection/selection.py mkw_tracker/ipc/protocol.py mkw_tracker/main.py tests/test_option_lists.py
git commit -m "$(cat <<'EOF'
feat(run-review): engine emits canonical option_lists for popup dropdowns

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Rust — persist `is_pb` on the held row, return it from `sync_list_pending`

**Why:** A resurfaced (previous-session) held run must know whether it was a PB to require the per-lap grid. The PB cache is seeded asynchronously after launch, so recomputing `is_pb` at list time is racy and would re-trigger the optimistic cache-lowering. Persisting the value computed at route time is correct and race-free.

**Files:**
- Modify: `src-tauri/src/sync.rs`
- Test: inline `#[cfg(test)] mod tests` in the same file

- [ ] **Step 1: Write the failing test**

Add to `mod tests` in `src-tauri/src/sync.rs`:

```rust
    #[test]
    fn list_pending_includes_persisted_is_pb() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        // A held PB and a held non-PB.
        outbox_insert(&conn, "pb1",
            r#"{"type":"run_finalized","attempt_id":"pb1","course":"Rainbow Road","status":"finished"}"#,
            "pending_review", true);
        outbox_insert(&conn, "np1",
            r#"{"type":"run_finalized","attempt_id":"np1","course":"Mario Circuit","status":"reset"}"#,
            "pending_review", false);
        let rows = outbox_list_pending(&conn);
        let pb = rows.iter().find(|(id, _, _)| id == "pb1").unwrap();
        let np = rows.iter().find(|(id, _, _)| id == "np1").unwrap();
        assert_eq!(pb.2, true);
        assert_eq!(np.2, false);
    }
```

- [ ] **Step 2: Run it to confirm it fails**

Run (from `src-tauri/`): `cargo test sync::`
Expected: FAIL — `outbox_insert` takes 4 args not 5; `outbox_list_pending` rows are 2-tuples not 3-tuples.

- [ ] **Step 3: Add the `is_pb` column**

In `ensure_outbox` (~line 65), add the column to the `CREATE TABLE` and an idempotent `ALTER` (mirroring the existing `status` migration):

```rust
fn ensure_outbox(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outbox (
            attempt_id TEXT PRIMARY KEY,
            body       TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'ready',
            is_pb      INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )",
        [],
    ).expect("create outbox");
    // Migrate pre-existing tables. Errors if the column already exists — ignored.
    conn.execute("ALTER TABLE outbox ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'", []).ok();
    conn.execute("ALTER TABLE outbox ADD COLUMN is_pb INTEGER NOT NULL DEFAULT 0", []).ok();
}
```

- [ ] **Step 4: Thread `is_pb` through insert + list**

Change `outbox_insert` (~line 80) to take and store `is_pb`:

```rust
fn outbox_insert(conn: &Connection, attempt_id: &str, body: &str, status: &str, is_pb: bool) {
    conn.execute(
        "INSERT OR REPLACE INTO outbox(attempt_id, body, status, is_pb) VALUES (?1, ?2, ?3, ?4)",
        rusqlite::params![attempt_id, body, status, is_pb as i64],
    ).ok();
}
```

Change `outbox_list_pending` (~line 95) to return `(id, body, is_pb)`:

```rust
fn outbox_list_pending(conn: &Connection) -> Vec<(String, String, bool)> {
    let mut stmt = conn.prepare(
        "SELECT attempt_id, body, is_pb FROM outbox WHERE status='pending_review' ORDER BY created_at"
    ).unwrap();
    let rows = stmt.query_map([], |r| Ok((
        r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)? != 0,
    ))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}
```

- [ ] **Step 5: Update `route_line` to pass `is_pb` to both insert calls**

In `route_line` (~line 230 and ~line 239), update both `outbox_insert` calls:

```rust
    if missing.is_empty() {
        outbox_insert(conn, &id, line, "ready", is_pb);
        …
    } else {
        outbox_insert(conn, &id, line, "pending_review", is_pb);
        …
    }
```

- [ ] **Step 6: Include `is_pb` in the `sync_list_pending` JSON**

In `sync_list_pending` (~line 354), the closure now destructures three fields and adds `is_pb`:

```rust
#[tauri::command]
pub fn sync_list_pending() -> String {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() {
            let arr: Vec<serde_json::Value> = outbox_list_pending(conn).iter().filter_map(|(id, body, is_pb)| {
                let mut v: serde_json::Value = serde_json::from_str(body).ok()?;
                if let Some(o) = v.as_object_mut() { o.remove("type"); }
                Some(serde_json::json!({ "attempt_id": id, "run": v, "is_pb": is_pb }))
            }).collect();
            return serde_json::to_string(&arr).unwrap_or_else(|_| "[]".into());
        }
    }
    "[]".into()
}
```

- [ ] **Step 7: Fix the other `outbox_insert` / `outbox_list_pending` call sites in existing tests**

The existing tests call `outbox_insert(&conn, "a1", LINE, "ready")` (4 args) and treat `outbox_list_pending` rows as 2-tuples. Update each to the new signatures. Affected tests: `outbox_is_idempotent_by_attempt_id`, `route_line_complete_pb_goes_ready_with_pb_achieved`, `route_line_incomplete_goes_pending_review`, `outbox_status_routes_ready_vs_pending`, `outbox_update_ready_merges_body_and_flips_status`, `resolve_merges_filled_fields_and_releases`.

- Append `, false` (or `, true` where a PB is intended) to every `outbox_insert(...)` call in tests.
- In `route_line_incomplete_goes_pending_review` and `outbox_status_routes_ready_vs_pending`, the `outbox_list_pending(...)` results are mapped/indexed as 2-tuples — change `.map(|(id, _)| id)` to `.map(|(id, _, _)| id)`.

Example (idempotency test):

```rust
    #[test]
    fn outbox_is_idempotent_by_attempt_id() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "a1", LINE, "ready", false);
        outbox_insert(&conn, "a1", LINE, "ready", false);
        assert_eq!(outbox_pending(&conn).len(), 1);
        outbox_delete(&conn, "a1");
        assert_eq!(outbox_pending(&conn).len(), 0);
    }
```

Example (status-routing test, note the 3-tuple destructure):

```rust
    #[test]
    fn outbox_status_routes_ready_vs_pending() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "a1", LINE, "ready", false);
        outbox_insert(&conn, "a2", LINE, "pending_review", false);
        let pend: Vec<_> = outbox_pending(&conn).into_iter().map(|(id, _)| id).collect();
        assert_eq!(pend, vec!["a1"]);
        let review: Vec<_> = outbox_list_pending(&conn).into_iter().map(|(id, _, _)| id).collect();
        assert_eq!(review, vec!["a2"]);
    }
```

(`outbox_pending` is unchanged — it stays a 2-tuple `(id, body)`.)

- [ ] **Step 8: Run the Rust tests**

Run (from `src-tauri/`): `cargo test sync::`
Expected: PASS (previous count + 1 = 18).

- [ ] **Step 9: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "$(cat <<'EOF'
feat(run-review): persist is_pb on held runs so resurfaced ones gate correctly

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Frontend lib — `src/lib/runReview.js` pure helpers + vitest

**Files:**
- Create: `src/lib/runReview.js`
- Test: `src/lib/runReview.test.js` (new; mirror `src/lib/discordFormat.test.js`)

- [ ] **Step 1: Write the failing test**

Create `src/lib/runReview.test.js`:

```js
import { describe, it, expect } from "vitest";
import { isValidTime, parseTimeMs, isValidInt, isValidCount, buildLaps } from "./runReview.js";

describe("runReview validators", () => {
  it("validates M:SS.mmm time strings", () => {
    expect(isValidTime("1:50.123")).toBe(true);
    expect(isValidTime("0:36.400")).toBe(true);
    expect(isValidTime(" 1:50.123 ")).toBe(true);
    expect(isValidTime("1:50")).toBe(false);
    expect(isValidTime("")).toBe(false);
    expect(isValidTime(null)).toBe(false);
  });

  it("parses time to ms (lossless, mirrors server timeToMs)", () => {
    expect(parseTimeMs("1:50.123")).toBe(110123);
    expect(parseTimeMs("0:36.400")).toBe(36400);
    expect(parseTimeMs("nope")).toBe(null);
  });

  it("coins accept any integer incl. negative and zero", () => {
    expect(isValidInt("0")).toBe(true);
    expect(isValidInt("-1")).toBe(true);
    expect(isValidInt("12")).toBe(true);
    expect(isValidInt("")).toBe(false);
    expect(isValidInt("1.5")).toBe(false);
    expect(isValidInt("x")).toBe(false);
  });

  it("mushrooms accept non-negative integers only", () => {
    expect(isValidCount("0")).toBe(true);
    expect(isValidCount("3")).toBe(true);
    expect(isValidCount("-1")).toBe(false);
    expect(isValidCount("")).toBe(false);
  });

  it("buildLaps derives time_ms and parses coins/shrooms", () => {
    const laps = [
      { lap: 1, time: "0:30.000", coins: "5", shrooms: "2" },
      { lap: 2, time: "0:31.500", coins: "-1", shrooms: "0" },
    ];
    expect(buildLaps(laps)).toEqual([
      { lap: 1, time_str: "0:30.000", time_ms: 30000, coins: 5, shrooms: 2 },
      { lap: 2, time_str: "0:31.500", time_ms: 31500, coins: -1, shrooms: 0 },
    ]);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npm run test:js -- runReview`
Expected: FAIL — cannot resolve `./runReview.js`.

- [ ] **Step 3: Create `src/lib/runReview.js`**

```js
// Pure helpers for the run-review popup. Kept out of the .svelte component so the
// tricky bits (negative coins, 0-is-valid, time_ms derivation) are unit-testable.
// Mirrors the server's M:SS.mmm rule (pi/src/db/ingest.ts:timeToMs).

const TIME_RE = /^(\d+):(\d{2})\.(\d{3})$/;

export const isValidTime = (t) => TIME_RE.test((t ?? "").toString().trim());

export function parseTimeMs(t) {
  const m = TIME_RE.exec((t ?? "").toString().trim());
  return m ? Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3]) : null;
}

// Coins are a signed delta — any integer (incl. negative, incl. 0) is valid.
export const isValidInt = (s) => /^-?\d+$/.test((s ?? "").toString().trim());

// Mushrooms used per lap — a non-negative integer.
export const isValidCount = (s) => /^\d+$/.test((s ?? "").toString().trim());

// A lap row is complete when every field validates.
export const lapComplete = (l) =>
  isValidTime(l.time) && isValidInt(l.coins) && isValidCount(l.shrooms);

// Turn the popup's working lap rows ({lap, time, coins, shrooms} as strings) into
// the upload shape. time_ms is derived from the edited string so the server (which
// stores lap.time_ms directly) always gets it.
export function buildLaps(laps) {
  return (laps ?? []).map((l) => ({
    lap: l.lap,
    time_str: l.time.trim(),
    time_ms: parseTimeMs(l.time),
    coins: parseInt(l.coins, 10),
    shrooms: parseInt(l.shrooms, 10),
  }));
}
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `npm run test:js -- runReview`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/runReview.js src/lib/runReview.test.js
git commit -m "$(cat <<'EOF'
feat(run-review): runReview.js pure validators + buildLaps (vitest)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: RunReviewModal — per-lap coins + mushrooms grid

**Files:**
- Modify: `src/components/RunReviewModal.svelte`

Use the lib helpers from Task 3, extend the working copy and lap rows with coins + mushrooms, send `time_ms` on submit, and guarantee a `Base` costume option.

- [ ] **Step 1: Swap the inline validators for the lib import**

Replace the script's local `TIME_RE`/`validTime` (lines ~28-29) and the snd import region so the imports read:

```js
  import { createEventDispatcher, onMount, tick } from "svelte";
  import { fade, scale } from "svelte/transition";
  import { quintOut } from "svelte/easing";
  import snd from "../assets/run-review.wav";
  import { isValidTime, isValidInt, isValidCount, lapComplete, buildLaps } from "../lib/runReview.js";
```

Delete the now-unused local `const TIME_RE = …` and `const validTime = …` lines. (Every later `validTime(...)` call becomes `isValidTime(...)`.)

- [ ] **Step 2: Seed coins + mushrooms into the working copy**

Replace the reactive working-copy block (~lines 37-48) with:

```js
  $: if (run && run.attempt_id !== loadedId) {
    loadedId  = run.attempt_id;
    confirmingDiscard = false;       // reset the prompt when the queue advances
    course    = run.course    ?? "";
    character = run.character ?? "";
    kart      = run.kart      ?? "";
    costume   = run.costume   ?? "Base";
    totalTime = run.total_time ?? "";
    const n = run.total_laps ?? (run.laps?.length ?? 0);
    const seenT = new Map((run.laps ?? []).map((l) => [l.lap, l.time_str ?? ""]));
    const seenC = new Map((run.laps ?? []).map((l) => [l.lap, l.coins]));
    const seenS = new Map((run.laps ?? []).map((l) => [l.lap, l.shrooms]));
    // coins/shrooms are kept as strings for the inputs; 0 must render as "0", null as "".
    const numStr = (x) => (x == null ? "" : String(x));
    laps = Array.from({ length: n }, (_, i) => ({
      lap: i + 1,
      time:    seenT.get(i + 1) ?? "",
      coins:   numStr(seenC.get(i + 1)),
      shrooms: numStr(seenS.get(i + 1)),
    }));
  }
```

- [ ] **Step 3: Update the missing/canSubmit computeds + costume options**

Replace the computeds block (~lines 55-61) with:

```js
  $: missCourse  = !course;
  $: missChar    = !character;
  $: missKart    = !kart;
  $: missTotal   = needTotal && !isValidTime(totalTime);
  $: badLaps     = needSplits ? (laps ?? []).filter((l) => !lapComplete(l)).map((l) => l.lap) : [];
  $: canSubmit   = !missCourse && !missChar && !missKart && !missTotal && badLaps.length === 0;

  // Costume is optional and "Base" (no costume) must always be selectable + first.
  $: costumeOptions = ["Base", ...(options.costumes ?? []).filter((c) => c !== "Base")];
```

- [ ] **Step 4: Update `submit()` to send `time_ms` via `buildLaps`**

Replace `submit()` (~lines 63-72) with:

```js
  function submit() {
    if (!canSubmit) return;
    dispatch("submit", {
      attempt_id: run.attempt_id,
      course, character, kart, costume,
      total_time: needTotal ? totalTime.trim() : (run.total_time ?? null),
      laps: needSplits ? buildLaps(laps) : (run.laps ?? []),
    });
  }
```

- [ ] **Step 5: Point the costume `<select>` at `costumeOptions`**

Replace the costume select's `{#each}` (~line 128):

```svelte
        <select class="rv-ctrl" bind:value={costume}>
          {#each costumeOptions as c}<option value={c}>{c}</option>{/each}
        </select>
```

- [ ] **Step 6: Replace the lap rows with a Time/Coins/Mush grid**

Replace the `{#if needSplits} … {/if}` block (~lines 141-149) with a column header + per-lap 4-column rows. Each input flags itself amber when invalid:

```svelte
      {#if needSplits}
        <div class="rv-divider"></div>
        <div class="rv-laphead">
          <span class="rv-lh rv-lh-lap">Lap</span>
          <span class="rv-lh">Time</span>
          <span class="rv-lh rv-lh-num">Coins</span>
          <span class="rv-lh rv-lh-num">Mush</span>
        </div>
        {#each laps as lap (lap.lap)}
          <div class="rv-laprow">
            <span class="rv-lap-no">{lap.lap}</span>
            <input class="rv-ctrl rv-time" class:rv-ctrl-miss={!isValidTime(lap.time)}
                   bind:value={lap.time} placeholder="0:00.000" spellcheck="false" autocomplete="off" />
            <input class="rv-ctrl rv-time rv-num" class:rv-ctrl-miss={!isValidInt(lap.coins)}
                   bind:value={lap.coins} placeholder="0" inputmode="numeric" spellcheck="false" autocomplete="off" />
            <input class="rv-ctrl rv-time rv-num" class:rv-ctrl-miss={!isValidCount(lap.shrooms)}
                   bind:value={lap.shrooms} placeholder="0" inputmode="numeric" spellcheck="false" autocomplete="off" />
          </div>
        {/each}
      {/if}
```

- [ ] **Step 7: Add styles for the lap grid**

Add to the `<style>` block (after the `.rv-time` rules, ~line 243):

```css
  /* Per-lap PB grid: lap no. | time | coins | mush. Columns line up with the header. */
  .rv-laphead, .rv-laprow {
    display: grid;
    grid-template-columns: 30px 1fr 52px 52px;
    align-items: center; gap: .4rem;
  }
  .rv-laphead { margin-top: .05rem; }
  .rv-lh { font-size: .6rem; color: var(--tx-dim); text-transform: uppercase; letter-spacing: .04em; }
  .rv-lh-lap { text-align: left; }
  .rv-lh-num { text-align: right; }
  .rv-lap-no {
    font-size: .7rem; color: var(--tx-dim);
    font-variant-numeric: tabular-nums; text-align: center;
  }
  .rv-num { text-align: right; padding-right: .45rem; }
```

- [ ] **Step 8: svelte-check**

Run: `npm run check`
Expected: 0 errors, 0 warnings.

- [ ] **Step 9: Build (smoke)**

Run: `npm run build`
Expected: completes with no errors.

- [ ] **Step 10: Commit**

```bash
git add src/components/RunReviewModal.svelte
git commit -m "$(cat <<'EOF'
feat(run-review): popup PB lap rows gain coins + mushrooms inputs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

> **Controller note:** after this task, offer the user a quick Visual Companion preview of the densified lap grid before continuing (the spec flags it as worth a re-preview). This is a controller step, not a subagent step — do not block the next task on it.

---

### Task 5: App.svelte — review queue wiring

**Files:**
- Modify: `src/App.svelte`

Add the queue state + handlers, render the modal for the head, invoke resolve/discard, and resurface held runs on launch.

- [ ] **Step 1: Import the modal + add queue/options state**

Add the import alongside the other component imports (~line 21):

```js
  import RunReviewModal from "./components/RunReviewModal.svelte";
```

Add state vars near the other UI state (e.g. after the "Race HUD state" block, ~line 79):

```js
  // ── Run review queue ────────────────────────────────────────────────────────
  // Each entry: { attemptId, run, isPb }. The modal renders the head; submit/discard
  // dequeues. Fed live by `run_needs_review` and on launch by sync_list_pending.
  let reviewQueue = [];
  $: reviewHead = reviewQueue[0] ?? null;
  // Canonical dropdown options from the engine `option_lists` event.
  let optionLists = { courses: [], characters: [], karts: [], costumes: [] };
```

- [ ] **Step 2: Handle `run_needs_review` + `option_lists` in `handleMsg`**

Add two cases to the `switch (msg.type)` in `handleMsg` (e.g. after the `pb_achieved` case, ~line 799):

```js
      case "run_needs_review":
        pushLog(`[review] ${msg.run?.course ?? "?"} ${msg.run?.status ?? ""} — missing: ${(msg.missing ?? []).join(", ") || "none"}`);
        // Replace any existing entry for this attempt (idempotent), else append.
        reviewQueue = [
          ...reviewQueue.filter((e) => e.attemptId !== msg.attempt_id),
          { attemptId: msg.attempt_id, run: msg.run, isPb: !!msg.is_pb },
        ];
        break;
      case "option_lists":
        optionLists = {
          courses:    msg.courses    ?? [],
          characters: msg.characters ?? [],
          karts:      msg.karts      ?? [],
          costumes:   msg.costumes   ?? [],
        };
        break;
```

- [ ] **Step 3: Add submit/discard handlers**

Add near the other handlers (e.g. after `handleMsg`, before the Camera section ~line 821):

```js
  // ── Run review actions ──────────────────────────────────────────────────────
  function _dequeue(attemptId) {
    reviewQueue = reviewQueue.filter((e) => e.attemptId !== attemptId);
  }
  function onReviewSubmit(e) {
    const { attempt_id, ...filled } = e.detail;   // attempt_id travels separately
    invoke("sync_resolve_pending", { attemptId: attempt_id, filled }).catch(() => {});
    pushLog(`[review] submitted ${attempt_id}`);
    _dequeue(attempt_id);
  }
  function onReviewDiscard(e) {
    const { attempt_id } = e.detail;
    invoke("sync_discard_pending", { attemptId: attempt_id }).catch(() => {});
    pushLog(`[review] discarded ${attempt_id}`);
    _dequeue(attempt_id);
  }
```

- [ ] **Step 4: Resurface held runs on launch**

In `onMount`, after `initSync();` (~line 1225), add:

```js
    // Resurface any runs held for review from a previous session.
    try {
      const pending = JSON.parse(await invoke("sync_list_pending"));
      if (Array.isArray(pending) && pending.length) {
        reviewQueue = [
          ...reviewQueue,
          ...pending.map((p) => ({ attemptId: p.attempt_id, run: p.run, isPb: !!p.is_pb })),
        ];
        pushLog(`[review] ${pending.length} run(s) awaiting review from a previous session`);
      }
    } catch (_) { /* no outbox / not ready — ignore */ }
```

- [ ] **Step 5: Render the modal**

Add after the closing `</SettingsModal>` tag near the end of the markup (~line 1704), before the `<style>`:

```svelte
{#if reviewHead}
  <RunReviewModal
    run={reviewHead.run}
    isPb={reviewHead.isPb}
    options={optionLists}
    queueIndex={0}
    queueCount={reviewQueue.length}
    on:submit={onReviewSubmit}
    on:discard={onReviewDiscard}
  />
{/if}
```

(`queueIndex` is always 0 because we render the head; `queueCount` shows the "n / N" badge.)

- [ ] **Step 6: svelte-check**

Run: `npm run check`
Expected: 0 errors, 0 warnings.

- [ ] **Step 7: Build (smoke)**

Run: `npm run build`
Expected: completes with no errors.

- [ ] **Step 8: Commit**

```bash
git add src/App.svelte
git commit -m "$(cat <<'EOF'
feat(run-review): App.svelte review queue, resurface, resolve/discard wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification (controller, after all tasks)

- [ ] Engine suite: `python -m pytest -q` → green (baseline + 2).
- [ ] Rust: from `src-tauri/`, `cargo test sync::` → green (18).
- [ ] Frontend lib: `npm run test:js` → green.
- [ ] `npm run check` → 0/0.
- [ ] `npm run build` → clean.
- [ ] Dispatch a final code review over the whole B3 diff, then use superpowers:finishing-a-development-branch to merge.

## Manual smoke (user, post-merge)

1. Finish a real run missing a field (e.g. cover the kart name) → popup appears with that field flagged, sound plays, dropdowns pre-filled with detected values; fill + Submit → run uploads (`status` flips to ready, drains).
2. Reset mid-run → popup nags for course/character/kart only (no time/laps).
3. Finish a **PB** with the engine missing some per-lap coins → popup shows the Lap/Time/Coins/Mush grid with the gaps flagged; fill (0 and negative accepted) + Submit → uploads.
4. Discard run (two-step confirm) → row deleted, never uploads.
5. Quit with a held run, relaunch → popup resurfaces it (PB grid intact if it was a PB).

## Self-review notes (already applied)

- **Spec coverage:** option_lists emit (Task 1), per-lap coins/mush popup (Tasks 3-4), App queue + resurface + resolve/discard (Task 5). The `is_pb`-on-resurface gap the spec implies (a resurfaced PB must require per-lap data) is closed by Task 2.
- **time_ms:** server stores `lap.time_ms` directly, so `buildLaps` derives it from the (authoritative) edited time string — fixes the current modal dropping it.
- **camelCase invoke keys:** `attemptId` / `filled` used throughout (Task 5).
- **`0`/negative validity:** `isValidInt` (coins) accepts negatives + 0; `isValidCount` (mush) accepts 0 but not negatives; empty = missing.
- **Type consistency:** `outbox_insert` 5-arg / `outbox_list_pending` 3-tuple updated at every call site incl. existing tests (Task 2 Step 7). `outbox_pending` stays 2-tuple.
