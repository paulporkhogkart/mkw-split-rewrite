# Discord Rich Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a live Discord Rich Presence card ("Mario Kart World") driven by the tracker — penguin in menus, course art while racing with reset count + live PB delta, results, and an optional Twitch button.

**Architecture:** A decoupled "plugin": a self-contained Rust module (`discord.rs`) owns the Discord IPC transport; a self-contained frontend module (`discord.js`) reads existing Svelte stores, computes the presence payload from pure functions, and calls the Rust commands. One small engine addition persists per-lap splits (needed for the live PB delta). Total footprint on existing code: a few wiring lines.

**Tech Stack:** Rust (`discord-rich-presence` crate) + Tauri commands; Svelte/Vite frontend (vitest for pure-logic tests); Python engine + SQLite (pytest).

**Spec:** `docs/superpowers/specs/2026-06-03-discord-rich-presence-design.md`

**Prerequisite (user, one-time):** Create a Discord Application named "Mario Kart World" at the Developer Portal; note its **Application ID**; upload the art assets produced in Task B1 keyed by their filenames. The Application ID is pasted in Task C2. Until set, the feature no-ops gracefully.

---

## Phase A — Engine: persist per-lap splits (Python / pytest)

Run all pytest with: `python -m pytest tests/test_pb_splits.py -v` (from repo root).

### Task A1: `replay_splits` table

**Files:**
- Modify: `mkw_tracker/database/migrations.py` (schema string)
- Test: `tests/test_pb_splits.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pb_splits.py
"""Tests for per-lap split persistence and the get_pb_splits IPC."""
import json
from unittest.mock import MagicMock

from mkw_tracker.database.connection import get_connection
from mkw_tracker.database.replay_repo import save_run, get_pb_splits


def test_replay_splits_table_exists(memdb):
    row = get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='replay_splits'"
    ).fetchone()
    assert row is not None
```

- [ ] **Step 2: Run it; verify it fails**

Run: `python -m pytest tests/test_pb_splits.py::test_replay_splits_table_exists -v`
Expected: FAIL — table missing (row is None) or import error for `get_pb_splits`.

- [ ] **Step 3: Add the table to the schema**

In `mkw_tracker/database/migrations.py`, inside the `_SCHEMA` string, after the `replay_points` table + its index, add:

```sql
CREATE TABLE IF NOT EXISTS replay_splits (
    replay_id  INTEGER NOT NULL REFERENCES replays(id) ON DELETE CASCADE,
    lap        INTEGER NOT NULL,
    split_ms   INTEGER,
    split_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_replay_splits_id ON replay_splits(replay_id);
```

(The schema is applied with `CREATE TABLE IF NOT EXISTS` on every startup, so existing DBs gain the table on next launch — no version bump needed.)

- [ ] **Step 4: Add a temporary stub so the import resolves**

In `mkw_tracker/database/replay_repo.py`, add a stub (replaced in A3) so `test_pb_splits.py` imports cleanly:

```python
def get_pb_splits(course: str, player: str = "me"):
    """Return {lap: split_ms} for the course PB, or None. (Implemented in A3.)"""
    return None
```

- [ ] **Step 5: Run it; verify it passes**

Run: `python -m pytest tests/test_pb_splits.py::test_replay_splits_table_exists -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/database/migrations.py mkw_tracker/database/replay_repo.py tests/test_pb_splits.py
git commit -m "feat(db): add replay_splits table"
```

### Task A2: `save_run` persists `lap_splits`

**Files:**
- Modify: `mkw_tracker/database/replay_repo.py` (`save_run`)
- Test: `tests/test_pb_splits.py`

- [ ] **Step 1: Write the failing test**

```python
def test_save_run_persists_lap_splits(memdb):
    rid = save_run(
        "Mario Circuit",
        [(0, 1600.0, 700.0, 0.95)],
        total_time="1:23.456",
        lap_splits={1: "0:41.000", 2: "1:23.456"},
    )
    rows = get_connection().execute(
        "SELECT lap, split_ms, split_text FROM replay_splits WHERE replay_id=? ORDER BY lap",
        (rid,),
    ).fetchall()
    assert [(r["lap"], r["split_ms"], r["split_text"]) for r in rows] == [
        (1, 41000, "0:41.000"),
        (2, 83456, "1:23.456"),
    ]
```

- [ ] **Step 2: Run it; verify it fails**

Run: `python -m pytest tests/test_pb_splits.py::test_save_run_persists_lap_splits -v`
Expected: FAIL — `save_run() got an unexpected keyword argument 'lap_splits'`.

- [ ] **Step 3: Add the param + insert**

In `save_run`, add `lap_splits: Optional[dict] = None,` to the signature (after `source`). Then, after the existing `replay_points` insert block and before `conn.commit()`, add:

```python
    if lap_splits:
        conn.executemany(
            "INSERT INTO replay_splits(replay_id, lap, split_ms, split_text) VALUES(?,?,?,?)",
            [(replay_id, int(lap), _to_ms(txt), txt) for lap, txt in sorted(lap_splits.items())],
        )
```

- [ ] **Step 4: Run it; verify it passes**

Run: `python -m pytest tests/test_pb_splits.py::test_save_run_persists_lap_splits -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/database/replay_repo.py tests/test_pb_splits.py
git commit -m "feat(db): save_run persists per-lap splits"
```

### Task A3: `get_pb_splits` repo function

**Files:**
- Modify: `mkw_tracker/database/replay_repo.py` (replace the A1 stub)
- Test: `tests/test_pb_splits.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_get_pb_splits_returns_pb_splits(memdb):
    # First run becomes the PB (no prior PB), with splits.
    save_run("Rainbow Road", [(0, 1.0, 2.0, 0.9)], total_time="2:00.000",
             lap_splits={1: "0:40.000", 2: "1:20.000", 3: "2:00.000"})
    assert get_pb_splits("Rainbow Road") == {1: 40000, 2: 80000, 3: 120000}


def test_get_pb_splits_none_when_no_pb(memdb):
    assert get_pb_splits("Rainbow Road") is None
```

- [ ] **Step 2: Run; verify they fail**

Run: `python -m pytest tests/test_pb_splits.py -k get_pb_splits -v`
Expected: FAIL — stub returns None for the first test.

- [ ] **Step 3: Implement (replace the A1 stub)**

