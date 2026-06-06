# Run Review — Phase C (live-state) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user completes a *just-finished* run in the review popup, correct the engine's live selection state so a retry inherits the values they confirmed (detection may still be failing).

**Architecture:** A new engine inbound command `set_selection {course?, character?, kart?, costume?}` mutates `SelectionTracker.state` (value + conf=1.0 per provided field). The main loop already re-emits `selection_update` when `tracker.state` changes and does not overwrite it off the selection screens, so no extra emit is needed engine-side. The frontend tags review-queue entries as `live` (from `run_needs_review`) vs not (resurfaced via `sync_list_pending`) and, on submit of a `live` entry, sends `set_selection` with the confirmed fields. Resurfaced runs never set live state (you are not about to race that course).

**Tech Stack:** Python engine (stdio IPC), Svelte 4 + Tauri v2 (`send()` → engine stdin), pytest.

---

## Background the engineer needs

**This is the final phase of the run-review feature.** Phases A/B (PB cache, gating, popup, per-lap data, `option_lists`) are merged on `main`.

**`set_selection` runs in the main loop's command drain** (`_handle_ipc_command`, `mkw_tracker/main.py`), which is dispatched before `selection = tracker.update(...)` each frame. `tracker.update()` returns `self.state` and only rescans on the selection screens (CHARACTER_SELECT/KART_SELECT/COURSE_SELECT), so on POST_TIME_TRIAL / a retry the values we set persist. The loop's on-change block (`main.py:~1184`) then emits `selection_update` automatically because the `(character, costume, kart, course)` tuple changed. **So `set_selection` only needs to mutate `tracker.state`** — do not emit from the command.

**`SelectionState`** (`mkw_tracker/detection/selection.py`) fields: `character/character_conf`, `costume/costume_conf`, `kart/kart_conf`, `course/course_conf`. Setting `f"{field}_conf" = 1.0` marks the field as manually confirmed so the rail shows it confidently.

**Frontend `send()`** (`src/lib/ipc.js`) writes `JSON.stringify(msg)` to the engine's stdin via `invoke("send_to_tracker", …)`. The Python side reads `msg.get("course")` etc. — so the keys are plain snake_case single words (`course`, `character`, `kart`, `costume`); the Tauri camelCase rule does NOT apply here (that only applies to `#[tauri::command]` arg names, and `send_to_tracker`'s arg is already `message`). `send` is already imported in `App.svelte`.

**Queue entries** are currently `{ attemptId, run, isPb }`. This phase adds a `live` boolean: `true` for entries from the `run_needs_review` event (a run that just finished this session), `false` for entries seeded on launch from `sync_list_pending` (previous-session runs). Only `live` submits set engine state. Discard never sets state.

**Why retries are safe:** in time trials a retry reuses the same course/character/kart, so even if several live runs queue up, setting state from any of them yields the same selection; changing selection requires the selection screens, where detection re-runs and overrides anyway.

## File Structure

- **Engine** `mkw_tracker/main.py` — add a `set_selection` branch to `_handle_ipc_command`.
- **Engine test** `tests/test_set_selection.py` (new).
- **Frontend** `src/App.svelte` — tag queue entries with `live`; send `set_selection` on submit of a live entry.

---

### Task 1: Engine `set_selection` command

**Files:**
- Modify: `mkw_tracker/main.py` (`_handle_ipc_command`, after the `get_state` branch ~line 172)
- Test: `tests/test_set_selection.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_set_selection.py`:

```python
from unittest.mock import MagicMock

from mkw_tracker.main import _handle_ipc_command
from mkw_tracker.detection.selection import SelectionTracker, SelectionState


def _tracker(state=None):
    # __new__ skips __init__ (no template/image load); we only need .state.
    t = SelectionTracker.__new__(SelectionTracker)
    t.state = state or SelectionState()
    return t


def _dispatch(msg, tracker):
    # _handle_ipc_command only touches `tracker` for set_selection; the rest are stubs.
    _handle_ipc_command(msg, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                        MagicMock(), [True], None, [None], [False], tracker)


def test_set_selection_sets_all_fields_and_confidence():
    t = _tracker()
    _dispatch({"type": "set_selection", "course": "Rainbow Road",
               "character": "Mario", "kart": "Pipe Frame", "costume": "Aero"}, t)
    assert t.state.course == "Rainbow Road"
    assert t.state.character == "Mario"
    assert t.state.kart == "Pipe Frame"
    assert t.state.costume == "Aero"
    assert t.state.course_conf == 1.0
    assert t.state.character_conf == 1.0
    assert t.state.kart_conf == 1.0
    assert t.state.costume_conf == 1.0


def test_set_selection_ignores_missing_and_null_fields():
    t = _tracker(SelectionState(character="Luigi", character_conf=0.5))
    _dispatch({"type": "set_selection", "course": "DK Pass", "character": None}, t)
    assert t.state.course == "DK Pass"          # set
    assert t.state.character == "Luigi"         # null in msg -> left unchanged
    assert t.state.character_conf == 0.5
    assert t.state.kart is None                 # absent in msg -> untouched


def test_set_selection_no_tracker_is_noop():
    # Must not raise when tracker is None (e.g. very early startup).
    _dispatch({"type": "set_selection", "course": "X"}, None)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_set_selection.py -q`
