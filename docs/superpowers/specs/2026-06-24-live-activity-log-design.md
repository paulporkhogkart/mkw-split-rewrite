# Live Activity Log — Design Spec

**Date:** 2026-06-24
**Status:** Design approved (visual locked via brainstorming companion `final-ui` mockup); pending spec review → implementation plan.
**Surface:** `web/` (thekartoff.com `/live` page) + `pi/` server + a small `PlayerCard` change.

## Goal

The `/live` page currently shows only the player-card wall and feels bare. Add a **live-updating activity log** beneath the cards: a streaming, human-readable history of what the competitors are doing — PBs, leaderboard moves, turf (territory/dominance) changes, world records, grind sessions, and menu/screen activity. New events arrive at the top and push older ones down.

Most of the underlying data already exists server-side (`runs`, `run_laps`, `screen_intervals`, `world_records`, the fire model). The work is **curation + presentation**: compute readable events server-side, store them, and render a calm, scannable feed.

This spec has three parts: **A** the player-card change, **B** the activity-log UI/event model, **C** the plumbing.

---

## Part A — Player-card change (the live "counting" state)

The card's **RESETS** line (`PlayerCard.svelte:84`, the `.foot` label+number) is replaced by a **live activity line**. Rationale: the separate "Now" band we considered is redundant with the cards, so the live counting state lives *on* the card.

| Card state | Activity line |
|---|---|
| Racing | `41 attempts · 14:03` — attempts this session · session timer (ticks) |
| Choosing character / kart / track | `choosing character · 0:40` — activity label · time on screen |
| In the menus | `in the menus · 2:34` |
| Watching a ghost | `watching a ghost · 1:10` |

- For racing, the big race clock (`.prim.time`) and lap bar are unchanged; the activity line sits where RESETS was.
- The card **keeps its LiveSplit green/red pace delta** — it is a live racing instrument, the one place we do *not* follow the log's neutral-delta rule.
- Card NAME stays uppercase (existing shipped style).

**Data needed (new):** the card is fed by presence, which today carries `resets` and the race clock but not session attempts or a per-screen start time. Presence gains:
- `session_attempts` — count of race attempts in the current contiguous session on the current course.
- `screen_since_ms` (or equivalent) — when the current screen/activity began, so the card can tick "time on screen" / session duration.

These are computed server-side from `runs` + `screen_intervals` and attached in the presence hub (see Part C).

**Session boundary:** a session is the contiguous stretch on one course's race context — it starts when the player enters that course's racing/setup loop and ends on a course change, an extended non-race detour, or going offline. The card's `session_attempts` is the **running total for the whole session** (it does *not* reset at a mid-session PB). The log's attempts rows (Part B) split that same run of attempts into **segments** at each interrupting event, so the two counts differ by design: the card is the live aggregate, the log is the historical breakdown.

---

## Part B — The activity log

### B.1 Visual grammar

- **Four columns, every row:** `when · who · where · what`. Each row is **fully self-contained** (restates its own time + track) because it is a streaming line-by-line log and rows interleave across players.
- **Colour = identity only.** The only hues are **player names** (player colour) and the **PB row's left colour strip**. Every delta/gap is neutral grey; times are bright (`--tx`) but uncoloured.
- **Restraint:** flat band on `--panel`, hairline dividers (`--bdHair`), no rounding/shadow — matches the player-card language. Segoe UI, `tabular-nums`. Tokens from `src/theme.css` / `src/lib/palette.js`.
- Newest on top; new rows prepend. Reference mockup: brainstorm companion `final-ui`.

### B.2 Event types

**Player rows** — the `who` column is the player's name in their colour.

| Type | `where` | `what` | Notes |
|---|---|---|---|
| **PB** | course | `PB 1:47.980 (-0.430)` | The **only** row with a player-colour left strip. Delta is signed, 3-decimal, neutral (matches `playerCard.js:signedText`). |
| **Attempts** (grind segment) | course | `19 attempts · 7m` | One row per contiguous run of attempts, closed by any interrupting event. No PB-count baked in (PBs are their own rows). |
| **Off-track** (per-screen) | `character select` / `kart select` / `track select` / `watching a ghost` / `menus` | dwell e.g. `40s` | **One row per screen interval, no floor** — every interval logs, flickers included. Dimmed (`--txDim` track), least eventful. |

**System rows** — the `who` column is a small grey uppercase tag (`RANK` / `TURF` / `WR`); player names appear **coloured inside the description**.

| Type | `where` | `what` |
|---|---|---|
| **RANK** | course | `Paul took 2nd from Aliias · 1:56.420 (+1.118)` — one row per position gained. Shows the rival's PB and the **gap** (rival's deficit to the mover's new PB, neutral, `+`). |
| **TURF claimed** | course | `Gub claimed Paul's turf` — no numbers. |
| **TURF caught fire** | course | `the people are rallying behind Gub` |
| **TURF wavering** | course | `the people are losing faith in Aliias` |
| **WR** | course | `1:29.180 (-0.220) by Ralph` — record time, neutral delta (how far it dropped), holder + "by" dimmed. |

Wording rule: the player **acted upon** falls later in the sentence (`took 1st from **Paul**`, `claimed **Paul**'s turf`, `losing faith in **Aliias**`); the actor/beneficiary leads (`**Gub** claimed…`, `rallying behind **Gub**`). The wavering/fire pair is deliberately matched ("losing faith in" ↔ "rallying behind").

### B.3 Cascades (time-order; newest-on-top, so reversed in the feed)

