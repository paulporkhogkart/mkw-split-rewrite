# Player Panel (Monitor redesign · sub-project #3) — Design

**Status:** design locked 2026-06-09. Next: implementation plan (writing-plans).

## Goal

Fill the monitor's reserved band with one live **"timing tower"** card per Season-1 player,
driven by the `presence` store from sub-project #2. Each card shows who the player is, what they
picked, and how their current race is going — consistent across racing / setup / finished / offline.

## Context (what already exists)

- **#1 layout:** `App.svelte` reserves `.player-band` (full width of the feed column, ~940px wide,
  ~146px tall) between the feed footer and the bottom StatusBar. It currently holds a placeholder.
- **#2 presence pipeline:** the `presence` store (`src/lib/stores.js`) holds
  `{ [player_id]: PresenceEntry }`, broadcast from the server `/v1/presence` WS. `PresenceEntry`
  (`pi/src/presence/hub.ts`) carries: `player_id, name, color, online, screen, course, character,
  kart, costume, cur_lap, tot_lap, coins, mushrooms, completion (0..1), final_time, updated_at`.
  The roster is the active season's `season_rosters` (5 players: Paul, Aliias, Alex, Luke, Adymer).
- **Theme:** Neutral-Graphite tokens (`src/theme.css`), tabular figures, `palette.js`. Player
  colours come from `players.color` (seeded by `server/importer.py:PLAYER_COLORS`).

## The card (locked design)

A flat card on the continuous band (hairline dividers between cards, no rounding/shadow). One
consistent skeleton; only the content changes by state.

**Layout, left → right:**
- A 3px **player-colour spine** on the left edge (greyed when offline).
- The player's **figure** — a transparent cut-out gif still-frame, height-locked, flush to the
  card's bottom edge, ~33% width, with a little headroom above the head. Online = a late ("end")
  frame in colour; offline = an early ("start") frame, greyscaled.
- The **data zone** (right ~61%), stacked top → bottom in this order:
  1. **NAME** — uppercase, player colour (muted grey when offline).
  2. **Selection** block — `CHR` / `KRT` / `TRK` key/value rows (character, kart, course as
     **text**; `—` when unknown). This is the persistent "what they picked" group.
  3. *(spacer)*
  4. **Per-race cluster** (the volatile group, built toward the bottom): **RESETS** count →
     **PB** reference line (`PB 1:19.880`, with a coloured delta when finished) → **TIME**
     (the largest element) → **lap-segment bar**.
- The **lap-segment bar** sits on the bottom edge: one segment per lap (`tot_lap` segments), filled
  by `completion`; a small **position dot** rides the leading edge of the fill while racing.

**Rationale for the order** (per review): selections persist for the race, so they group as
identity up top; the timer/resets/lap bar all change constantly, so they cluster together at the
bottom for a single "how's the race" glance, with the live time largest, sitting right on the bar.

**States** (same skeleton, content adapts — there are *no* state tags):
| State | Figure | NAME | Selection | Per-race cluster | Primary "time" line |
|---|---|---|---|---|---|
| **Racing** | colour, end-frame | colour | filled | resets · PB · bar filling + dot | live time |
| **Setup** (char/kart/course select) | colour | colour | fills in as picked | hidden | "Choosing character/kart/track" |
| **Finished** | colour | colour | filled | resets · PB+delta · bar full (no dot) | final time |
| **Offline** | greyscale, start-frame | muted | cleared (`—`) | hidden | "last seen 3h ago" |

The **Racing** "time" is `—` until the engine provides a live clock (see Data); the bar + dot still
move via `completion`.

**Explicitly dropped** (from earlier rounds): coins & mushrooms (replaced by **resets**), the
course icon/image (distorts at this size — course is **text** only), the track-photo background,
the status tags (LIVE/SETUP/…), and all scrim/fade gradients (text lives on flat graphite in its
own zone, never over the figure).

## Data: what the card needs vs. what presence provides

**Already in `PresenceEntry`:** name, color, online, screen, course, character, kart, cur_lap,
tot_lap, completion, final_time, updated_at. (`coins`, `mushrooms`, `costume`, `pos` exist but the
card does not display them.)

**Must be added to the pipeline:**
- **`resets`** — not in presence. It is frontend state (`src/lib/resets.js`). Add it to the
  frontend `frame()` (`src/lib/presence.js`), to `PresenceFrame`, and pass it through
  `PresenceHub.update` into `PresenceEntry`. No engine change.
- **PB for the current course** — server enrichment. In `PresenceHub.update`, when `course` is
  known, look up the player's PB from the server PB cache **keyed on the course slug** (reuse the
  server's existing slugify so apostrophe courses like "Wario's Galleon" match) and set
  `pb_ms` + `pb_str` on the entry. The finished delta (`final_time − pb`) is computed client-side.
- **Live timer** — *not wired* (the engine does not read the on-screen clock). While racing the time
  slot simply shows **`—`** (dashes); `final_time` fills it once the run finishes. A real live timer
  is future engine work — no interim/client-side ticking.

