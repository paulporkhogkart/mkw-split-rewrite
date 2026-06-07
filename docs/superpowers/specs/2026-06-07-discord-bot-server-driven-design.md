# Discord bot (server-driven) — Design

- **Date:** 2026-06-07
- **Status:** approved (brainstorm), pending implementation plan
- **Relates to:** [[client-server-shift]] sub-project **C (broadcast/website)** — this is the broadcast half. Consumes the server's `pb_achieved` + `wr_update` events ([[wr-scraper-done]] for WR; [[run-review-gated-upload]] for the server-authoritative PB cache). Replaces the legacy bot's Google-Sheets sync + mkwrs scraping (the server scrapes now).

## Goal

Re-create the legacy Discord bot (`legacy/mkwpb2/kart-off/services/discord_bot.py`) — whose embeds the user likes — as a **TypeScript / discord.js** bot in `pi/src/bot/`, driven by the new server instead of Google Sheets. It announces personal bests and world records as embeds, and serves the `/leaderboard`, `/nemesis`, `/wr` slash commands. It runs on the Pi as a **separate process** from the server.

The legacy embeds are fed by peewee models (`PersonalBest.update_pb` / `WorldRecord.update_wr`) that computed rich fields (overtaken, positions, still-ahead, reign, track-record detection) against a database that no longer exists. The server's events are lean, so the bot **recomputes** those fields from the shared server DB. The embed *look* is preserved 1:1.

## Non-goals

