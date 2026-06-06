# Run Review — Phase B1: per-lap run data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the engine's `run_finalized` event with per-lap data — `total_laps`, and for each lap its `time_str`, `coins` (signed change in coin count since the previous lap line), and `shrooms` (mushrooms *used* that lap) — and store `lap_time_str` on the server.

**Architecture:** A new `LapStatsTracker` accumulates per-lap stats from the existing `CoinTracker`/`MushroomTracker` state: coins are snapshotted at each lap line and diffed (signed); mushroom *uses* are the summed decrements of the mushroom count within the lap. The main loop feeds it each RACING frame and records a lap at every crossing + the finish; `RaceLifecycle._finalize_recording` merges `per_lap` into the `laps[]` payload. The server's `ingest` already stores `coins`/`shrooms`; this adds `lap_time_str`.

**Tech Stack:** Python engine (pytest); Node + node:sqlite server (vitest).

**Scope note:** This is the data layer for the run-review feature (spec `docs/superpowers/specs/2026-06-06-run-review-gated-upload-design.md`). Phase B2 (Rust gating) consumes `total_laps` + the per-lap completeness; B3 (frontend popup) consumes the per-lap fields. `0`/negative are valid per-lap values; `null` means "not captured" (missing).

---

## File structure
- `mkw_tracker/race/lapstats.py` — new `LapStatsTracker` (per-lap coins delta + mushroom-use count).
- `tests/test_lapstats.py` — unit tests for it.
- `mkw_tracker/lifecycle/race.py` — accept a `lapstats` tracker, reset it on clear, enrich the `laps[]` payload + add `total_laps`.
- `mkw_tracker/main.py` — construct `LapStatsTracker`, feed it each frame, record laps at crossings + finish, pass it to `RaceLifecycle`.
- `tests/test_run_finalized.py` — assert the enriched payload.
- `pi/src/db/types.ts` — `Lap` type gains `time_str`.
- `pi/src/db/ingest.ts` — store `lap_time_str` in `run_laps`.
- `pi/src/db/ingest.test.ts` — assert `lap_time_str` stored.

---

## Task 1: `LapStatsTracker`

**Files:**
- Create: `mkw_tracker/race/lapstats.py`
- Test: `tests/test_lapstats.py`

- [ ] **Step 1: Write the failing tests** (`tests/test_lapstats.py`)

```python
from mkw_tracker.race.lapstats import LapStatsTracker


def test_coins_are_signed_deltas_between_lap_lines():
    ls = LapStatsTracker()
    ls.record_lap(1, coin_count=5)            # 5 - 0
    ls.record_lap(2, coin_count=3)            # 3 - 5 = -2 (negative is valid)
    assert ls.per_lap[1]["coins"] == 5
    assert ls.per_lap[2]["coins"] == -2


def test_mushrooms_used_counts_decrements_within_a_lap():
    ls = LapStatsTracker()
    ls.update(3); ls.update(2); ls.update(1)  # lap 1: two uses
    ls.record_lap(1, coin_count=0)
    ls.update(1)                              # lap 2: none
    ls.record_lap(2, coin_count=0)
    ls.update(2); ls.update(1)               # lap 3: a pickup (gain, ignored) then one use
    ls.record_lap(3, coin_count=0)
    assert [ls.per_lap[i]["shrooms"] for i in (1, 2, 3)] == [2, 0, 1]


def test_unread_coins_are_none_and_do_not_move_the_baseline():
    ls = LapStatsTracker()
    ls.record_lap(1, coin_count=None)
    ls.record_lap(2, coin_count=4)           # baseline still 0 → 4
    assert ls.per_lap[1]["coins"] is None
    assert ls.per_lap[2]["coins"] == 4


def test_record_lap_is_idempotent_per_lap():
    ls = LapStatsTracker()
    ls.record_lap(1, coin_count=5)
    ls.record_lap(1, coin_count=9)           # second call for lap 1 is ignored
    assert ls.per_lap[1]["coins"] == 5


def test_reset_clears_everything():
    ls = LapStatsTracker()
    ls.update(2); ls.record_lap(1, coin_count=7)
    ls.reset()
    assert ls.per_lap == {}
    ls.record_lap(1, coin_count=3)
    assert ls.per_lap[1]["coins"] == 3       # baseline reset to 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_lapstats.py -q`
