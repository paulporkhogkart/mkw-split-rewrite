# Activity Feed — Presence-Driven Sessions (Redesign)

**Date:** 2026-06-24
**Supersedes the activity-*session* half of:** `2026-06-24-live-activity-log-design.md`
(the milestone cascade — PB / rank / turf / WR — from that spec is unchanged and out of scope here).

## Problem

The shipped activity feed has four user-confirmed defects, all from one root cause —
**the feed is assembled from already-closed batches that don't know the player-card screen model:**

1. **Menus mislabelled.** `activity/screens.ts:labelFor` maps every screen it doesn't
   explicitly recognise to `'menus'`, so `RACE_MENU`, `RESET`, `UNKNOWN_RACE_ACTIVE`
   (which the cards deliberately treat as *part of racing*) all surface as "menus."
2. **Fragmented flood.** Each raw `screen_intervals` row becomes its own `screen`
   activity event — no aggregation.
3. **Emit-at-end.** Both the grind (`/v1/runs` → `GrindTracker`) and the screen feed
   (`/v1/screen-intervals`) only emit once a segment has *closed*, so an activity never
   appears until the player leaves it ("doesn't show till I change screen").
4. **Broken durations.** The grind's `durationMs = lastUpload − firstUpload`, which is
   `0` for a single race → "1 attempt · 0s".

## Goal

Drive the feed's **session activities** (racing/grind, menus, character/kart/track select,
watching a ghost) off the live presence stream, using the **same screen classification the
player cards use**, emitting each session **at its start**, ticking its duration **live**, and
**finalising it in place** when the player leaves it.

## Non-Goals

- The instantaneous milestone cascade (PB / rank ladder / turf claim·fire·waver / WR) is
  already correct and stays exactly as-is — **except** the grind's attempt count is removed
  from the PB cascade (the racing session owns it now; see §5).