```python
def get_pb_splits(course: str, player: str = "me"):
    """Return {lap: split_ms} for the course PB, or None if no PB / no splits."""
    conn = get_connection()
    pb = conn.execute(
        "SELECT id FROM replays WHERE player=? AND course=? AND is_pb=1",
        (player, course),
    ).fetchone()
    if pb is None:
        return None
    rows = conn.execute(
        "SELECT lap, split_ms FROM replay_splits WHERE replay_id=? AND split_ms IS NOT NULL ORDER BY lap",
        (pb["id"],),
    ).fetchall()
    if not rows:
        return None
    return {int(r["lap"]): int(r["split_ms"]) for r in rows}
```

- [ ] **Step 4: Run; verify pass**

Run: `python -m pytest tests/test_pb_splits.py -k get_pb_splits -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/database/replay_repo.py tests/test_pb_splits.py
git commit -m "feat(db): get_pb_splits returns PB lap splits"
```

### Task A4: `pb_splits` emit + `get_pb_splits` IPC command

**Files:**
- Modify: `mkw_tracker/ipc/protocol.py` (add emit)
- Modify: `mkw_tracker/main.py` (`_handle_ipc_command`, add branch)
- Test: `tests/test_pb_splits.py`

- [ ] **Step 1: Write the failing integration test**

```python
def test_dispatch_get_pb_splits(memdb):
    save_run("Mario Circuit", [(0, 1.0, 2.0, 0.9)], total_time="1:23.456",
             lap_splits={1: "0:41.000", 2: "1:23.456"})

    emitted = []

    class FakeIpc:
        def emit(self, line):
            emitted.append(json.loads(line))

    from mkw_tracker.main import _handle_ipc_command
    _handle_ipc_command(
        {"type": "get_pb_splits", "course": "Mario Circuit"}, FakeIpc(),
        detector=MagicMock(), settings=MagicMock(),
        minimap=MagicMock(), lifecycle=MagicMock(),
        show_debug=[False], cap=None,
        current_frame=[None], setup_mode=[False],
    )
    assert len(emitted) == 1
    evt = emitted[0]
    assert evt["type"] == "pb_splits"
    assert evt["course"] == "Mario Circuit"
    # JSON object keys are strings.
    assert evt["splits"] == {"1": 41000, "2": 83456}
```

- [ ] **Step 2: Run; verify it fails**

Run: `python -m pytest tests/test_pb_splits.py::test_dispatch_get_pb_splits -v`
Expected: FAIL — no `pb_splits` emitted (unknown command).

- [ ] **Step 3: Add the emit helper**

In `mkw_tracker/ipc/protocol.py`, after `emit_pb_export`:

```python
def emit_pb_splits(course: str, splits) -> str:
    return _emit("pb_splits", course=course, splits=splits)
```

- [ ] **Step 4: Add the dispatch branch**

In `mkw_tracker/main.py` `_handle_ipc_command`, after the `elif t == "get_replay_paths":` block, add:

```python
    elif t == "get_pb_splits":
        from .database.replay_repo import get_pb_splits as _get_pb_splits
        from .ipc.protocol import emit_pb_splits as _emit_pbs
        _course = msg.get("course", "")
        ipc.emit(_emit_pbs(_course, _get_pb_splits(_course)))
```

- [ ] **Step 5: Run; verify pass + full suite**