Expected: FAIL — the command is unhandled, so `t.state.course` stays `None` (first two tests fail on the assertions; the third passes vacuously).

- [ ] **Step 3: Add the `set_selection` branch**

In `mkw_tracker/main.py`, in `_handle_ipc_command`, immediately AFTER the `get_state` branch's `ipc.emit(emit_state(state_dict))` line (~line 172) and BEFORE `elif t == "force_screen":`, insert:

```python
    elif t == "set_selection":
        # Phase C: correct the engine's live selection from a just-finished review
        # popup, so a retry inherits the user-confirmed values. Mutate state only -
        # the main loop re-emits selection_update on the next frame (it does not
        # overwrite this off the selection screens). conf=1.0 = manually confirmed.
        if tracker is not None:
            for field in ("course", "character", "kart", "costume"):
                if msg.get(field) is not None:
                    setattr(tracker.state, field, msg[field])
                    setattr(tracker.state, f"{field}_conf", 1.0)
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `python -m pytest tests/test_set_selection.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full engine suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (prior green count + 3).

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/main.py tests/test_set_selection.py
git commit -m "$(cat <<'EOF'
feat(run-review): set_selection command to correct live state from the popup

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Frontend — send `set_selection` on a just-finished submit

**Files:**
- Modify: `src/App.svelte`

- [ ] **Step 1: Tag live queue entries (`run_needs_review`)**

In `handleMsg`, the `case "run_needs_review":` enqueue currently pushes `{ attemptId, run, isPb }`. Add `live: true`:

```js
        reviewQueue = [
          ...reviewQueue.filter((e) => e.attemptId !== msg.attempt_id),
          { attemptId: msg.attempt_id, run: msg.run, isPb: !!msg.is_pb, live: true },
        ];
```

- [ ] **Step 2: Tag resurfaced entries as not-live (`sync_list_pending`)**

In `onMount`, the resurface block maps `pending`. Add `live: false`:

```js
        reviewQueue = [
          ...reviewQueue,
          ...pending.map((p) => ({ attemptId: p.attempt_id, run: p.run, isPb: !!p.is_pb, live: false })),
        ];
```

- [ ] **Step 3: Send `set_selection` on submit of a live entry**

Replace `onReviewSubmit` with:

```js
  function onReviewSubmit(e) {
    const { attempt_id, ...filled } = e.detail;   // attempt_id travels separately
    const entry = reviewQueue.find((x) => x.attemptId === attempt_id);
    invoke("sync_resolve_pending", { attemptId: attempt_id, filled }).catch(() => {});
    // For a just-finished run (not a resurfaced one), correct the engine's live
    // selection state so a retry inherits the values the user just confirmed.
    if (entry?.live) {
      send({ type: "set_selection", course: filled.course, character: filled.character,
             kart: filled.kart, costume: filled.costume });
    }
    pushLog(`[review] submitted ${attempt_id}`);
    _dequeue(attempt_id);
  }
```

(`send` is already imported from `./lib/ipc.js`. `onReviewDiscard` is unchanged — discarding never sets live state.)

- [ ] **Step 4: svelte-check**

Run: `npm run check`
Expected: 0 errors, 0 warnings.

- [ ] **Step 5: Build (smoke)**

Run: `npm run build`
Expected: clean build.

- [ ] **Step 6: Commit**

```bash
git add src/App.svelte
git commit -m "$(cat <<'EOF'
feat(run-review): set engine live selection from a just-finished popup submit

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification (controller, after both tasks)

- [ ] Engine suite: `python -m pytest tests/ -q` → green (baseline + 3).
- [ ] `npm run check` → 0/0.
- [ ] `npm run build` → clean.
- [ ] (Rust/server untouched this phase — no need to re-run, but `cargo test` is harmless.)
- [ ] Use superpowers:finishing-a-development-branch to merge.

## Manual smoke (user, post-merge — needs app restart so the engine reloads)

1. Finish a run where the engine mis-detected (or missed) the character. The popup appears; correct the character and Submit. The monitor rail's Character readout should update to the corrected value (the loop re-emits `selection_update`).
2. Retry the same course (Switch "retry", no re-selection). Finish it: the engine should now report the corrected character in the new run (no popup if everything else is present).
3. Quit with a held run, relaunch, and submit the resurfaced popup → the rail does NOT change (resurfaced runs are not live).

## Self-review notes (already applied)

- **Spec coverage:** `set_selection` command (Task 1) + submit→set_selection for live runs only (Task 2) = the spec's "Phase C — live-state" in full.
- **Mutate-only:** relies on the loop's existing `selection_update` on-change emit; no duplicate emit, no new IPC event.
- **Null/absent handling:** `msg.get(field) is not None` sets only provided fields; `0`-style edge cases don't apply (these are name strings).
- **Live vs resurfaced:** the `live` flag is the single gate; discard never sets state.
- **Key casing:** `set_selection` goes over engine stdin (snake_case keys), not a Tauri command — no camelCase needed.