Expected: FAIL — `No module named 'mkw_tracker.race.lapstats'`.

- [ ] **Step 3: Implement `LapStatsTracker`** (`mkw_tracker/race/lapstats.py`)

```python
"""LapStatsTracker - per-lap coins (signed delta of the coin count between lap lines)
and mushrooms used (count of mushroom-count decrements within the lap)."""
from typing import Optional


class LapStatsTracker:
    def __init__(self):
        self.per_lap: dict = {}          # {lap_number: {"coins": int|None, "shrooms": int}}
        self._coin_baseline: int = 0     # coin count at the previous lap line (0 at race start)
        self._mush_used: int = 0         # mushroom uses accumulated in the current lap
        self._prev_mush: int = 0         # last seen mushroom count (to detect decrements)

    def reset(self):
        self.per_lap = {}
        self._coin_baseline = 0
        self._mush_used = 0
        self._prev_mush = 0

    def update(self, mush_count: int):
        """Each RACING frame: a drop in the mushroom count is a use (a pickup/gain is
        ignored). Triple-mushroom bursts decrement by >1, so accumulate the difference."""
        if mush_count < self._prev_mush:
            self._mush_used += self._prev_mush - mush_count
        self._prev_mush = mush_count

    def record_lap(self, lap: int, coin_count: Optional[int]):
        """At a lap crossing (and the finish) for the just-completed lap: store its coins
        (signed delta since the previous lap line; None if the count wasn't read) and the
        mushrooms used. Idempotent per lap."""
        if lap is None or lap in self.per_lap:
            return
        if coin_count is None:
            coins = None
        else:
            coins = coin_count - self._coin_baseline
            self._coin_baseline = coin_count
        self.per_lap[lap] = {"coins": coins, "shrooms": self._mush_used}
        self._mush_used = 0
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_lapstats.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/race/lapstats.py tests/test_lapstats.py
git commit -m "feat(engine): LapStatsTracker - per-lap coin deltas + mushroom-use counts"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 2: enrich the `run_finalized` payload in `RaceLifecycle`

**Files:**
- Modify: `mkw_tracker/lifecycle/race.py`
- Test: `tests/test_run_finalized.py`

- [ ] **Step 1: Write/extend the failing test** (`tests/test_run_finalized.py`)

In the existing `_lifecycle` helper, the lifecycle is built with `MagicMock()`s. `total_laps` and the per-lap stats must be real, so update the helper to inject them. Replace the helper's `laps=MagicMock()` argument and add a real `LapStatsTracker`:

```python
from mkw_tracker.race.lapstats import LapStatsTracker

def _lifecycle(ipc, total_time, splits):
    sel = MagicMock()
    sel.state.course = "Rainbow Road"
    sel.state.character = "Mario"
    sel.state.costume = "Base"
    sel.state.kart = "Standard Kart"
    ts = MagicMock(); ts.total_time = total_time; ts.splits = splits
    minimap = MagicMock(); minimap._calibrated = True
    mm_rec = MagicMock(); mm_rec.points = [(0, 1.0, 2.0, 0.9)]; mm_rec.save.return_value = None
    laps = MagicMock(); laps.state.total_laps = 3                  # real int for the payload
    lapstats = LapStatsTracker()
    lapstats.per_lap = {1: {"coins": 5, "shrooms": 2}, 2: {"coins": -1, "shrooms": 0}}
    return RaceLifecycle(
        selection=sel, laps=laps, coins=MagicMock(), ts=ts,
        finish=FinishStillDetector(), mush=MagicMock(), minimap=minimap,
        mm_rec=mm_rec, mm_player=MagicMock(), lapstats=lapstats, ipc=ipc,
    )