Run: `python -m pytest tests/test_pb_splits.py -v`
Expected: PASS (all). Then `python -m pytest -q` — existing 47 still green.

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/ipc/protocol.py mkw_tracker/main.py tests/test_pb_splits.py
git commit -m "feat(ipc): get_pb_splits command + pb_splits event"
```

### Task A5: thread `lap_splits` through the recorder + lifecycle

**Files:**
- Modify: `mkw_tracker/minimap/recorder.py` (`save`)
- Modify: `mkw_tracker/lifecycle/race.py:162` (the `_mm_rec.save(...)` call)
- Test: `tests/test_pb_splits.py`

- [ ] **Step 1: Write the failing test (recorder passes lap_splits through)**

```python
def test_recorder_save_forwards_lap_splits(memdb, monkeypatch):
    import mkw_tracker.minimap.recorder as rec_mod
    captured = {}

    def fake_save_run(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(rec_mod, "save_run", lambda *a, **k: fake_save_run(*a, **k) if False else fake_save_run(**k))

    rec = rec_mod.MinimapRecorder.__new__(rec_mod.MinimapRecorder)
    rec._points = [(0, 1.0, 2.0, 0.9)]
    rec._recording = False
    rec._pause_start = None

    rec.save("Mario Circuit", total_time="1:23.456",
             character="Mario", kart="Pipe Frame",
             lap_splits={1: "0:41.000"})
    assert captured.get("lap_splits") == {1: "0:41.000"}
```

- [ ] **Step 2: Run; verify it fails**

Run: `python -m pytest tests/test_pb_splits.py::test_recorder_save_forwards_lap_splits -v`
Expected: FAIL — `save() got an unexpected keyword argument 'lap_splits'`.

- [ ] **Step 3: Add param to `recorder.save` and forward it**

In `mkw_tracker/minimap/recorder.py`, add `lap_splits: Optional[dict] = None,` to `save(...)`'s signature (after `kart`), and add `lap_splits=lap_splits,` to the `save_run(...)` call.

- [ ] **Step 4: Wire the lifecycle call**

In `mkw_tracker/lifecycle/race.py`, change the `_finalize_recording` save call (currently lines ~162-163) to pass splits:

```python
        self._mm_rec.save(course, character=character, costume=costume,
                          kart=sel.kart, total_time=best_total_time,
                          lap_splits=dict(self._ts.splits))
```

- [ ] **Step 5: Run; verify pass + full suite**

Run: `python -m pytest tests/test_pb_splits.py -v && python -m pytest -q`
Expected: PASS; existing suite green.

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/minimap/recorder.py mkw_tracker/lifecycle/race.py tests/test_pb_splits.py
git commit -m "feat(engine): capture lap splits on race save"
```

---

## Phase B — Discord image assets (Python script)

### Task B1: fetch/prepare course art keyed by slug

**Files:**
- Create: `scripts/fetch_discord_assets.py`
- Create: `tests/test_discord_assets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discord_assets.py
"""The URL->slug asset map must cover exactly the 30 known course slugs,
and slugify(display name) must equal the slug for every known course."""
from scripts.fetch_discord_assets import COURSE_ASSETS, slugify
from mkw_tracker.detection.selection import KNOWN_COURSES


def test_asset_map_covers_all_known_course_slugs():
    map_slugs = {slug for (_url, slug) in COURSE_ASSETS}
    known_slugs = {slugify(name) for name in KNOWN_COURSES}
    assert map_slugs == known_slugs
    assert len(COURSE_ASSETS) == 30


def test_slugify_matches_known_courses():
    cases = {
        "Wario's Galleon": "warios_galleon",
        "Great ? Block Ruins": "great_block_ruins",
        "Mario Bros. Circuit": "mario_bros_circuit",
        "Sky-High Sundae": "sky_high_sundae",
        "DK Pass": "dk_pass",
        "Rainbow Road": "rainbow_road",
    }
    for name, slug in cases.items():
        assert slugify(name) == slug
```

- [ ] **Step 2: Run; verify it fails**

Run: `python -m pytest tests/test_discord_assets.py -v`
Expected: FAIL — module/symbols missing.

- [ ] **Step 3: Write the script**

```python
# scripts/fetch_discord_assets.py
"""Download MKW course icons + penguin/splash, named by Discord asset key (slug),
into out/ ready to drag into the Discord Developer Portal -> Art Assets.

Usage: python scripts/fetch_discord_assets.py [--out out_dir]
"""
import argparse, re, shutil, urllib.request
from pathlib import Path

def slugify(name: str) -> str:
    return re.sub(r"_+$", "", re.sub(r"^_+", "", re.sub(r"[^a-z0-9]+", "_", name.lower())))

# (url, slug) — slugs match images/courses/*.png stems and slugify(display name).
COURSE_ASSETS = [
    ("https://mario.wiki.gallery/images/thumb/8/85/MKWd_Mario_Bros_Circuit_Icon.png/120px-MKWd_Mario_Bros_Circuit_Icon.png", "mario_bros_circuit"),
    ("https://mario.wiki.gallery/images/thumb/e/ef/MKWd_Crown_City_Icon.png/120px-MKWd_Crown_City_Icon.png", "crown_city"),
    ("https://mario.wiki.gallery/images/thumb/d/db/MKWd_Whistlestop_Summit_Icon.png/120px-MKWd_Whistlestop_Summit_Icon.png", "whistlestop_summit"),
    ("https://mario.wiki.gallery/images/thumb/8/89/MKWd_DK_Spaceport_Icon.png/120px-MKWd_DK_Spaceport_Icon.png", "dk_spaceport"),
    ("https://mario.wiki.gallery/images/thumb/6/6a/MKWd_Desert_Hills_Icon.png/120px-MKWd_Desert_Hills_Icon.png", "desert_hills"),
    ("https://mario.wiki.gallery/images/thumb/3/32/MKWd_Shy_Guy_Bazaar_Icon.png/120px-MKWd_Shy_Guy_Bazaar_Icon.png", "shy_guy_bazaar"),
    ("https://mario.wiki.gallery/images/thumb/3/34/MKWd_Wario_Stadium_Icon.png/120px-MKWd_Wario_Stadium_Icon.png", "wario_stadium"),
    ("https://mario.wiki.gallery/images/thumb/c/c4/MKWd_Airship_Fortress_Icon.png/120px-MKWd_Airship_Fortress_Icon.png", "airship_fortress"),
    ("https://mario.wiki.gallery/images/thumb/e/e1/MKWd_DK_Pass_Icon.png/120px-MKWd_DK_Pass_Icon.png", "dk_pass"),
    ("https://mario.wiki.gallery/images/thumb/c/c4/MKWd_Starview_Peak_Icon.png/120px-MKWd_Starview_Peak_Icon.png", "starview_peak"),
    ("https://mario.wiki.gallery/images/thumb/8/83/MKWd_Sky-High_Sundae_Icon.png/120px-MKWd_Sky-High_Sundae_Icon.png", "sky_high_sundae"),
    ("https://mario.wiki.gallery/images/thumb/b/b4/MKWd_Wario_Shipyard_Icon.png/120px-MKWd_Wario_Shipyard_Icon.png", "warios_galleon"),
    ("https://mario.wiki.gallery/images/thumb/c/c2/MKWd_Koopa_Troopa_Beach_Icon.png/120px-MKWd_Koopa_Troopa_Beach_Icon.png", "koopa_troopa_beach"),
    ("https://mario.wiki.gallery/images/thumb/5/5d/MKWd_Faraway_Oasis_Icon.png/120px-MKWd_Faraway_Oasis_Icon.png", "faraway_oasis"),
    ("https://mario.wiki.gallery/images/thumb/e/e3/Peach-Beach-MarioKartWorld.jpg/120px-Peach-Beach-MarioKartWorld.jpg", "peach_beach"),
    ("https://mario.wiki.gallery/images/thumb/d/d8/Salty_Salty_Speedway_Mario_Kart_World.jpg/120px-Salty_Salty_Speedway_Mario_Kart_World.jpg", "salty_salty_speedway"),
    ("https://mario.wiki.gallery/images/thumb/6/67/Dino_Dino_Jungle_Mario_Kart_World.png/120px-Dino_Dino_Jungle_Mario_Kart_World.png", "dino_dino_jungle"),
    ("https://mario.wiki.gallery/images/thumb/5/5e/MKWorld_Question_Ruins_icon.png/120px-MKWorld_Question_Ruins_icon.png", "great_block_ruins"),
    ("https://mario.wiki.gallery/images/thumb/0/06/MKWorld_Cheep_Cheep_Falls_icon.png/120px-MKWorld_Cheep_Cheep_Falls_icon.png", "cheep_cheep_falls"),
    ("https://mario.wiki.gallery/images/thumb/7/70/MKWorld_Dandelion_Depths_icon.png/120px-MKWorld_Dandelion_Depths_icon.png", "dandelion_depths"),
    ("https://mario.wiki.gallery/images/thumb/0/08/MKWorld_Boo_Cinema_icon.png/120px-MKWorld_Boo_Cinema_icon.png", "boo_cinema"),
    ("https://mario.wiki.gallery/images/thumb/0/01/MKWorld_Dry_Bones_Burnout_icon.png/120px-MKWorld_Dry_Bones_Burnout_icon.png", "dry_bones_burnout"),
    ("https://mario.wiki.gallery/images/thumb/4/42/MKWorld_Moo_Moo_Meadows_icon.png/120px-MKWorld_Moo_Moo_Meadows_icon.png", "moo_moo_meadows"),
    ("https://mario.wiki.gallery/images/thumb/b/b1/MKWorld_Choco_Mountain_icon.png/120px-MKWorld_Choco_Mountain_icon.png", "choco_mountain"),
    ("https://mario.wiki.gallery/images/thumb/1/1e/MKWorld_Toads_Factory_icon.png/120px-MKWorld_Toads_Factory_icon.png", "toads_factory"),
    ("https://mario.wiki.gallery/images/thumb/8/86/MKWorld_Bowsers_Castle_icon.png/120px-MKWorld_Bowsers_Castle_icon.png", "bowsers_castle"),
    ("https://mario.wiki.gallery/images/thumb/5/5a/MKWorld_Acorn_Heights_Icon.jpg/120px-MKWorld_Acorn_Heights_Icon.jpg", "acorn_heights"),
    ("https://mario.wiki.gallery/images/thumb/f/f0/MKWorld_Mario_Circuit_icon.png/120px-MKWorld_Mario_Circuit_icon.png", "mario_circuit"),
    ("https://mario.wiki.gallery/images/thumb/7/7e/MKWorld_Peach_Stadium_icon_2.png/120px-MKWorld_Peach_Stadium_icon_2.png", "peach_stadium"),
    ("https://mario.wiki.gallery/images/thumb/8/88/MKWorld_Rainbow_Road_icon.png/120px-MKWorld_Rainbow_Road_icon.png", "rainbow_road"),
]
SPLASH_URL = "https://image-assets.m.nintendo.com/985d81ae-7d37-4bd1-8732-f247f47f8821"
PENGUIN_SRC = Path("src-tauri/icons/128x128@2x.png")

def _download(url: str, dest_stem: Path):
    ext = ".jpg" if url.lower().split("?")[0].endswith((".jpg", ".jpeg")) else ".png"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        dest_stem.with_suffix(ext).write_bytes(r.read())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/discord-assets")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    for url, slug in COURSE_ASSETS:
        print(f"  {slug}")
        _download(url, out / slug)
    _download(SPLASH_URL, out / "splash")
    if PENGUIN_SRC.exists():
        shutil.copy(PENGUIN_SRC, out / "penguin.png")
    print(f"Done -> {out} (drag these into the Discord portal Art Assets)")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run; verify pass**

Run: `python -m pytest tests/test_discord_assets.py -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_discord_assets.py tests/test_discord_assets.py
git commit -m "feat(assets): Discord art-asset fetch script + slug mapping"
```

> **Manual (user, later):** run `python scripts/fetch_discord_assets.py`, then upload everything in `out/discord-assets/` to the Discord app's Art Assets (keys = filenames).

### Task B2: Discord setup guide (for the user)

**Files:**
- Create: `docs/discord-setup.md`

- [ ] **Step 1: Write the guide**

Create `docs/discord-setup.md` with exactly this content:

````markdown
# Discord Rich Presence — Setup (one-time)

Three steps: create a Discord application, give us its ID, and upload the images.

## 1. Create the application
1. Go to https://discord.com/developers/applications and sign in.
2. Click **New Application**, name it exactly **Mario Kart World**, agree, **Create**.
3. On **General Information**, copy the **Application ID** (an ~18-19 digit number). This is the one value we need from you. It is *not* secret — no bot token or OAuth is required for Rich Presence.

## 2. Upload the art assets
1. From the repo root, run: `python scripts/fetch_discord_assets.py`
   - Downloads every course icon plus the penguin and splash into `out/discord-assets/`, named by asset key (`rainbow_road.png`, `penguin.png`, `splash.jpg`, ...).
2. In the Developer Portal: open your app → left sidebar **Rich Presence** → **Art Assets** → **Add Image(s)**.
3. Drag in **all** files from `out/discord-assets/`. Discord uses the **filename (lowercased, no extension)** as the asset key, so they line up automatically with what the app references.
4. **Save Changes.** Assets can take a few minutes to process.

## 3. Hand off the Application ID
Paste the Application ID into `src-tauri/src/discord.rs` (the `DISCORD_APP_ID` constant), or send it to your assistant. Until it's set, the feature stays silent (no errors).

## Notes
- In Discord: **User Settings → Activity Privacy → Display current activity** must be on.
- You may not see your *own* presence buttons (e.g. "Watch on Twitch") in your own client — a known Discord quirk; others see them.
- Set your Twitch URL in the app's **Settings → Discord** to enable the button.
````

- [ ] **Step 2: Commit**

```bash
git add docs/discord-setup.md
git commit -m "docs: Discord Rich Presence user setup guide"
```

---

## Phase C — Rust Discord transport

### Task C1: `discord.rs` module — payload, debounce (cargo test)

**Files:**
- Modify: `src-tauri/Cargo.toml` (dependency)
- Create: `src-tauri/src/discord.rs`

- [ ] **Step 1: Add the dependency**

In `src-tauri/Cargo.toml` under `[dependencies]`:

```toml
discord-rich-presence = "0.2"
```

- [ ] **Step 2: Write the module with a unit-testable debounce + payload**

```rust
// src-tauri/src/discord.rs
use std::sync::Mutex;
use std::time::{Duration, Instant};
use discord_rich_presence::{activity, DiscordIpc, DiscordIpcClient};

/// Replace with the "Mario Kart World" Discord Application ID (see plan prerequisite).
const DISCORD_APP_ID: &str = "REPLACE_WITH_APPLICATION_ID";
/// Minimum spacing between activity updates (Discord rate-limits ~5 / 20s).
const MIN_INTERVAL: Duration = Duration::from_millis(2500);

#[derive(Clone, Default, PartialEq, serde::Deserialize)]
pub struct Presence {
    pub details: Option<String>,
    pub state: Option<String>,
    pub large_image: Option<String>,
    pub small_image: Option<String>,
    pub button_label: Option<String>,
    pub button_url: Option<String>,
}

/// Pure debounce decision: send if enough time passed OR the payload changed.
pub fn should_send(now: Instant, last_sent: Option<Instant>, changed: bool) -> bool {
    match last_sent {
        None => true,
        Some(t) => changed || now.duration_since(t) >= MIN_INTERVAL,
    }
}

struct State {
    client: Option<DiscordIpcClient>,
    connected: bool,
    last_sent: Option<Instant>,
    last_payload: Option<Presence>,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);
```

- [ ] **Step 3: Add the cargo test for `should_send`**

Append to `src-tauri/src/discord.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn first_send_always_allowed() {
        assert!(should_send(Instant::now(), None, false));
    }
    #[test]
    fn changed_payload_sends_immediately() {
        let now = Instant::now();
        assert!(should_send(now, Some(now), true));
    }
    #[test]
    fn unchanged_payload_waits_for_interval() {
        let now = Instant::now();
        assert!(!should_send(now, Some(now), false));
        assert!(should_send(now + MIN_INTERVAL, Some(now), false));
    }
}
```

- [ ] **Step 4: Run; verify pass**

Run: `cd src-tauri && cargo test discord`
Expected: 3 tests pass. (Run `cargo test` from `src-tauri`.)

- [ ] **Step 5: Commit**

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/discord.rs
git commit -m "feat(rust): discord presence module skeleton + debounce"
```

