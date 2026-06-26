# Asset clip sweep — automated idle / spawn-in / flourish capture

**Date:** 2026-06-26
**Status:** Design approved, pending spec review
**Builds on:** `tools/asset_matte/README.md` ("Open / next work": kart-sweep combo capture + flourishes), `tools/autotemplate/` (nxbt nav harness), `mkw_tracker/tools/record_clips.py` (4K60 recorder), the idle matte pipeline (`loop_probe → extract_loop → matte_loop`).

---

## 1. Goal

Automatically record, for every character×costume and every character×costume×kart, the in-menu **idle**, **spawn-in**, and **flourish** animations from the live Switch 2 feed, as high-quality 4K60 clips — the source footage for thekartoff.com's transparent animated cards / roster-combo showcase.

The navigation, controller emulation, and screen-detection grounding already exist for *template* capture (`tools/autotemplate/full_runner.py`). This project layers **video capture** onto that proven harness and resolves the one thing template capture never needed: continuous ownership of the capture card while detection still runs.

## 2. What already exists (reused, not rebuilt)

| Capability | Source | Reuse |
|---|---|---|
| nxbt Pro Controller emulation | `tools/autotemplate/controller.py` | as-is |
| Continuous 120 Hz sender + held right-stick (anti-spin) | `tools/autotemplate/switch_bridge.py` (`sender_thread`, `rstick_down`) | as-is |
| Grid layout (chars, costumes, karts) | `full_capture.yaml` / `prompt.md` ordered cell lists | **parsed into a 2D grid model** (cell→row,col); nav + recovery computed, not path-replayed |
| Per-item grounding ("did the cursor reach the target?") | `full_runner._retry_if_nav_unchanged`, `at_check_asset_match` | extended: target-ID + keep/discard |
| Transition detection by tell-score | `full_runner.exit_course_select`, `_check_course_select` | reused; flourish uses "tell drops" |
| Bluetooth reconnect-with-retry | `full_runner._ctrl_connect_with_retry` | as-is |
| 4K60 ffmpeg recorder + tee'd preview | `mkw_tracker/tools/record_clips.py` | becomes the orchestrator's record engine |
| Idle loop measurement | `mkw_tracker/tools/loop_probe.py` | reused for segmentation |
| Loop extraction + matte | `tools/asset_matte/extract_loop.py`, `matte_loop.py` | reused for post-processing |
| Screen / selection detection | `mkw_tracker/detection/` | imported by the orchestrator, run on tee frames |

**Genuinely new:** (a) the capture **orchestrator** (Windows); (b) the **sweep runner** (WSL2, derived from `full_runner`); (c) a **segmentation** step that fans one recording into spawn-in / idle-loop / flourish.

## 3. Scope

**In scope.** One SDR pass in the current console language (`en_uk`). 153 character×costume grid cells × {idle, flourish}; 153 × 40 = 6,120 character×costume×kart combos × {spawn-in, idle, flourish}. Capture + per-recording event sidecars + skip-if-exists resume.

**Out of scope (separate, later).** The matte fan-out run itself (reuses the existing pipeline at ~100 GPU-hrs — batched after capture). Multi-language (assets are the character *render*, not name *text* — language-invariant). HDR capture (SDR only, for accurate colour).

**Counts** (from the real grid in `full_capture.yaml`): 3 rows × 51 = **153** char×costume cells; **40** karts (4 rows × 10). So **6,273 recordings** total (153 char + 6,120 kart), each yielding 2–3 assets.

## 4. Architecture

DirectShow on Windows is exclusive: the 4K60 recorder and detection cannot both open the card. Resolution (option **B**, chosen): **one ffmpeg owns the card continuously**, records the 4K60 clip on command, and *always* emits a downscaled 1080p tee; **detection runs on the tee**, so grounding works even mid-recording. `record_clips.py` is already built around this exact tee.

```
  WSL2 (Linux Bluetooth)                         Windows
  ┌─────────────────────────┐   WebSocket   ┌──────────────────────────────────┐
  │ sweep runner            │◄────JSON─────►│ capture orchestrator             │
  │  - nxbt control         │   commands    │  - ffmpeg owns capture card      │
  │  - grid nav (reuse)     │               │     ├─ 4K60 HEVC → <item>.mkv     │
  │  - keep/discard + retry │               │     └─ tee 1080p ~15fps → detect │
  │  - reconnect            │               │  - mkw_tracker.detection (tee)   │
  └─────────────────────────┘               │  - writes <item>.mkv + events    │
            │ nxbt / BT                      └──────────────────────────────────┘
            ▼                                          ▲ USB (capture card)
     Nintendo Switch 2 ───────── HDMI ────────── Elgato 4K X ──────────────────┘
```