```

Add a new test:

```python
def test_run_finalized_includes_total_laps_and_per_lap_stats():
    ipc = _FakeIpc()
    lc = _lifecycle(ipc, total_time="1:23.456", splits={1: "0:41.000", 2: "1:23.456"})
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)
    evt = _run_finalized(ipc)
    assert evt["total_laps"] == 3
    assert evt["laps"] == [
        {"lap": 1, "time_ms": 41000, "time_str": "0:41.000", "coins": 5, "shrooms": 2},
        {"lap": 2, "time_ms": 83456, "time_str": "1:23.456", "coins": -1, "shrooms": 0},
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_run_finalized.py -q`
Expected: FAIL — `RaceLifecycle.__init__` has no `lapstats` argument (and `total_laps`/per-lap keys missing).

- [ ] **Step 3: Implement the enrichment** (`mkw_tracker/lifecycle/race.py`)

In `__init__`, add the parameter (place it next to the other trackers, e.g. after `mush`) and store it. Add to the signature:
```python
        mush:       MushroomTracker,
        lapstats=None,
```
and in the body, alongside `self._mush = mush`:
```python
        self._lapstats = lapstats
```

In `_clear_race_state`, after `self._mush.reset()`:
```python
        if self._lapstats is not None:
            self._lapstats.reset()
```

In `_finalize_recording`, replace the `laps = [...]` comprehension with the enriched build, and add `total_laps` to the emitted payload:
```python
            per_lap = self._lapstats.per_lap if self._lapstats is not None else {}
            laps = []
            for lap, txt in sorted(self._ts.splits.items()):
                stats = per_lap.get(int(lap), {})
                laps.append({
                    "lap":     int(lap),
                    "time_ms": _to_ms(txt),
                    "time_str": txt,
                    "coins":   stats.get("coins"),
                    "shrooms": stats.get("shrooms"),
                })
            self._ipc.emit(emit_run_finalized({
                "attempt_id": uuid.uuid4().hex,
                "course":     course,
                "status":     "finished" if completed else "reset",
                "character":  character,
                "kart":       sel.kart,
                "costume":    costume,
                "total_laps": self._laps.state.total_laps,
                "started_at": self._race_started_at,
                "ended_at":   datetime.now(timezone.utc).isoformat(),
                "total_time": best_total_time,
                "laps":       laps,
                "points":     [[t, cx, cy, sc] for (t, cx, cy, sc) in self._mm_rec.points],
            }))
```
(Only the `laps = [...]` line and the `emit_run_finalized({...})` dict change — keep the surrounding `if self._ipc is not None and course:` block, the `import uuid`, the datetime/`_to_ms` imports, and the `save()` call exactly as they are.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_run_finalized.py -q`
Expected: PASS (the new test + the existing finish/started_at/dedup tests — the existing ones don't pass `lapstats`, so it defaults to `None`, and they don't assert the new keys).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/lifecycle/race.py tests/test_run_finalized.py
git commit -m "feat(engine): run_finalized carries total_laps + per-lap time_str/coins/shrooms"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 3: feed `LapStatsTracker` from the main loop

**Files:**
- Modify: `mkw_tracker/main.py`

This is an integration task (no unit test — verified by the engine suite compiling/running and by a `--video` re-run). Read the relevant region (`mkw_tracker/main.py` ~814–860 for construction, ~1059–1130 for the per-frame updates) before editing.

- [ ] **Step 1: Construct the tracker + pass it to the lifecycle**

After `mm_player = MinimapPlayer()` (the tracker construction block ~line 823) add:
```python
    lapstats  = LapStatsTracker()
```
Add the import near the other `from .race...` imports at the top of the file:
```python
from .race.lapstats import LapStatsTracker
```
In the `RaceLifecycle(...)` constructor call, add the argument (after `mush=mush,`):
```python
        lapstats=lapstats,
```

- [ ] **Step 2: Feed it each RACING frame + record laps**

In the `if not _race_complete:` block where the trackers update (right after `mush_state = mush.update(frame, screen)`), add:
```python
            lapstats.update(mush_state.count)
```

Where the timestamp burst is triggered on a lap crossing (the `if not _race_complete:` block that computes `_ts_lap` and calls `ts.update(...)`), record the just-completed lap's stats right after the `ts.update(...)` call:
```python
            if lap_inc and _ts_lap is not None:
                lapstats.record_lap(_ts_lap, coin_state.coins)
```

Where the finish is handled (right after `if finish_just_detected:` `mm_player.stop()`), record the final lap (idempotent, so the multi-frame `finish_just_detected` only records once):
```python
        if finish_just_detected and lap_state.current_lap is not None:
            lapstats.record_lap(lap_state.current_lap, coin_state.coins)
```

- [ ] **Step 3: Verify the engine still imports + the suite passes**

Run: `python -m pytest tests/ -q`
Expected: PASS (139+ tests; the `tools/autotemplate` nxbt collection error is pre-existing — ignore).
Also confirm a clean import: `python -c "import mkw_tracker.main"`
Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add mkw_tracker/main.py
git commit -m "feat(engine): wire LapStatsTracker into the race loop (per-lap coins/mushrooms)"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 4: server — store `lap_time_str`

**Files:**
- Modify: `pi/src/db/types.ts`, `pi/src/db/ingest.ts`
- Test: `pi/src/db/ingest.test.ts`

- [ ] **Step 1: Write the failing test** (append to `pi/src/db/ingest.test.ts`)

```ts
it('stores lap_time_str (and coins/shrooms) on run_laps', () => {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  const runId = upsertRun(db, {
    attempt_id: 'a1', course: 'Rainbow Road', status: 'finished', total_time: '1:40.000',
    laps: [{ lap: 1, time_ms: 40000, time_str: '0:40.000', coins: 5, shrooms: 2 }],
  } as any, 1, 1);
  const row = db.prepare(
    'SELECT lap_time_str, coins, shrooms FROM run_laps WHERE run_id=? AND lap_index=1'
  ).get(runId) as any;
  expect(row.lap_time_str).toBe('0:40.000');
  expect(row.coins).toBe(5);
  expect(row.shrooms).toBe(2);
});
```
(`openDb`/`applySchema`/`upsertRun` are imported at the top of `ingest.test.ts` already; if not, add `import { openDb, applySchema } from './connect';` and `import { upsertRun } from './ingest';`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/ingest.test.ts`
Expected: FAIL — `row.lap_time_str` is `null` (the column isn't written).

- [ ] **Step 3: Add `time_str` to the `Lap` type** (`pi/src/db/types.ts`)

```ts
export type Lap = { lap: number; time_ms: number; time_str?: string | null; coins?: number | null; shrooms?: number | null };
```

- [ ] **Step 4: Store it in the lap INSERT** (`pi/src/db/ingest.ts`)

Replace the lap insert (currently inserts `lap_time_ms, coins, shrooms`) with one that includes `lap_time_str`:
```ts
    const lapStmt = db.prepare(
      'INSERT INTO run_laps(run_id, lap_index, lap_time_ms, lap_time_str, coins, shrooms) VALUES (?,?,?,?,?,?)'
    );
    for (const lap of p.laps ?? []) lapStmt.run(runId, lap.lap, lap.time_ms, lap.time_str ?? null, lap.coins ?? null, lap.shrooms ?? null);
```

- [ ] **Step 5: Run to verify it passes + full suite**

Run: `cd pi && npx vitest run src/db/ingest.test.ts` → PASS.
Then: `cd pi && npm test` → all PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add pi/src/db/types.ts pi/src/db/ingest.ts pi/src/db/ingest.test.ts
git commit -m "feat(pi): store lap_time_str on run_laps (Lap type + ingest)"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Self-review

- **Spec coverage:** `total_laps` (Task 2), per-lap `lap_time_str` (Tasks 2+4), per-lap coins as signed delta + mushrooms as use-count (Tasks 1–3), ingest stores `lap_time_str` (Task 4). `0`/negative valid, `null`=missing (Task 1 `record_lap` None-handling). Covered. `default_laps` populate is B2; `option_lists` is B3 (consumers there).
- **Type/name consistency:** `LapStatsTracker.update(mush_count)` / `record_lap(lap, coin_count)` / `per_lap[lap] = {"coins","shrooms"}` used identically in Tasks 1–3; payload keys `time_str`/`coins`/`shrooms`/`total_laps` match the server `Lap` type + ingest (Task 4). `RaceLifecycle(..., lapstats=...)` matches between race.py (Task 2) and main.py (Task 3).
- **Placeholders:** none — all code/tests/commands concrete.