### Task C2: commands, connection, wiring

**Files:**
- Modify: `src-tauri/src/discord.rs` (add `apply`, commands)
- Modify: `src-tauri/src/lib.rs` (register module + commands + clear on exit)

- [ ] **Step 1: Implement connect/apply/clear + the two commands**

Append to `src-tauri/src/discord.rs`:

```rust
fn ensure_state(s: &mut Option<State>) -> &mut State {
    if s.is_none() {
        *s = Some(State { client: None, connected: false, last_sent: None, last_payload: None });
    }
    s.as_mut().unwrap()
}

fn try_connect(st: &mut State) {
    if st.connected { return; }
    if st.client.is_none() {
        match DiscordIpcClient::new(DISCORD_APP_ID) {
            Ok(c) => st.client = Some(c),
            Err(e) => { log::warn!("[discord] client init failed: {e}"); return; }
        }
    }
    if let Some(c) = st.client.as_mut() {
        match c.connect() {
            Ok(_) => { st.connected = true; log::info!("[discord] connected"); }
            Err(e) => log::debug!("[discord] not connected (Discord closed?): {e}"),
        }
    }
}

#[tauri::command]
pub fn discord_set_presence(payload: Presence) {
    let mut guard = STATE.lock().unwrap();
    let st = ensure_state(&mut guard);
    try_connect(st);
    if !st.connected { st.last_payload = Some(payload); return; } // retry on next call

    let changed = st.last_payload.as_ref() != Some(&payload);
    if !should_send(Instant::now(), st.last_sent, changed) { return; }

    // Hold owned strings so the borrowed Activity stays valid for set_activity.
    let details = payload.details.clone().unwrap_or_default();
    let state = payload.state.clone().unwrap_or_default();
    let large = payload.large_image.clone().unwrap_or_default();
    let small = payload.small_image.clone().unwrap_or_default();
    let (blabel, burl) = (payload.button_label.clone().unwrap_or_default(),
                          payload.button_url.clone().unwrap_or_default());

    let mut act = activity::Activity::new();
    if !details.is_empty() { act = act.details(&details); }
    if !state.is_empty() { act = act.state(&state); }
    let mut assets = activity::Assets::new();
    if !large.is_empty() { assets = assets.large_image(&large); }
    if !small.is_empty() { assets = assets.small_image(&small); }
    act = act.assets(assets);
    if !blabel.is_empty() && !burl.is_empty() {
        act = act.buttons(vec![activity::Button::new(&blabel, &burl)]);
    }

    if let Some(c) = st.client.as_mut() {
        match c.set_activity(act) {
            Ok(_) => { st.last_sent = Some(Instant::now()); st.last_payload = Some(payload); }
            Err(e) => { log::debug!("[discord] set_activity failed: {e}"); st.connected = false; }
        }
    }
}

#[tauri::command]
pub fn discord_clear_presence() {
    let mut guard = STATE.lock().unwrap();
    let st = ensure_state(&mut guard);
    if let Some(c) = st.client.as_mut() {
        let _ = c.clear_activity();
    }
    st.last_payload = None;
}

/// Called on app exit.
pub fn shutdown() {
    if let Ok(mut guard) = STATE.lock() {
        if let Some(st) = guard.as_mut() {
            if let Some(c) = st.client.as_mut() {
                let _ = c.clear_activity();
                let _ = c.close();
            }
        }
    }
}
```