This is the **same process split** as the existing `full_runner` ↔ tracker setup (WSL2 nav ↔ Windows capture over WebSocket), which is why it is low-risk — only the Windows endpoint changes (orchestrator instead of tracker) and the command vocabulary grows.

## 5. Components

### 5.1 Capture orchestrator (Windows, new — `mkw_tracker/tools/clip_orchestrator.py`)

- **Purpose:** sole capture-card consumer; records 4K60 clips on command, runs detection on the tee for grounding, owns the output filesystem, timestamps capture events.
- **Built on:** `record_clips.py`'s `FramePipe` / `tee_cmd` ffmpeg machinery + `mkw_tracker/detection/`.
- **Interface:** WebSocket server (default `ws://0.0.0.0:8766`). Messages in §7.
- **Depends on:** ffmpeg (bundled), `mkw_tracker.detection`, `mkw_tracker.config` (ROIs/settings).
- **Owns:** the recording clock (t=0 at ffmpeg record start), the `events.json` sidecar, skip-if-exists checks.

### 5.2 Sweep runner (WSL2, new — `tools/autotemplate/sweep_runner.py`)

- **Purpose:** drive the controller through the grid and orchestrate each item's record/ground/keep-or-discard.
- **Grid model:** the ordered char×costume and kart cell lists (from `full_capture.yaml` / `prompt.md`, trimmed into `scripts/clip_sweep.yaml`) are parsed into a 2D map — each cell → `(row, col)` (rights advance col, downs advance row, trailing blanks end a row). Navigation between any two cells is a **computed D-pad delta, not a replay** of the fixed path; this is what makes recovery cheap (§6.0).
- **Built on:** `full_runner.py` (control helpers, reconnect, skip logic) with a new per-item recording loop.
- **Interface:** CLI mirroring `full_runner` (`--mac`, `--capture-ws ws://<win-host>:8766`, `--start-from`, `--dry-run`).
- **Depends on:** `controller.py`, `switch_bridge.py` (held right-stick), the orchestrator WS.

### 5.3 Segmentation (post, new — `tools/asset_matte/segment_clip.py`)

- **Purpose:** read `<item>.mkv` + `<item>.events.json`, run `loop_probe` over the idle span, emit the 2–3 sub-clips (spawn-in, idle-loop, flourish) ready for `matte_loop`.
- **Hero ROI:** `loop_probe`/`extract_loop` crop a hero region to score the loop seam. Char-select uses the existing `HERO_ROI (1075,30,1800,845)`; kart-select sits differently and needs its own crop. Recording is full-frame, so this is a **segmentation-stage constant to measure once**, never a capture concern.
- **Depends on:** `loop_probe`, `extract_loop`.

## 6. Capture sequence

**Preconditions (operational):** HDR **off** on the Switch; Windows **camera-sharing off** (frame server otherwise steals exclusive DirectShow access); right-stick held down for the whole session (anti-spin); start on HOME with MKW hovered (as `full_capture.yaml`).

**Per language pass (one: `en_uk`):** preamble HOME → Time Trials → character select (reused from `full_capture.yaml`).

### 6.0 Navigation & recovery (grid model)

Every move is a computed D-pad delta over the §5.2 grid model, grounded after it lands. `ground` returns `read_name`, so the runner always knows its *actual* cell, not just "did it move":

- **On target** → proceed (keep the recording, for karts).
- **Mis-nav** (over/undershoot) → for karts `clip_abort`; then map `read_name → (row, col)`, compute the delta to the target, step there, and re-try. No corner-reset, no path replay.

Horizontal deltas (within a row) are fully exercised by the existing nav. Vertical moves only ever happen at row boundaries in the source path, so arbitrary-column up/down is the one grid behaviour to confirm at live bring-up (§12); until confirmed, cross-row recovery routes via a row end.

### 6.1 Character×costume item (no spawn-in)

