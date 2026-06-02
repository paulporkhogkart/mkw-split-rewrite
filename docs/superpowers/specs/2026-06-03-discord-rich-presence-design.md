# Discord Rich Presence — Design

**Date:** 2026-06-03
**Status:** Approved (design); pending implementation plan
**Component:** Tauri frontend + a small Rust module (decoupled, plugin-like). No change to the Python engine's role beyond one small read-only PB-splits emit.

## 1. Goal

Show a live Discord Rich Presence card while the user runs Mario Kart World time trials, driven entirely by the data the tracker already detects. The presence reflects what screen the user is on, and during a run shows the track, lap, reset count, and a live delta against their personal best.

The card's bold title reads **"Mario Kart World"** (the registered Discord application's name) — pbenguin runs quietly underneath.

## 2. Non-goals (YAGNI)

- No character/kart art (we don't have those icons; names appear as text only).
- No live minimap image (Discord can't host a per-frame generated image cheaply).
- No party/lobby/join features, no multiplayer (the app is time-trial focused).
- No elapsed timer on the card (explicitly dropped).
- No second presence button beyond the optional Twitch one.
- No coins/mushrooms on the card.

## 3. Prerequisite (user, one-time)

Create a Discord **Application** at the Developer Portal named **"Mario Kart World"**. Its **Application ID** (a public client ID, not a secret) is the only value the RPC client needs to connect, and the portal is where the image **Art Assets** are uploaded (§9). The Application ID is stored as a constant/config in the app. Implementation will include exact click-by-click steps.

## 4. Architecture — a decoupled "plugin"

The feature is isolated so it can be added or removed with near-zero impact on the rest of the app. Three new, self-contained units plus three one-line touch-points.

### New units

- **`src-tauri/src/discord.rs`** — owns the Discord IPC connection via the `discord-rich-presence` crate. Responsibilities: lazy connect, auto-reconnect, **graceful no-op when Discord isn't running/installed**, debounce/rate-limit (§14), and clear-on-exit. Public surface is just two Tauri commands:
  - `discord_set_presence(payload)` — payload carries the already-resolved fields: `details`, `state`, `large_image`, `small_image`, optional `button_label` + `button_url`. No tooltip fields.
  - `discord_clear_presence()`.
- **`src/lib/discord.js`** — a single `initDiscordPresence()` that subscribes to the existing Svelte stores (`screen`, `selection`, `race`) plus the new `resets` store and a `discordSettings` store, maps state → payload per §6–§8, and calls the Rust command. **Reads only; never mutates UI state.** Removing its one import disables the entire feature with no side effects.
- **`src/lib/resets.js`** — a small app-owned store that subscribes to the `screen` store and counts time-trial restarts per session (§10). Independent of Discord; reusable elsewhere in the UI.

### Touch-points on existing code (the entire footprint)

1. `src-tauri/src/lib.rs` — add the two commands to the existing `invoke_handler!` list, and call a `discord::clear()` on the existing `RunEvent::Exit`.
2. `src/App.svelte` — one `initDiscordPresence()` line in `onMount`.
3. The settings modal — one small "Discord" section (§12).

### Why frontend-driven (vs. Rust subscribing to events)

The frontend already parses every `tracker-event` into clean stores (screen label, selection, splits). Driving presence from JS reuses that mapping; the Rust side stays a dumb, robust transport. This matches the existing split of concerns (Rust owns process/IPC lifecycle like the sidecar; JS owns event→state).

## 5. Data sources (already available)

From `tracker-event` IPC, already mirrored into stores:

| Need | Source |
|---|---|
| Current screen | `screen_change` / `heartbeat.screen` → `screen` store |
| Character / kart / course | `selection_update` → `selection` store |
| Current / total lap, per-lap splits | `lap_update` → `race` store |
| Finish total time | `finish` / `split_recorded(is_final)` → `race` store |
| New PB | `pb_achieved` |
| Reset events | `screen_change` to `RESET` → `resets` store (new) |
| **PB per-lap splits** (for the live delta) | **new** `pb_splits` emit, see §11 |

## 6. Screen → presence mapping

Course art = the per-track icon (large image, §9). Penguin = the pbenguin app icon. The small penguin **badge** appears on every course-art card and never when the penguin is already the large image.

| Screen(s) | Large image | Badge | Details (line 1) | State (line 2) |
|---|---|---|---|---|
| `UNKNOWN` | penguin | — | `Idle` | — |
| `TITLE`, `MAIN_MENU`, `SINGLEPLAYER_MENU`, `TIME_TRIALS`, `START_TIME_TRIAL`, `GALLERY`, and any other detected-but-unmapped screen | penguin | — | `In the menus` | — |
| `CHARACTER_SELECT` | penguin | — | `Choosing a character` | — |
| `KART_SELECT` | penguin | — | `Choosing a kart` | — |
| `COURSE_SELECT` | penguin | — | `Choosing a track` | — |
| `RACING` | course art | penguin | `{Course} · {N} resets` | lap-dependent (§7) |
| `GHOST` | course art | penguin | `{Course}` | `Watching a ghost` |
| `POST_TIME_TRIAL` | course art | penguin | `{Course} · finished` | `{FinalTime} · {suffix}` — suffix per §7 |
| `RACE_MENU`, `RESET`, `GHOST_RESET`, `UNKNOWN_RESET`, `REPLAY_MENU`, `UNKNOWN_RACE_ACTIVE`, and HOME* | — | — | **presence left untouched** | (no update sent) |

*HOME is treated as an "ignore/leave-untouched" screen (Switch home overlay), not a menu — so popping to Home mid-run keeps the racing card up.

- **No tooltips** — `large_text` / `small_text` are not set on any card.
- The results suffix depends on PB state (§7).

## 7. Racing card — line 2 across the race

Line 2 depends on whether a PB delta is available yet:

- **Before the first split lands (lap 1):** `Lap 1/{T} · {Character} · {Kart}` — character/kart fill the otherwise-empty line.
- **From the first recorded split onward (lap ≥ 2):** `Lap {C}/{T} · {delta} ahead of PB` / `… behind PB` (wording per §8).
- **No stored PB for the track:** keep showing `{Character} · {Kart}` for the whole race (never blank, never "no PB").

The trigger is the first recorded lap split, so it adapts to any total-lap count automatically.

**Results (`POST_TIME_TRIAL`) line 2** — `{FinalTime} · {suffix}`, where the suffix is:
- **new PB this run** (a `pb_achieved` fired) → `New personal best`
- **else, a stored PB exists** → `{delta} behind PB` (same 3-dp delta wording as §8 — how far this run finished off the PB)
- **else (no stored PB at all)** → `{Character} · {Kart}` (as on lap 1 / the no-PB racing case)

## 8. Time & delta formatting

- **Always 3 decimal places, trailing zeros kept.** Never trimmed.
- **Final time / any clock time:** `m:ss.mmm` — e.g. `1:57.812`, `7:34.123`.
- **PB delta:** seconds with 3 dp + `s`, plus an explicit direction word — `0.420s ahead of PB`, `0.073s behind PB`, `1.500s ahead of PB`. No `+`/`−` sign, no emoji, no color (Discord presence text supports none of these). If a delta ever reaches ≥ 60s, format as `m:ss.mmm` with the direction word.
- Direction: faster than PB → "ahead", slower → "behind".

## 9. Image assets — uploaded to the Discord portal

**Decision:** upload all images to the Discord application's **Art Assets** and reference them by **key**. Rationale: cached by Discord, zero runtime network dependency, offline-safe; ~31 assets is well under the 300 limit. (External-URL proxying was rejected: Discord fetches the URL server-side, so `mario.wiki.gallery` hotlink/referrer blocks would bite, and it adds a runtime dependency.)

### Keys

- Course assets are keyed by the **course slug** = `slugify(displayName)`, where `slugify` = lowercase, replace each run of non-alphanumeric characters with a single `_`, trim leading/trailing `_`. Verified to produce exactly the existing `images/courses/en_us/*.png` stems for all 30 courses (e.g. `Wario's Galleon`→`warios_galleon`, `Great ? Block Ruins`→`great_block_ruins`, `Mario Bros. Circuit`→`mario_bros_circuit`, `Sky-High Sundae`→`sky_high_sundae`). The frontend slugifies the emitted course display name at runtime — no lookup table.
- Penguin badge/large image keyed `penguin` (from `src-tauri/icons/128x128@2x.png`).
- The unused MKW key-art splash is uploaded as `splash` and used as the **fallback large image** if a course has no matching asset (defensive; shouldn't happen for the 30 known tracks).

### Fetch/prep script

A repo script (`scripts/fetch_discord_assets.py` or similar) downloads the 30 course icons from the provided URL list, names each `<slug>.png`/`.jpg`, and drops them in an output folder ready to drag into the portal (or upload via the portal API if a token is supplied). The URL→slug mapping it uses (resolved from `temp/mkw/New Text Document.txt`, confirmed 1:1 against the 30 internal slugs):

| Line | Wiki name | Slug |
|---|---|---|
| 1 | (Nintendo key art) | `splash` (fallback) |
| 2 | Mario_Bros_Circuit | `mario_bros_circuit` |
| 3 | Crown_City | `crown_city` |
| 4 | Whistlestop_Summit | `whistlestop_summit` |
| 5 | DK_Spaceport | `dk_spaceport` |
| 6 | Desert_Hills | `desert_hills` |
| 7 | Shy_Guy_Bazaar | `shy_guy_bazaar` |
| 8 | Wario_Stadium | `wario_stadium` |
| 9 | Airship_Fortress | `airship_fortress` |
| 10 | DK_Pass | `dk_pass` |
| 11 | Starview_Peak | `starview_peak` |
| 12 | Sky-High_Sundae | `sky_high_sundae` |
| 13 | Wario_Shipyard | `warios_galleon` *(name variance; confirmed by elimination)* |
| 14 | Koopa_Troopa_Beach | `koopa_troopa_beach` |
| 15 | Faraway_Oasis | `faraway_oasis` |
| 16 | Peach-Beach | `peach_beach` |
| 17 | Salty_Salty_Speedway | `salty_salty_speedway` |
| 18 | Dino_Dino_Jungle | `dino_dino_jungle` |
| 19 | Question_Ruins | `great_block_ruins` *(name variance; confirmed by elimination)* |
| 20 | Cheep_Cheep_Falls | `cheep_cheep_falls` |
| 21 | Dandelion_Depths | `dandelion_depths` |
| 22 | Boo_Cinema | `boo_cinema` |
| 23 | Dry_Bones_Burnout | `dry_bones_burnout` |
| 24 | Moo_Moo_Meadows | `moo_moo_meadows` |
| 25 | Choco_Mountain | `choco_mountain` |
| 26 | Toads_Factory | `toads_factory` |
| 27 | Bowsers_Castle | `bowsers_castle` |
| 28 | Acorn_Heights | `acorn_heights` |
| 29 | Mario_Circuit | `mario_circuit` |
| 30 | Peach_Stadium | `peach_stadium` |
| 31 | Rainbow_Road | `rainbow_road` |

## 10. Reset counter (`src/lib/resets.js`)

- Subscribes to the `screen` store. Each transition **into** `RESET` increments a per-session counter. `GHOST_RESET` and `UNKNOWN_RESET` do **not** count toward it.
- The counter resets to 0 when the selected course changes (new track attempt) and on app start.
- Exposed as a writable/derived store, consumed by `discord.js`. App-owned (not Discord's concern), and available to surface elsewhere in the UI later.
- Debounce: only count one reset per entry into a reset screen (not per frame) — the store transitions, so it naturally fires once per change.

## 11. Live PB delta — small backend assist

The delta compares the player's time at each completed lap to the PB's time at the same lap.

- **New IPC:** command `get_pb_splits {course}` → event `pb_splits {course, splits:[ms,…], total_ms}` (or `splits:null` if no PB). This is a natural extension of the existing PB/replay IPC family (`pb_achieved`, `pb_export`, `replay_paths`) and keeps presentation out of Python — it only ships raw numbers.
- **Frontend:** on entering `RACING`, `discord.js` (or the existing RACING-entry fetch hook in `App.svelte`) requests `get_pb_splits` for the current course and caches the result. At each `lap_update`, it compares the player's cumulative time at the just-completed lap to the PB's cumulative at that lap → signed delta → "ahead/behind" text.
- Exact split arithmetic (per-lap vs cumulative) is resolved during implementation against the real `lap_update`/replay split semantics; the data needed and the ownership are fixed here.

## 12. Settings

Minimal, stored **frontend-side** (Tauri store / localStorage) to stay decoupled from the Python `config` table:

- `discord_enabled` (bool, default **on**) — when off, `discord.js` calls `discord_clear_presence()` and stops sending updates.
- `discord_twitch_url` (string, default empty) — when non-empty, the racing/results card gets a **"Watch on Twitch"** button → this URL. When empty, no button is set. (Note: Discord typically hides a user's own buttons from themselves; others see them.)

UI: one small "Discord" section in the existing settings modal — an enable toggle and a Twitch URL text field.

## 13. Rust module detail

- Crate: `discord-rich-presence` (maintained). `DiscordIpcClient::new(APP_ID)`, `.connect()`, `.set_activity(Activity…)`, `.clear_activity()`, `.close()`.
- Activity built from the payload: `.details()`, `.state()`, `.assets(large_image/small_image)` (no `large_text`/`small_text`), `.buttons(vec![Button::new(label,url)])` (0 or 1 button). **No timestamps** (no timer).
- Connection lifecycle: try connect on first `set`; if it fails (Discord closed/not installed), keep the latest payload and retry on a backoff — never error to the UI. Reconnect transparently if the pipe drops.
- Holds the latest desired payload behind a mutex; a single worker applies it respecting the debounce.

## 14. Update cadence / rate limiting

Discord rate-limits activity updates (~5 per 20s). Our triggers (screen change, lap crossing, reset, selection change) are naturally infrequent, but the Rust module **coalesces**: it keeps only the latest payload and enforces a minimum send interval (~2–3s), sending the most recent state when the window opens. Identical consecutive payloads are dropped (no redundant sends).

## 15. Edge cases & error handling

- **Discord not running/installed:** silent no-op + background retry. App never blocks or errors.
- **No PB for course:** line 2 stays character/kart (§7), including on the results card.
- **Course with no asset:** large image falls back to `splash`.
- **App exit:** clear presence on `RunEvent::Exit`.
- **Feature disabled:** clear once, then idle.
- **Rapid screen flapping:** coalescing + the "ignore" bucket prevent churn; transient reset/pause screens never overwrite.

## 16. Testing

- **Unit (JS):** slugify (all 30 display names → expected slugs), screen→payload mapping per row of §6, line-2 selection logic across lap states and no-PB, time/delta formatting (3 dp, trailing zeros, ahead/behind, ≥60s).
- **Unit (Rust):** payload→Activity construction (button present/absent, fallback image), debounce/coalesce timing, graceful behaviour when connect fails.
- **Manual:** with Discord open, walk the lifecycle (menus → setup → race with/without PB → ghost → results), toggle the setting, set/clear the Twitch URL, and quit (presence clears).

## 17. Future (out of scope now)

- Character/kart art as the small badge if those icons are ever sourced.
- A second button (e.g. "Get pbenguin").
- Surfacing the reset counter in the app's own Rail UI.
- Per-language course display names (slugify already language-agnostic for `en_*`; other languages would need a slug map).