**Offline clears state.** `PresenceHub.setOffline` keeps its current behaviour — null every live
field. The offline card shows only the greyscale figure, the muted name, and a "last seen X" line;
selection rows render `—`. (No last-track retention — not wanted.)

**Derived, not stored:**
- **"last seen"** (offline) — relative format of `now − updated_at` (`just now` / `Xm` / `Xh` /
  `Xd`), where `updated_at` is the go-offline time. A player seeded offline at boot (never seen this
  session) shows plain "offline" with no timestamp.

## State derivation (pure mapping)

A pure function maps `PresenceEntry → view-model`, mirroring `src/lib/discordPayload.js`'s screen
logic so the two stay consistent:
- `!online` → **offline**.
- online & `screen ∈ {CHARACTER_SELECT, KART_SELECT, COURSE_SELECT}` → **setup** (with the matching
  "Choosing …" phrase).
- online & `screen == "RACING"` & no `final_time` → **racing**.
- online & (`screen == "RACING"` with `final_time`, or `POST_TIME_TRIAL`) → **finished**.
- online, anything else (TITLE/MENU/GHOST/…) → **menus** (renders like setup with an "In the menus"
  primary line, no selection-in-progress emphasis).

## Figure assets pipeline

Source gifs are the high-res 360×360 RGBA set the user supplied (player+action named, e.g.
`paulPosted.gif`). They are **full-frame** (GIF disposal 0 — frames must be read **standalone**, not
composited, or late frames ghost).

- **Source:** move the chosen gifs into `assets/player_gifs/` (committed via Git LFS, matching the
  `captures/**` LFS convention).
- **Prep script** `scripts/gen_player_figures.py` (Pillow): for each rostered player, take the **end**
  frame (~88%) of its **online** gif and the **start** frame (first frame with >10% opaque pixels) of
  its **offline** gif, crop each to its alpha bounding box, resize to ~260px tall, write transparent
  PNGs to `src/assets/players/<name>__on.png` (end of online gif) and `__off.png` (start of offline
  gif). Greyscale is applied in CSS at render time, not baked in.
- **Mapping** — `name → { online_gif (end frame), offline_gif (start frame) }`:
  | Player | online (end) | offline (start) |
  |---|---|---|
  | Paul | `paulPosted` | `paulPosted` |
  | Aliias | `aliiasPosted` | `aliiasBird` |
  | Luke | `lukePosted` | `lukeThumbsUp` |
  | Adymer | `adymerPosted` | `adymerPosted` |
  | Alex | `adymerPosted` (borrowed) | `adymerPosted` (borrowed) |

  A player with no mapping → a neutral silhouette placeholder.
- The Svelte card imports the generated PNGs (vite bundles them), keyed by player name (lowercased).

## Components & files

- `src/lib/playerCard.js` — **pure**, unit-tested: `viewModel(entry, now)` (state + fields),
  `lapSegments(completion, totLap)`, `lastSeen(ms)`, `pbDelta(finalStr, pbMs)`.
- `src/components/PlayerCard.svelte` — renders one card from a view-model; all the locked CSS.
- `src/components/PlayerPanel.svelte` — the band: subscribes to `presence`, renders one `PlayerCard`
  per roster entry **in roster order** (stable), as a CSS grid of N equal columns.
- `src/App.svelte` — replace the `.player-band` placeholder with `<PlayerPanel/>`.
- `src/lib/presence.js` — add `resets` to `frame()`.
- `pi/src/presence/hub.ts` — `PresenceFrame` += `resets`; `PresenceEntry` += `resets, pb_ms, pb_str`;
  `update()` passes resets through and enriches PB from the cache. `setOffline()` unchanged (clears all).
- `scripts/gen_player_figures.py` + `assets/player_gifs/` (LFS) + `src/assets/players/*.png`.

## Error / edge handling

- Missing `color` → neutral grey spine/name.
- Missing figure PNG → neutral silhouette placeholder (card still renders).
- Unknown course or no PB → PB line hidden.
- `tot_lap` unknown → default to **3** segments (MKW standard) so the bar still reads.
- Racing time always shows `—` (engine timer not wired); only `final_time` ever populates it.
- Roster larger/smaller than 5 → grid uses N columns; cards keep min width and the band scrolls only
  if absurdly many (not expected).

## Testing

- **vitest** (`src/lib/playerCard.test.js`): state derivation for each state; `lapSegments` (e.g.
  0.63 over 3 laps → [full, ~0.89, 0]); `lastSeen` buckets; `pbDelta` sign/format.
- **svelte-check** 0/0 and **vite build** pass.
- Server: extend `pi/src/presence/hub.test.ts` for `resets` passthrough + PB enrichment.
- **Manual:** mock the `presence` store with all four states and confirm the band renders 5
  consistent cards at true scale.

## Out of scope / future

- **Real in-game timer** (engine reads the on-screen clock) — until then the racing time shows `—`.
- Displaying coins/mushrooms/costume.
- Alex's own figure art (borrows Adymer's for now).
- Online-first / rank sorting (roster order for v1).