- **No server changes.** The bot is purely additive: it consumes the existing `/v1/events` WebSocket and reads the existing `mkw.db`. No new HTTP endpoints, no event-payload changes.
- Not announcing `run_started` / `run_finished` / `lead_change` / `wr_beaten` (only `pb_achieved` + `wr_update`, matching the legacy bot's PB/WR scope).
- No website (the rest of sub-project C).
- No catch-up/replay of events missed while the bot is disconnected (events are best-effort; see Error handling).

## Decisions (from brainstorm)

1. **Language: TypeScript / discord.js.** The data layer must be rewritten regardless (the peewee models are gone), so "keep Python" would only save the embed-rendering code. TS wins structurally: it imports the server's `ServerEvent` type (compiler-enforced contract), runs in the one Node runtime already on the Pi, and uses the `pi/` vitest tooling to snapshot-test embed output against drift. Only real cost — porting the formatting helpers — is mechanical and test-guarded.
2. **Scope: full parity** — PB + WR announcement embeds **and** all three slash commands.
3. **Events via WebSocket; reads via the shared DB.** See below.
4. **PB title:** `"<NAME> PERSONAL BEST"`, name uppercased to match the sibling titles (e.g. `PAUL PERSONAL BEST`).

## Improvements over the legacy bot (design review)

This is a deliberate redesign, not a transcription. The **embed output is held identical** to the legacy bot (locked by snapshot tests — see Testing); everything behind the embeds is improved:

- **No N+1 leaderboard queries.** Legacy `get_track_leaderboard` ran one query per player and `get_total_leaderboard` called it per track (O(tracks×players) queries). Replaced by single-SQL `courseLeaderboard` / `overallLeaderboard`.
- **Reign in one pass** instead of rebuilding a historical leaderboard per PB (see Reign above).
- **Separation of concerns.** The legacy ~1050-line `DiscordBot` god-object (gateway + events + rendering + formatting + commands + reign + video lookup) is split into focused, individually-testable modules; rendering (`embeds/`) is pure and decoupled from fetching (`enrich.ts`).
- **No magic numbers / no crashes.** Drops the `previous_ms = 5459999` "no previous PB" hack (null-handled) and the `THUMBNAIL_GIFS[name]` KeyError (defensive `gifFor`).
- **Resilience.** Adds backoff WS reconnect + pre-ready buffering.
- **Deferred to Stage 2:** factor the duplicated decimal-column alignment shared by the four leaderboard/overtaken/nemesis formatters into one helper.

The one intentional *content* change in an embed: the WR embed's **DELTA** field shows the real improvement (the `wr_update` event carries `improvement_ms`, which the legacy sheet-driven path lacked, so it usually printed "First WR"). Same field, same formatting — better data. Flagged for the user; easy to revert to always-"First WR" if undesired.

## Architecture

```
server process (npm run dev)            bot process (npm run bot)
  Hono + WS /v1/events  ───events───▶   WS client ──▶ dispatch ──▶ embeds ──▶ Discord
  writes mkw.db (WAL)                    reads  mkw.db (shared file, read-only access)
                                         discord.js client + slash commands
```

- **Events:** the bot is a WebSocket **client** of the existing `/v1/events` broadcast (`ws://127.0.0.1:<PORT>/v1/events`). It uses Node's built-in global `WebSocket` (Node ≥22; no `ws` dependency). It imports `ServerEvent` from `pi/src/db/types.ts`, so the union it switches on is exactly what the server publishes.
- **Reads:** the bot opens the **same `mkw.db`** the server uses and reuses/extends the `pi/src/db/` query functions. WAL is on (`connect.ts`), so a concurrent reader is safe alongside the server's writes. The bot performs **only reads** (never writes, never `applySchema`).

### Why shared-DB reads (the considered alternatives)

- **HTTP read API (rejected as primary):** the rich embed fields and the slash commands need *reign* and *historical-leaderboard* data the HTTP API doesn't expose — going HTTP would force bot-specific endpoints onto the server. `/nemesis` over HTTP would be ~30 `/v1/leaderboard` round-trips per invocation (one per course); one SQL query against the shared DB instead.
- **Enrich the server's events (rejected):** bloats event payloads with presentation concerns and still can't serve the slash commands.
- **Shared-DB reads (chosen):** intra-package reuse (same repo, same TS `db/` modules), no new public surface, reign/history feasible, cheap. Coupling is to our own schema, in our own package.

## Event → enrich → embed pipeline

On each consumed event the bot assembles the legacy data object from `{event fields} + {DB reads}`, then renders the embed.

### `pb_achieved` → green PB embed (`color 0x6cca5f`)

Event: `{ player, course, cc, total_time, delta_vs_prev_ms, rank }`.

| Embed element | Source |
|---|---|
| **title** | `is_new_track_record`/reign logic (below); the non-record branch is `"<PLAYER> PERSONAL BEST"` |
| **thumbnail** | `THUMBNAIL_GIFS[player]` (random), **guarded** — no thumbnail if the player has none |
| **TRACK** | `course` resolved to course `display_name` (slugify → `courses`) |
| **TIME** | `total_time` |
| **DELTA** | format of `delta_vs_prev_ms` (negative = improvement) |
| **OVERTOOK** | players whose current PB time is between `prev_ms` and `new_ms`, plus `WR` if `new_ms < wr.record_ms` |
| **POSITION** | track old→new and overall old→new |
| **footer** | still-ahead: the player at `rank-1`, else the WR if `rank==1` but slower than WR |

Reconstruction keys:
- `new_ms = timeToMs(total_time)`; `prev_ms = new_ms - delta_vs_prev_ms` (delta = new − prev).
- **new track position** = `rank` (from event); **old track position** = where `prev_ms` slots into the current course leaderboard with this player removed.
- **overall (total) positions:** new from `overallLeaderboard`; old by recomputing this player's overall sum using `prev_ms` on this course.
- **is_new_track_record** = this PB now holds course rank 1 and didn't before (ported faithfully from `update_pb`: no prior leaderboard, or `new_ms < old_leader_ms`).
- **reign_info** (only when `is_new_track_record`) from the ported reign query.

### `wr_update` → grey WR embed (`color 0xf3f3f3`)

Event: `{ course (verbatim mkwrs name), cc, holder, total_time, prev_holder, prev_time, improvement_ms, character, vehicle, video_url }`.

| Embed element | Source |
|---|---|
| **title** | `"WORLD RECORD BY <HOLDER>"` |
| **TRACK** | `course` resolved via `mkwrsNameToSlug`/`resolveCourseId` → `display_name` (memory caveat: event carries the **verbatim mkwrs name**, key on the slug) |
| **TIME** | `total_time` |
| **DELTA** | format of `improvement_ms`, else `"First WR"` |
| **footer** | WR reign: `"THE <DUR> REIGN OF <PREV_HOLDER> IS OVER/CONTINUES"` |

The WR embed has **no thumbnail** in the legacy bot, so non-friend mkwrs holders need no GIF.

### Reign (PB and WR)

Reign is recomputed with **graceful degradation** (no duration when timestamps are missing, never an error):
- **WR reign:** walk `world_records` for `(course_id, cc)` ordered by `achieved_at` desc from the current row; reign start = earliest contiguous row with the same `holder_name`; duration = `now − reign_start`.
- **PB / track-record reign:** a best-times leaderboard is **monotonic** (PBs only improve), so a player's reign = *the time since the lead last changed to them*. One forward pass over the course's finished runs (ordered by `ended_at`), tracking each player's running best and resetting the reign start whenever the overall leader changes. This replaces the legacy `_get_leaderboard_at_time` approach (which rebuilt the whole leaderboard per PB, O(PBs×players) queries) with a single query + single pass.
- Duration formatting reuses the legacy `_format_duration` buckets (SECOND/MINUTE/HOUR/DAY/MONTH/YEAR).

## Slash commands (full parity)

Rewired to shared-DB reads; autocomplete from the `courses` / `players` tables.

- **`/leaderboard [track]`** — track board (WR line + ranked PBs + gap-to-leader), or overall board (aggregate time + golf points) when no track. Reuses `courseLeaderboard` / `overallLeaderboard` / `currentWr`; reign for the footer.
- **`/wr <track>`** — current WR embed for a track (`currentWr`) + reign footer + the video-fallback behaviour from the legacy `_find_wr_video`.
- **`/nemesis [player]`** — tracks where the caller is furthest behind the leader, or a chosen player; paginated. Ports the pairwise-gap computation as SQL across the season's PBs. Maps the caller's Discord id via `ID_TO_NAME`.

The monospace column-alignment formatters (`_format_track_leaderboard`, `_format_total_leaderboard`, `_format_overtaken`, `_format_positions`, `_format_nemesis_tracks`) are ported to TS verbatim in behaviour.

## Config & identity

- **Secrets via env** (a `pi/.env`, loaded by the bot entry only): `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, optional `DISCORD_GUILD_ID` (guild-scoped command sync is instant vs. global), `MKW_DB` (shared DB path, defaults to the server's), and the server WS URL/port.
- **Non-secret player config committed** in `pi/src/bot/players.config.ts`:
  - `ID_TO_NAME` — Discord user id → player display name (for `/nemesis` "you").
  - `THUMBNAIL_GIFS` — player display name → GIF URL list.
  - Both are looked up **defensively**: an unknown player yields no thumbnail / "not registered", never a crash. (The legacy `THUMBNAIL_GIFS[name]` would `KeyError`.)
- Player display names in events come from the server (`players.display_name`), so they line up with the config keys for the friend group.

## Error handling & lifecycle

- **WS reconnect** with capped backoff; on the server restarting, the bot reconnects and resumes. Events emitted while disconnected are **not** replayed (the hub doesn't persist) — accepted best-effort behaviour for announcements.
- **Pre-ready buffering:** events arriving before the discord.js `ready` event are queued and flushed on ready (as the legacy bot did with `message_queue`).
- Each event handler and command is wrapped so one failure logs and does not take down the process.
- DB read errors degrade the affected field (e.g. missing reign → no footer) rather than dropping the whole embed.

## File layout (`pi/src/bot/`)

```
index.ts            entry: load env, open DB, start WS client + discord client
client.ts           discord.js client, ready/queue, channel send helpers
ws.ts               WS client: connect, backoff-reconnect, parse ServerEvent, dispatch
enrich.ts           assemble PB/WR embed data objects from event + DB reads
embeds/pb.ts        build the green PB EmbedBuilder
embeds/wr.ts        build the grey WR EmbedBuilder
commands/           leaderboard.ts, nemesis.ts, wr.ts (handlers + autocomplete)
format.ts           time-diff strings, duration buckets, monospace column alignment
players.config.ts   ID_TO_NAME + THUMBNAIL_GIFS (committed, non-secret)
```

Shared DB query primitives (reign, historical leaderboard, overtaken/old-position helpers) go in `pi/src/db/` (e.g. `db/reign.ts`) so they're testable and reusable; embed-assembly stays in `bot/`. Reuses existing `db/connect.ts`, `db/reads.ts`, `db/seasons.ts`, `db/slug.ts`, `wr/courses.ts`, `db/types.ts`.

`pi/package.json`: add dependency `discord.js`; add script `"bot": "node --no-warnings --import tsx src/bot/index.ts"`.

## Testing & fidelity guard

- **vitest unit** — `format.ts` (time-diff, duration buckets, column alignment) against cases that lock the legacy behaviour.
- **vitest + seeded DB** — reign / historical-leaderboard / overtaken / old-position queries (the `pi/` suite already seeds DBs, e.g. `reads.test.ts`).
- **Embed snapshot tests** — feed a fixed enriched data object into each embed builder and snapshot the resulting JSON (title / fields / footer / color). This is the primary guard that the TS port matches the legacy look.

## Phasing

Shipped in two stages (separate implementation plans / review checkpoints):

- **Stage 1 — announcements:** scaffolding (`bot/` dir, `discord.js` dep, `npm run bot`, config/env), discord.js client + ready-buffer, WS client + reconnect, the shared-DB read primitives (course/overall leaderboards already exist; add reign + overtaken/old-position), `enrich.ts`, both embed builders, `format.ts`, and the snapshot/format/reign tests. This is the core of the request — PB + WR embeds driven by the server.
- **Stage 2 — slash commands:** `/leaderboard`, `/wr`, `/nemesis` (+ autocomplete) on top of Stage 1's reads and formatters, factoring the shared decimal-column alignment into one helper (the legacy duplicated it across four formatters).

## Operational note

On the Pi, the bot is a second systemd unit (`npm run bot`) alongside the server unit. Same working dir so `MKW_DB` resolves to the one shared file.