- [ ] **Step 2: Wire into `lib.rs`**

In `src-tauri/src/lib.rs`: add `mod discord;` near the top (after the `use` lines). Add the two commands to the existing `tauri::generate_handler![...]` list: `discord::discord_set_presence, discord::discord_clear_presence`. In the `RunEvent::Exit` arm (inside `.run(|app_handle, event| { ... })`), add `discord::shutdown();` before/after the existing sidecar kill.

- [ ] **Step 3: Paste the Application ID**

Replace `REPLACE_WITH_APPLICATION_ID` in `discord.rs` with the real "Mario Kart World" Application ID from the prerequisite.

- [ ] **Step 4: Verify it compiles + tests pass**

Run: `cd src-tauri && cargo build && cargo test discord`
Expected: builds clean; 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/discord.rs src-tauri/src/lib.rs
git commit -m "feat(rust): discord set/clear presence commands + exit cleanup"
```

---

## Phase D — Frontend pure logic (vitest)

### Task D1: add vitest

**Files:**
- Modify: `package.json` (devDep + script)

- [ ] **Step 1: Install vitest**

Run: `npm install -D vitest`

- [ ] **Step 2: Add the test script**

In `package.json` `"scripts"`, add: `"test:js": "vitest run"`.

- [ ] **Step 3: Verify the runner works (no tests yet is fine)**

Run: `npm run test:js`
Expected: vitest runs, reports "no test files found" (exit 0 or a clear no-tests message). Proceed.

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json
git commit -m "chore(frontend): add vitest for unit tests"
```

### Task D2: `discordFormat.js` — slugify + time/delta formatting

**Files:**
- Create: `src/lib/discordFormat.js`
- Create: `src/lib/discordFormat.test.js`

- [ ] **Step 1: Write the failing tests**