```
verify-then-record: re-press until grounded on the target cell  (reuse _retry_if_nav_unchanged)
clip_begin char=<char>__<costume>
sleep idle_seconds (10s settled idle)
press A ; mark flourish                       # flourish plays, then character_select drops
orchestrator detects character_select drop → clip_done → we are now on kart select
```

The character flourish ends exactly as **kart select** appears — no B needed; flow continues straight into the kart sweep on Standard Kart.

### 6.2 Kart sweep (record-through-the-swap, keep/discard)

```
# Standard Kart (arrived from char-confirm): trigger its spawn-in by going off and back
clip_begin kart=<combo>__standard_kart
press right (off) ; press left (back on → spawn-in) ; mark swap
ground karts standard_kart →  matched? keep : (clip_abort, recover, retry)
sleep idle_seconds ; press A ; mark flourish
orchestrator detects kart_select drop → clip_done
press B → returns to Standard Kart (confirmed) ; is_screen kart_select sanity-check

# Each subsequent kart
clip_begin kart=<combo>__<kart>
press right (→ spawn-in onto this kart) ; mark swap
ground karts <kart> →  matched? keep : (clip_abort, recover, retry)
sleep idle_seconds ; press A ; mark flourish
orchestrator detects kart_select drop → clip_done
press B → returns to the same kart (confirmed) ; right to next  (loop)

# After the 40th kart: B → character select ; navigate to next cell
```