- The player **card** redesign (RESETS → `attempts · timer`) is deferred (the user's "fix
  feed first"). Out of scope.
- No historical backfill of sessions. Sessions are derived from live presence, which has no
  history; session rows accrue going forward only. The milestone backfill is untouched.

---

## Architecture

### 1. Screen classification (shared with the card)

The canonical screen model lives in `src/lib/playerCard.js` (frontend). Port its sets into a
**server-side classifier** `pi/src/activity/screenClass.ts`, with a **parity test** that pins
the server sets against a table copied from `playerCard.js` (a `// keep in sync with
src/lib/playerCard.js` comment marks both sides; the JS/TS package boundary rules out a direct
import, so the test is the drift guard).

A frame's `screen` (+ whether a racing context is currently open for the player) classifies to
one of:

| Class | Screens | Notes |
|-------|---------|-------|
| `racing` | `RACING` | keyed by **course**; the grind/attempts session (a finished race continues it via `POST_TIME_TRIAL` until the player leaves) |
| *(held)* | `RACE_MENU`, `HOME`, `RESET`, `GHOST_RESET`, `UNKNOWN_RESET`, `UNKNOWN_RACE_ACTIVE`, `PHOTO_MODE`, `EXIT_PHOTO_MODE`, `POST_TIME_TRIAL` | **continues** an open racing session; if none is open, classifies as `menus` (mirrors the card's `holds`-gated `HOLD_SCREENS` plus its `inRaceCtx` results state) |
| `character_select` | `CHARACTER_SELECT` | |
| `kart_select` | `KART_SELECT` | |
| `track_select` | `COURSE_SELECT` | |
| `ghost` | `GHOST`, `START_REPLAY`, `REPLAY_MENU` | "watching a ghost" |
| `menus` | everything else (incl. `MAIN_MENU`, `START_TIME_TRIAL`, and any unrecognised screen) | the aggregate bucket |

The "held continues racing" rule is **stateful**: the classifier is a method on the tracker
that reads the player's current open-session class. This is the exact behaviour the card's
`HOLD_SCREENS`/`holds` map encodes — a pause or reset mid-grind does not fragment the session,
but a pause reached from a cold menu is just menus.

### 2. The `SessionTracker`

A new in-memory `pi/src/activity/sessionTracker.ts`, owned by `server.ts`, hooked into the
presence hub. One **open session** per player at a time.

**Inputs:**
- `onFrame(playerId, { screen, course, character, costume, finalTime, dnf, invalidated }, now)`
  — called from `PresenceHub.update()` each frame (~4 Hz).
- `onOffline(playerId, now)` — called from `PresenceHub.setOffline()` (and `sweep`).
- `noteRun(playerId, courseId, now)` — called from `/v1/runs` on every attempt (incl. resets).
- `notePb(playerId, courseId, now)` — called from `/v1/runs` when that attempt was a PB.

**Per-frame logic:**
1. Classify the frame (§1) → `nextClass` (+ `courseId` when `racing`).
2. Compute the session **key**: `racing` → `racing:<courseId>`; else the class name. A change
   of key = a transition.
3. **Debounce flicker:** a differing key must persist for `STABLE_MS` (≈1.2 s) before it
   commits as a transition; transient single-frame blips are ignored and the current session
   holds. (The held-screen rule already absorbs the common mid-race case; this covers
   detection noise on the menu side.)
4. On a committed transition: **finalise** the open session (§3), then **open** the new one
   with `started_ts = now` (the timestamp of the first frame of the new stable key) and emit it
   open (§4).

**Run coordination:**
- `noteRun`: if an open `racing:<courseId>` session matches, increment its `attempts`. If no
  racing session is open for that course (presence lag), open one now (`started_ts = now`,
  `attempts = 1`) and emit it open.
- `notePb`: increment the matching racing session's `pbs` (used for the finalised outcome).

**Offline / sweep:** finalise the open session at `now`.

### 3. Session shape

```ts
interface Session {
  sessionId: number;        // server monotonic, stable across open→final
  playerId: number;
  class: 'racing' | 'menus' | 'character_select' | 'kart_select' | 'track_select' | 'ghost';
  courseId: number | null;  // racing only
  character: string | null; // racing only (label "as Peach")
  costume: string | null;   // racing only
  startedTs: number;
  attempts: number;         // racing only; count of run POSTs in the session
  pbs: number;              // racing only; PBs landed during the session (0 ⇒ "no PB")
}
```

A session is **finalised** by stamping `endedTs = now` (duration = `endedTs − startedTs`) and:
- persisting it to `activity_events` (so it survives restart and appears in history) as a
  `session` event whose payload carries `class, course?, character?, costume?, started_ts,
  ended_ts, duration_ms, attempts?, pbs?` and `course_id`/`player_id` columns set;
- emitting a `final` message (§4).

**Drop rule:** the `STABLE_MS` debounce already absorbs sub-second blips into the prior session
(they never open a row), so the only finalise that's dropped is a **racing session with
`attempts === 0`** — a player who entered `RACING` but completed no attempt (no finish/reset
POST) before leaving. It has nothing to report, so a `drop` message retracts its open row
instead of persisting "raced 0 times". Every session that opens is otherwise kept.

### 4. Live protocol

Sessions ride the **existing `/v1/activity/stream`** WebSocket and `ActivityHub`, alongside
milestone events, as a tagged message:

```ts
type ActivityStreamMsg =
  | { kind: 'event';   event: ActivityEvent }                 // milestone (unchanged)
  | { kind: 'session'; state: 'open'|'final'|'drop'; session: SessionView };

interface SessionView {
  session_id: number; state: 'open'|'final';
  player: { id; name; color } | null;
  course: { slug; name } | null;
  cls: Session['class'];
  character: string | null; costume: string | null;
  started_ts: number; ended_ts: number | null;
  attempts: number | null; pbs: number | null;
}
```

- **open**: client inserts/updates a row keyed `sess:<session_id>`, renders it **ticking**
  (`now − started_ts`, client-side, on CardWall's shared clock — no server duration spam).
- **final**: same key, `ended_ts` set → duration locks, racing outcome (`attempts`, `pbs`)
  shown. The row stays at its `started_ts` position.
- **drop**: client removes `sess:<session_id>`.

**Late joiners / page load:**
- The activity-stream WS sends, on connect, an **open-session snapshot**
  `{ kind: 'sessions_snapshot', sessions: SessionView[] }` (like the presence snapshot) so a
  fresh client immediately sees in-flight ticking sessions.
- REST `/v1/activity` returns finalised sessions (persisted `session` events) interleaved with
  milestones, newest-first — unchanged query shape; the client keys persisted session rows
  `evt:<id>` and renders the locked duration from the payload. (A given session is only ever
  open-in-memory **or** finalised-in-DB, so no client sees both keys for it.)

**Ordering:** the feed sorts **all** rows — open sessions, finalised sessions, milestones — by
their feed timestamp **descending**. A session's feed timestamp is its `started_ts`; a
milestone's is its `ts`. Open sessions therefore tick **in their chronological start position**
(a long-running grind that started 20 min ago sits below a menus blip that started 1 min ago —
matching "the log is sent at the start").

### 5. Decoupling attempts from the PB cascade

`buildRunCascade` currently embeds the grind's `attempts` in the PB event. Remove it — the
racing **session** owns attempts now. The PB cascade becomes pb + rank + turf only. Delete the
old emit-at-end grind path in `runs.ts` (the `closedByMove` attempts event and the
`tracker.close()` attempts-in-cascade) and the `GrindTracker` class; replace with
`sessionTracker.noteRun` / `notePb` calls.

### 6. Screen-interval ingest

`/v1/screen-intervals` **keeps** `insertScreenIntervals` (the `screen_intervals` table still
feeds the `/v1/stats` screen-time metric and the Discord bot). It **stops** calling
`screenActivityInputs` / `commitActivity` — the feed no longer derives activities from batched
intervals. `activity/screens.ts` (`SCREEN_LABELS`/`labelFor`/`screenActivityInputs`) and its
test are deleted.

### 7. Frontend

- `web/src/activityClient.js` + `src/lib/stores.js` activity store: handle the three session
  message states + the snapshot, keyed as above; keep milestone handling.
- `web/src/lib/activityFormat.js`: format session rows under the **locked four-column grammar**
  (`when · who · where · what`, colour reserved for player names) from the prior spec:
  - `racing` open → `racing` + ticking `m:ss` + `· N` (attempts so far); final →
    `raced {course} as {costume? }{character} · {N} · {mm:ss} · {pbs>0 ? "new PB"×n : "no PB"}`.
  - `menus` → `in the menus · {dur}`.
  - `character_select`/`kart_select`/`track_select` → `choosing a character|kart|track · {dur}`.
  - `ghost` → `watching a ghost · {dur}`.
  - Course/character/kart rendered as the existing **icon placeholders**.
- Re-add `<ActivityLog>` to `web/src/CardWall.svelte` (removed during the perf diagnosis).

### 8. Constants

| Name | Value | Meaning |
|------|-------|---------|
| `STABLE_MS` | 1200 | a new screen-class must persist this long to commit a transition |

(Lives in `sessionTracker.ts`; tune later if needed.)

---

## Edge cases

- **Course change while racing** (rare; normally via a menu): the `racing:<courseId>` key
  changes → finalise the old racing session, open the new.
- **Photo Mode / GameChat / invalidated mid-race:** held screens → the racing session
  continues; an invalidated run is a dead *run* but the player is still at the course, so the
  session runs until they actually leave. No PB is recorded (`notePb` is only called on real
  PBs).
- **DNF:** terminal run, but the player is still at the course (`POST_TIME_TRIAL`/held) → the
  session finalises when they leave, like any racing session.
- **Player goes offline mid-session:** `onOffline` finalises at `now`.
- **Server restart mid-session:** open sessions are in-memory and lost; the next presence
  frames re-open fresh sessions. Finalised sessions already persisted in `activity_events` are
  unaffected. Acceptable.
- **Run POST before presence flips to RACING:** `noteRun` opens the racing session only when
  presence corroborates (it is mid-debounce toward racing this course) or there is no presence
  state yet; presence then continues it via the matching key. A stray / late / ghost POST that
  lands while presence shows a committed non-racing session is **ignored**, so it can't
  manufacture a phantom ~0s racing row. A run while already racing a *different* course switches
  the session (a course change presence is catching up on).

## Testing

- **Parity test:** server `screenClass.ts` sets match the pinned `playerCard.js` table (sync-comment guard).
- **Classifier unit tests:** each screen → expected class, including the stateful held rule
  (held continues racing; held from cold = menus).
- **SessionTracker unit tests** (inject `now`): open-at-start; tick (open emitted before
  finalise); transition finalises + opens; debounce swallows a one-frame blip; `noteRun`
  increments / opens-on-lag; `notePb` sets outcome; racing `attempts === 0` drop + `drop`
  message; offline finalises; course-change finalises+opens.
- **Frontend unit tests:** open row ticks (duration from `now − started_ts`); final locks;
  drop removes; ordering by feed timestamp; the format strings per class.
- **Runs integration:** a PB no longer carries `attempts` in its cascade; `noteRun`/`notePb`
  are invoked.

## Files

- **Create:** `pi/src/activity/screenClass.ts` (+ `.test.ts`), `pi/src/activity/sessionTracker.ts` (+ `.test.ts`).
- **Modify:** `pi/src/presence/hub.ts` (call `onFrame`/`onOffline`), `pi/src/server.ts` (wire tracker
  into presence + activity hub + the snapshot on stream connect), `pi/src/api/app.ts`
  (session snapshot on `/v1/activity/stream` connect), `pi/src/api/runs.ts` (`noteRun`/`notePb`,
  drop old grind), `pi/src/activity/cascade.ts` (drop `attempts`), `pi/src/api/screen.ts` (drop
  activity generation), `pi/src/db/activity.ts` (persist/read `session` events),
  `web/src/activityClient.js`, `web/src/lib/activityFormat.js`, `web/src/CardWall.svelte`,
  `src/lib/stores.js`.
- **Delete:** `pi/src/activity/screens.ts` (+ `.test.ts`), `pi/src/activity/grind.ts` (+ `.test.ts`).
</content>
</invoke>