```js
// src/lib/discordFormat.test.js
import { describe, it, expect } from "vitest";
import { courseSlug, parseTime, formatDelta } from "./discordFormat.js";

describe("courseSlug", () => {
  it("slugifies display names to image stems", () => {
    expect(courseSlug("Wario's Galleon")).toBe("warios_galleon");
    expect(courseSlug("Great ? Block Ruins")).toBe("great_block_ruins");
    expect(courseSlug("Mario Bros. Circuit")).toBe("mario_bros_circuit");
    expect(courseSlug("Sky-High Sundae")).toBe("sky_high_sundae");
    expect(courseSlug("Rainbow Road")).toBe("rainbow_road");
  });
  it("returns null for empty", () => expect(courseSlug(null)).toBe(null));
});

describe("parseTime", () => {
  it("parses m:ss.mmm to ms", () => {
    expect(parseTime("1:57.812")).toBe(117812);
    expect(parseTime("0:41.000")).toBe(41000);
  });
});

describe("formatDelta", () => {
  it("keeps 3 decimals, trailing zeros, ahead/behind", () => {
    expect(formatDelta(-420)).toBe("0.420s ahead of PB");
    expect(formatDelta(73)).toBe("0.073s behind PB");
    expect(formatDelta(-1500)).toBe("1.500s ahead of PB");
  });
  it("uses m:ss.mmm beyond a minute", () => {
    expect(formatDelta(-61234)).toBe("1:01.234 ahead of PB");
  });
});
```

- [ ] **Step 2: Run; verify it fails**

Run: `npm run test:js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```js
// src/lib/discordFormat.js
// Pure helpers for Discord presence text. No Svelte/Tauri imports — unit-testable.