Events arrive as causal cascades. "Then" = emission/time order.

- **A PB:** `PB → RANK adjustments → TURF adjustments (claim, then fire)`.
  Read a burst **bottom-up**: the PB lands, then each place is taken one at a time (gaps tightening as the player reaches closer rivals), then the turf claim if it took #1, then the catch-fire line if the lead clears the on-fire bar.
- **A WR:** `WR → TURF adjustments` (a faster WR raises the on-fire bar and can snuff a leader's fire → wavering).
- **Only the mover's side is logged.** A player dropping a place appears as "took … from X" inside the mover's burst — never a duplicate "you dropped" row.

### B.4 Turf state semantics (the fire model)

Turf = territory + dominance, all driven by `web/src/lib/fireModel.js`:
- A course is **on fire** when its leader's lead over #2 clears an exponential bar that rises the further the leader's PB sits off the **WR** (`isOnFire`: `leadPct ≥ E0·e^(offPct/K)`, `E0=0.2`, `K=4`).
- **caught fire** = on-fire crosses false→true · **wavering** = true→false while the player still owns #1 (e.g. a WR raised the bar, or a challenger closed in without overtaking) · **claimed** = #1 ownership changes hands.

---

## Part C — Plumbing

All computed **server-side in `pi/`** and **persisted** (the history never changes, so recomputing per request is wasteful).

### C.1 Derived table — computed once
New `activity_events` table in `pi/mkw.db`: `id, ts, type, player_id, course_id, payload (JSON)`. The `payload` carries type-specific fields (times, deltas, place, rival, holder, screen, dwell, etc.). Events are computed when source data arrives and stored; reads never recompute.

### C.2 Ingestion cascade
- **On a run** (`pi/src/db/ingest.ts` / `api/runs.ts`): emit the **PB** event (if PB) → diff the course leaderboard before/after for the **RANK** ladder → recompute ownership + on-fire for that course → **TURF** events. Close out the player's **attempts** segment if the run interrupts/ends it.
- **On a WR** (`pi/src/wr/scheduler.ts`): recompute on-fire for the affected course → **TURF** events.
- **On `screen_intervals`** (`pi/src/api/screen.ts`): emit one **off-track** event per interval (no floor).

### C.3 Per-course leaderboard cache
Maintain an in-memory per-course leaderboard (ordered best-PB-per-player), **seeded from the DB at boot, updated incrementally on each PB**. Gives:
- the cheap **before/after diff** for RANK events,
- the **top-2** for TURF / on-fire,
without re-sorting `runs` per call (today `leaderboardAt`/`territoryOwners` recompute on demand). Also speeds live turf/fire.

### C.4 Fire/turf logic ported server-side
`fireModel.js` (`isOnFire`, `fireBarPct`, `snuffLeadMs`) lives in `web/` today. Port it to `pi/` (TS) so turf transitions are detected on the server — one source of truth, still shared in spirit with the map's flames. Keep the constants identical (`E0`, `K`).

### C.5 One-time backfill
A migration replays all historical runs in time order (reusing the same leaderboard/timeline replay the territory map already does — `leaderboardAt`, `territoryTimeline`) to populate `activity_events` with history. Backfill must be deterministic (same input → same events) for re-runnability.

### C.6 Delivery
- **History:** `GET /v1/activity` (paginated, newest-first) — the page loads it on open and infinite-scrolls.
- **Live:** new activity events are pushed over a WS channel as they're computed. **Additive** — it does not alter the existing `/v1/events` stream the Discord bot consumes.
- **Retention:** keep every event forever; page lazily; no cap.

### C.7 Frontend
- New `web/src/ActivityLog.svelte` rendered under the `CardWall` (`web/src/CardWall.svelte`, add a full-width sibling below `.wall`).
- An `activity` store: fetch history via the api client (`web/src/lib/api.js`) + subscribe to the live channel; prepend new events.
- Row formatting helpers (event → `{when, who, where, what}`), kept pure + unit-testable.

---

## Out of scope / deferred

- **Character/kart icons** in log rows — dropped for now (they live as text on the card); can return later as real icons in the `where`/hover.
- **Territory page → "Turf" rename** (`#/territory` → `#/turf` + navbar) — tracked separately, see memory `turf-rename`. The log already uses the `TURF` vocabulary.
- **Gap colour** finalised as neutral; revisitable.

## Testing

- **pi:** unit tests for the cascade (PB → RANK ladder → TURF), on-fire transitions (caught/wavering/claimed incl. WR-snuff), the leaderboard cache (incremental == full recompute), and backfill determinism. Reuse vitest.
- **web:** `ActivityLog` rendering + event→row formatting (wording snapshots, neutral deltas, name colours), store prepend/pagination.
- Guard: no green/red deltas in the log (colour only on names + PB strip).

## Files touched (anticipated)

- `pi/`: `activity_events` migration; ingestion hook in `db/ingest.ts` + `api/runs.ts`; `wr/scheduler.ts` hook; `api/screen.ts` hook; ported `fireModel` (TS); per-course leaderboard cache; `GET /v1/activity` + live channel; backfill migration.
- `pi/` presence (`presence/hub.ts`): add `session_attempts` + `screen_since_ms`.
- `web/`: `ActivityLog.svelte`, `activity` store + formatter lib, `CardWall.svelte` insertion, `lib/api.js` endpoint.
- `src/components/PlayerCard.svelte`: RESETS → activity line; `src/lib/playerCard.js`: view-model fields for attempts/timer.