**Grounding semantics:** the kart name-plate updates the instant the cursor lands, *before* the spawn-in finishes, so `ground` fires ~1 s after the swap and a mis-nav (over/undershoot, incl. Standard's off-and-back) aborts early instead of wasting the full 10 s. The check is a **positive target-ID** (`at_check_asset_match` against the *target* template), not "still on previous".

**Flourish bracketing:** record from the A-press until the **current select screen's own tell drops** (`kart_select` for karts — there is an unidentifiable intermediary before `course_select`, so we cannot wait for it; `character_select` for characters). A `max_clip_seconds` backstop forces stop if a tell never drops.

## 7. Orchestrator WebSocket protocol

All messages JSON; the orchestrator owns the recording clock and filesystem.

| From runner | Orchestrator action | Reply |
|---|---|---|
| `{type:"exists", item}` | skip-if-exists check | `{type:"exists_result", done:bool}` |
| `{type:"clip_begin", item, category}` | start 4K60 record + tee clock at t=0 | `{type:"clip_begun"}` |
| `{type:"mark", event:"swap"\|"flourish"}` | stamp recording-relative time | `{type:"marked"}` |
| `{type:"ground", category, name}` | run detection on latest tee frame | `{type:"ground_result", matched, score, read_name}` |
| `{type:"clip_abort"}` | stop ffmpeg, delete `.mkv` | `{type:"clip_aborted"}` |
| `{type:"is_screen", screen}` | score a screen tell on the tee | `{type:"screen_score", score}` |

Unsolicited: after a `flourish` mark, the orchestrator watches the tee for the select-tell drop; on drop (or backstop) it stamps `flourish_end`, stops ffmpeg, writes `<item>.mkv` + `<item>.events.json`, and sends `{type:"clip_done", item, events}`. The runner waits for `clip_done` before pressing B.

`events.json` schema: `{ "item": "...", "category":"char|kart", "fps": 60, "swap_t": <s|null>, "flourish_t": <s>, "flourish_end_t": <s>, "duration_t": <s> }` (`swap_t` null for characters).

## 8. Output layout & naming

Mirrors the existing `captures_sdr/en_uk/combos/<char>__<costume>.png` convention (`__` separator, `__base` = no costume).

```
captures_sdr/en_uk/clips/<char>__<costume>.mkv               + .events.json   # character item
captures_sdr/en_uk/clips/<char>__<costume>__<kart>.mkv       + .events.json   # kart item
# post-processed (separate run):
captures_sdr/en_uk/assets/<char>__<costume>__idle_loop.webp  __flourish.webp
captures_sdr/en_uk/assets/<char>__<costume>__<kart>__spawn.webp  __idle_loop.webp  __flourish.webp
```

**Skip-if-exists** keys on the `.mkv` existing with non-zero size (resume after any interruption — essential over a ~40 hr run).

## 9. Segmentation (post)

From one recording + `events.json`:

| Segment | Frame span | Items |
|---|---|---|
| spawn-in | `swap_t` → idle-loop start | karts only |
| idle-loop | seamless window from `loop_probe` over `[swap_t\|0 … flourish_t]` | both |
| flourish | `flourish_t` → `flourish_end_t` | both |

`loop_probe` gives the idle period; `extract_loop` finds the seamless loop window within the idle span; **spawn-in end = idle-loop start**. The idle clip thus also supports the "play spawn-in once, then loop" web pattern by keeping `[spawn-in + idle-loop]` contiguous.

## 10. Parameters

| Parameter | Value | Notes |
|---|---|---|
| Idle dwell | **10 s** | `loop_probe` measured idles at 1.33–1.67 s → ~6–7 loops; tunable down to ~6 s later (~13 hr saving) |
| Record | 3840×2160 @ 60, HEVC NVENC p5, qp 14 | from `record_clips.py` defaults |
| Tee (detection) | 1920×1080 @ ~15 fps, bgr24 | 1080p so detection ROIs/coords work unchanged; 540p preview is too small for name-plate grounding |
| Language | `en_uk` only | single SDR pass |
| `max_clip_seconds` | ~25 s | backstop if a select-tell never drops |
| Ground match threshold | reuse selection thresholds (`at_check_asset_match`: name ≥ 0.85) | target-ID |

## 11. Estimate

| Part | Count | Time |
|---|---|---|
| Char-select idle+flourish | 153 items | ~1.3 hr |
| Kart sweep (spawn+idle+flourish per item) | 6,120 items (~23 s each) | **~39 hr** |
| **Total capture** | 6,273 recordings | **~40 hr** (3–4 calendar days incl. reconnects/retries) |

Matting is separable and comparable-scale (~100 GPU-hr at ~30 s/clip on the RTX 5080) — run after capture.

## 12. Risks & open questions

1. **Tee resolution adequacy** — confirm 1080p@15fps grounding reads kart/char name plates reliably; bump fps/res if marginal. *The one thing that could force an architecture tweak — validate first.*
2. **Arbitrary-column vertical nav** — the grid model's horizontal moves are proven; confirm down/up preserve the column from mid-row (else cross-row recovery routes via a row end). Cheap to check at bring-up.
3. **Intermediary needs >1 B** — `exit_course_select` already loops B until the tell clears; reuse that pattern for the post-flourish return.
4. **40 hr runtime stability** — Bluetooth *will* drop; survivability rests on reconnect-retry + skip-if-exists. Run in resumable chunks.
5. **Matte scale** — ~100 GPU-hr; out of scope here but plan the batch.

**Resolved:** B from a post-kart-flourish returns to the *same* kart (confirmed) → `is_screen kart_select` sanity only. Kart-select hero ROI is a segmentation-stage constant (§5.3), not a capture risk — recording is full-frame.

## 13. Testing strategy

- **Dry-run** (no hardware): `sweep_runner --dry-run` prints the full flow + every clip_begin/mark/ground (extends `full_runner`'s dry-run).
- **Single-combo smoke:** one char×costume + first ~3 karts end-to-end → verify `.mkv` + `events.json` written, grounding keep/discard fires, segmentation yields the right 2–3 sub-clips.
- **Unit tests** (extend `tests/test_record_clips.py`, `tests/test_loop_probe.py`):
  - segmentation frame-span math (events.json + loop period → correct ranges, char vs kart);
  - skip-if-exists / filename slug logic;
  - grounding keep/discard decision from a `ground_result`.
- **Visual validation:** eyeball spawn-in capture on 2–3 karts + Standard Kart's off-and-back.

## 14. Build phases (for the implementation plan)

1. **Orchestrator** — ffmpeg card-owner + 1080p tee + `detection` on tee + WS server + record-on-command + events.json. Validate tee-grounding reads names (risk 4).
2. **Sweep runner** — `clip_sweep.yaml` grid (single language) + per-item record/ground/keep-discard loop + reconnect + skip-exists. Dry-run first.
3. **Live bring-up** — one combo + few karts on real hardware; confirm tee-grounding sharpness (risk 1) and arbitrary-column vertical nav (risk 2).
4. **Segmentation** — `segment_clip.py` + tests.
5. **Full run** — resumable chunks; then batch matte (separate).