export function courseSlug(name) {
  if (!name) return null;
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

export function parseTime(str) {
  if (!str) return null;
  const m = /^(\d+):(\d{1,2})\.(\d{1,3})$/.exec(str);
  if (!m) return null;
  return (+m[1]) * 60000 + (+m[2]) * 1000 + (+m[3].padEnd(3, "0"));
}

function magnitude(absMs) {
  if (absMs >= 60000) {
    const m = Math.floor(absMs / 60000);
    const s = Math.floor((absMs % 60000) / 1000);
    const ms = absMs % 1000;
    return `${m}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
  }
  return (absMs / 1000).toFixed(3) + "s";
}

// deltaMs < 0 means faster than PB (ahead).
export function formatDelta(deltaMs) {
  const ahead = deltaMs < 0;
  return `${magnitude(Math.abs(Math.round(deltaMs)))} ${ahead ? "ahead of PB" : "behind PB"}`;
}
```

- [ ] **Step 4: Run; verify pass**

Run: `npm run test:js`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/lib/discordFormat.js src/lib/discordFormat.test.js
git commit -m "feat(frontend): discord format helpers (slug, time, delta)"
```

### Task D3: `discordPayload.js` — screen→payload mapping

**Files:**
- Create: `src/lib/discordPayload.js`
- Create: `src/lib/discordPayload.test.js`

- [ ] **Step 1: Write the failing tests**

```js
// src/lib/discordPayload.test.js
import { describe, it, expect } from "vitest";
import { computePresence, UNCHANGED } from "./discordPayload.js";

const base = {
  screen: "RACING", course: "Rainbow Road", character: "Mario", kart: "Pipe Frame",
  resets: 12, curLap: 3, totLap: 3,
  playerSplits: { 1: 40000, 2: 80000 }, pbSplits: { 1: 40420, 2: 80310, 3: 120000 },
  finalTime: null, isNewPb: false, twitchUrl: "",
};

describe("computePresence", () => {
  it("idle -> penguin", () => {
    const p = computePresence({ ...base, screen: "UNKNOWN" });
    expect(p.large_image).toBe("penguin");
    expect(p.small_image).toBeUndefined();
    expect(p.details).toBe("Idle");
  });

  it("character select -> penguin + text", () => {
    const p = computePresence({ ...base, screen: "CHARACTER_SELECT" });
    expect(p.large_image).toBe("penguin");
    expect(p.details).toBe("Choosing a character");
  });

  it("course select -> 'Choosing a track'", () => {
    expect(computePresence({ ...base, screen: "COURSE_SELECT" }).details).toBe("Choosing a track");
  });

  it("unmapped menu -> In the menus", () => {
    expect(computePresence({ ...base, screen: "MAIN_MENU" }).details).toBe("In the menus");
  });

  it("ignore screens -> UNCHANGED", () => {
    for (const s of ["RACE_MENU", "RESET", "GHOST_RESET", "UNKNOWN_RESET", "HOME"])
      expect(computePresence({ ...base, screen: s })).toBe(UNCHANGED);
  });

  it("racing lap 3 with PB -> delta from last completed lap", () => {
    const p = computePresence(base); // last completed lap = 2: 80000-80310 = -310
    expect(p.large_image).toBe("rainbow_road");
    expect(p.small_image).toBe("penguin");
    expect(p.details).toBe("Rainbow Road · 12 resets");
    expect(p.state).toBe("Lap 3/3 · 0.310s ahead of PB");
  });

  it("racing lap 1 -> character / kart (no delta yet)", () => {
    const p = computePresence({ ...base, curLap: 1, playerSplits: {} });
    expect(p.state).toBe("Lap 1/3 · Mario · Pipe Frame");
  });

  it("racing with no PB -> character / kart", () => {
    const p = computePresence({ ...base, pbSplits: null });
    expect(p.state).toBe("Lap 3/3 · Mario · Pipe Frame");
  });

  it("singular reset", () => {
    expect(computePresence({ ...base, resets: 1 }).details).toBe("Rainbow Road · 1 reset");
  });

  it("ghost -> course art + Watching a ghost", () => {
    const p = computePresence({ ...base, screen: "GHOST" });
    expect(p.large_image).toBe("rainbow_road");
    expect(p.small_image).toBe("penguin");
    expect(p.details).toBe("Rainbow Road");
    expect(p.state).toBe("Watching a ghost");
  });

  it("results new PB", () => {
    const p = computePresence({ ...base, screen: "POST_TIME_TRIAL", finalTime: "1:57.812", isNewPb: true });
    expect(p.details).toBe("Rainbow Road · finished");
    expect(p.state).toBe("1:57.812 · New personal best");
  });

  it("results not a PB -> delta vs PB total", () => {
    const p = computePresence({ ...base, screen: "POST_TIME_TRIAL", finalTime: "2:00.500", isNewPb: false });
    expect(p.state).toBe("2:00.500 · 0.500s behind PB"); // 120500 - 120000
  });

  it("twitch url adds a button on racing", () => {
    const p = computePresence({ ...base, twitchUrl: "https://twitch.tv/me" });
    expect(p.button_label).toBe("Watch on Twitch");
    expect(p.button_url).toBe("https://twitch.tv/me");
  });
});
```

- [ ] **Step 2: Run; verify it fails**

Run: `npm run test:js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```js
// src/lib/discordPayload.js
// Pure mapping: tracker state -> Discord presence payload. No Svelte/Tauri imports.
import { courseSlug, parseTime, formatDelta } from "./discordFormat.js";

export const UNCHANGED = Symbol("unchanged");

const IGNORE = new Set([
  "RACE_MENU", "RESET", "GHOST_RESET", "UNKNOWN_RESET",
  "REPLAY_MENU", "UNKNOWN_RACE_ACTIVE", "HOME",
]);
const SETUP = { CHARACTER_SELECT: "Choosing a character", KART_SELECT: "Choosing a kart", COURSE_SELECT: "Choosing a track" };

function withButton(p, twitchUrl) {
  if (twitchUrl) { p.button_label = "Watch on Twitch"; p.button_url = twitchUrl; }
  return p;
}
function lastCompletedDelta(s) {
  const lap = (s.curLap ?? 0) - 1;
  if (lap >= 1 && s.pbSplits && s.pbSplits[lap] != null && s.playerSplits && s.playerSplits[lap] != null)
    return s.playerSplits[lap] - s.pbSplits[lap];
  return null;
}
function charKart(s) { return `${s.character ?? "?"} · ${s.kart ?? "?"}`; }

export function computePresence(s) {
  const screen = s.screen;
  if (IGNORE.has(screen)) return UNCHANGED;

  if (screen === "UNKNOWN") return { large_image: "penguin", details: "Idle" };
  if (SETUP[screen]) return { large_image: "penguin", details: SETUP[screen] };

  if (screen === "RACING") {
    const slug = courseSlug(s.course) || "splash";
    const resets = `${s.resets} reset${s.resets === 1 ? "" : "s"}`;
    const delta = lastCompletedDelta(s);
    const line2 = delta != null
      ? `Lap ${s.curLap}/${s.totLap} · ${formatDelta(delta)}`
      : `Lap ${s.curLap}/${s.totLap} · ${charKart(s)}`;
    return withButton({ large_image: slug, small_image: "penguin",
                        details: `${s.course} · ${resets}`, state: line2 }, s.twitchUrl);
  }

  if (screen === "GHOST") {
    const slug = courseSlug(s.course) || "splash";
    return withButton({ large_image: slug, small_image: "penguin",
                        details: s.course || "", state: "Watching a ghost" }, s.twitchUrl);
  }

  if (screen === "POST_TIME_TRIAL") {
    const slug = courseSlug(s.course) || "splash";
    let suffix;
    if (s.isNewPb) suffix = "New personal best";
    else if (s.pbSplits && Object.keys(s.pbSplits).length) {
      const lastLap = Math.max(...Object.keys(s.pbSplits).map(Number));
      const pbTotal = s.pbSplits[lastLap];
      const myMs = parseTime(s.finalTime);
      suffix = (myMs != null) ? formatDelta(myMs - pbTotal) : charKart(s);
    } else suffix = charKart(s);
    return withButton({ large_image: slug, small_image: "penguin",
                        details: `${s.course} · finished`, state: `${s.finalTime} · ${suffix}` }, s.twitchUrl);
  }

  // TITLE / MAIN_MENU / HOME-excluded / SINGLEPLAYER_MENU / TIME_TRIALS / START_TIME_TRIAL / GALLERY / anything else
  return { large_image: "penguin", details: "In the menus" };
}
```

- [ ] **Step 4: Run; verify pass**

Run: `npm run test:js`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/lib/discordPayload.js src/lib/discordPayload.test.js
git commit -m "feat(frontend): screen->presence payload mapping"
```

---

## Phase E — Frontend wiring (manual verification)

### Task E1: reset-counter store

**Files:**
- Create: `src/lib/resets.js`
- Modify: `src/lib/stores.js` (re-export for convenience)

- [ ] **Step 1: Implement the store**

```js
// src/lib/resets.js
// App-owned per-session reset counter. Counts transitions INTO the RESET screen
// (not GHOST_RESET / UNKNOWN_RESET). Resets to 0 when the course changes.
import { writable, get } from "svelte/store";
import { screen, selection } from "./stores.js";

export const resets = writable(0);

let prevScreen = null;
let prevCourse = null;

screen.subscribe((s) => {
  if (s === "RESET" && prevScreen !== "RESET") resets.update((n) => n + 1);
  prevScreen = s;
});

selection.subscribe((sel) => {
  if (sel.course !== prevCourse) { prevCourse = sel.course; resets.set(0); }
});
```

- [ ] **Step 2: Type-check**

Run: `npm run check`
Expected: 0 errors (the import paths resolve; `get` import is unused — remove it if `svelte-check` warns).

- [ ] **Step 3: Commit**

```bash
git add src/lib/resets.js
git commit -m "feat(frontend): per-session reset counter store"
```

### Task E2: pb_splits store + RACING-entry fetch + is-new-PB flag

**Files:**
- Modify: `src/lib/stores.js` (add `pbSplits`, `newPbThisRun`)
- Modify: `src/App.svelte` (handle `pb_splits`, `pb_achieved`; request on RACING entry)

- [ ] **Step 1: Add stores**

In `src/lib/stores.js`, add:

```js
export const pbSplits     = writable(null);  // {lap: split_ms} for current course PB | null
export const newPbThisRun = writable(false); // set by pb_achieved, cleared on race start
```

- [ ] **Step 2: Handle the events + fetch on RACING entry**

In `src/App.svelte`'s `tracker-event` handler:

- In the `case "screen_change":` block, where it already fetches on `msg.to === "RACING"`, add a request for splits and clear the new-PB flag at the start of a run:

```js
        if (msg.to === "RACING") {
          newPbStore.set(false);
          if (selCourse) send({ type: "get_pb_splits", course: selCourse });
          if (!_fetchedThisRace && selCourse) {
            _fetchedThisRace = true;
            send({ type: "get_replay_paths",   course: selCourse });
            send({ type: "get_minimap_sample", course: selCourse });
          }
        } else if (msg.from === "RACING") {
          _fetchedThisRace = false;
        }
```

- Add two new cases:

```js
      case "pb_splits":
        pbSplitsStore.set(msg.splits ?? null);
        break;
      case "pb_achieved":
        newPbStore.set(true);
        pushLog(`[pb] ${msg.course}  ${msg.time}`);
        break;
```

- Add the imports at the top of the script block where the other stores are imported:

```js
import { pbSplits as pbSplitsStore, newPbThisRun as newPbStore } from "./lib/stores.js";
```

- [ ] **Step 3: Type-check**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add src/lib/stores.js src/App.svelte
git commit -m "feat(frontend): pb_splits store + RACING-entry fetch + new-PB flag"
```

### Task E3: settings (localStorage) + `discord.js` orchestrator + init

**Files:**
- Create: `src/lib/discordSettings.js`
- Create: `src/lib/discord.js`
- Modify: `src/App.svelte` (`onMount` init)

- [ ] **Step 1: Settings store backed by localStorage**

```js
// src/lib/discordSettings.js
import { writable } from "svelte/store";

const ENABLED_KEY = "discord_enabled";
const TWITCH_KEY = "discord_twitch_url";

const initialEnabled = localStorage.getItem(ENABLED_KEY);
export const discordEnabled = writable(initialEnabled === null ? true : initialEnabled === "true");
export const twitchUrl = writable(localStorage.getItem(TWITCH_KEY) || "");

discordEnabled.subscribe((v) => localStorage.setItem(ENABLED_KEY, String(v)));
twitchUrl.subscribe((v) => localStorage.setItem(TWITCH_KEY, v || ""));
```

- [ ] **Step 2: Orchestrator — subscribe stores, compute, invoke Rust**

```js
// src/lib/discord.js
// Decoupled Discord presence driver. Reads existing stores, computes the payload
// via the pure mapping, and calls the Rust commands. Reads only — no UI mutation.
import { invoke } from "@tauri-apps/api/core";
import { get } from "svelte/store";
import { screen, selection, race, pbSplits, newPbThisRun } from "./stores.js";
import { resets } from "./resets.js";
import { discordEnabled, twitchUrl } from "./discordSettings.js";
import { computePresence, UNCHANGED } from "./discordPayload.js";
import { parseTime } from "./discordFormat.js";

function snapshot() {
  const sel = get(selection);
  const r = get(race);
  // race.splits is keyed by completed-lap index; coerce to {lap:int -> ms}.
  const playerSplits = {};
  for (const [lap, t] of Object.entries(r.splits || {})) {
    const ms = parseTime(t);
    if (ms != null) playerSplits[Number(lap)] = ms;
  }
  return {
    screen: get(screen),
    course: sel.course, character: sel.char, kart: sel.kart,
    resets: get(resets),
    curLap: r.curLap, totLap: r.totLap,
    playerSplits, pbSplits: get(pbSplits),
    finalTime: r.finishTime, isNewPb: get(newPbThisRun),
    twitchUrl: get(twitchUrl),
  };
}
function push() {
  if (!get(discordEnabled)) { invoke("discord_clear_presence").catch(() => {}); return; }
  const payload = computePresence(snapshot());
  if (payload === UNCHANGED) return;
  invoke("discord_set_presence", { payload }).catch(() => {});
}

export function initDiscordPresence() {
  // Recompute whenever any input changes.
  [screen, selection, race, pbSplits, newPbThisRun, resets, discordEnabled, twitchUrl]
    .forEach((s) => s.subscribe(() => push()));
}
```

- [ ] **Step 3: Init once in App.svelte**

In `src/App.svelte`'s `onMount`, add (with the other imports): `import { initDiscordPresence } from "./lib/discord.js";` and call `initDiscordPresence();` once inside `onMount`.

- [ ] **Step 4: Type-check + build**

Run: `npm run check && npm run build`
Expected: 0 errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add src/lib/discordSettings.js src/lib/discord.js src/App.svelte
git commit -m "feat(frontend): discord presence orchestrator + settings"
```

### Task E4: settings UI (toggle + Twitch URL)

**Files:**
- Modify: `src/components/SettingsModal.svelte` (add a Discord section)

- [ ] **Step 1: Add the section**

In `SettingsModal.svelte`, import the stores and add a small section bound to them:

```svelte
<script>
  import { discordEnabled, twitchUrl } from "../lib/discordSettings.js";
</script>

<section class="settings-group">
  <h3>Discord</h3>
  <label><input type="checkbox" bind:checked={$discordEnabled} /> Show Discord Rich Presence</label>
  <label>Twitch URL (optional)
    <input type="text" placeholder="https://twitch.tv/yourname" bind:value={$twitchUrl} />
  </label>
</section>
```

(Match the file's existing markup/classes — mirror a neighbouring section's structure rather than these generic class names.)

- [ ] **Step 2: Type-check + build**

Run: `npm run check && npm run build`
Expected: 0 errors; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add src/components/SettingsModal.svelte
git commit -m "feat(frontend): Discord settings (enable toggle + Twitch URL)"
```

---

## Final verification

- [ ] **Python:** `python -m pytest -q` → all green (existing 47 + new pb_splits/asset tests).
- [ ] **JS:** `npm run test:js` → all green.
- [ ] **Rust:** `cd src-tauri && cargo test` → green.
- [ ] **Types/build:** `npm run check && npm run build` → 0 errors.
- [ ] **Manual (with Discord running + Application ID set + assets uploaded):** launch the app and walk the lifecycle — menus (penguin "In the menus"), character/kart/track select, a race with no PB (lap 1 shows character/kart; results shows final time + character/kart), set a PB, race again (lap 2+ shows live delta; results shows "New personal best" or delta), ghost ("Watching a ghost"), pause/reset (card unchanged), HOME (card unchanged). Toggle the setting off (presence clears) and set a Twitch URL (button appears for others). Quit (presence clears).

## Notes for the implementer

- **Store field names:** `selection` store uses `{ char, kart, course, … }` (not `character`); `race` uses `{ curLap, totLap, splits, finishTime }`. The orchestrator's `snapshot()` already maps these — keep them in sync if the stores change.
- **`race.splits` semantics:** keyed by completed-lap with a preformatted `m:ss.mmm` string (cumulative race time at that lap). Both player and PB splits are cumulative, so the delta is a direct subtraction at the matching lap.
- **Decoupling:** the only existing-file edits are `lib.rs` (3 lines), `App.svelte` (init + two event cases + fetch), `stores.js` (two stores), and one `SettingsModal` section. Removing `initDiscordPresence()` disables the whole feature.
