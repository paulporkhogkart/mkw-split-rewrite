# Activity Feed — Presence-Driven Sessions Implementation Plan

> **Execution:** inline (this session), task-by-task with tests, then a final whole-branch
> code review. Spec: `docs/superpowers/specs/2026-06-24-activity-feed-sessions-design.md`.

**Goal:** Replace the emit-at-end, mislabelled activity-session feed with presence-driven
sessions that classify screens like the player card, emit at start, tick live, and finalise in
place.

**Architecture:** A server-side `SessionTracker` fed by the presence hub (`update`/`setOffline`)
and the runs route (`noteRun`/`notePb`). It opens one session per player, debounces screen
flicker, and emits `open`/`final`/`drop` over the existing activity stream; finalised sessions
persist to `activity_events` as `type:'session'`. The client keys sessions, ticks open ones,
and renders the locked duration on finalise. Milestone cascade unchanged (attempts removed).

**Tech Stack:** pi/ (TypeScript, `node:sqlite`, hono, vitest), web/ + src/ (Svelte, vitest).

## Global Constraints

- `STABLE_MS = 1200` (lives in `sessionTracker.ts`; no duration floor — the debounce is the floor).
- Colour is reserved for player names only (carried over from the activity-log spec); no
  em-dashes anywhere in copy.
- Server classifier sets are pinned to `src/lib/playerCard.js` by a parity test with a
  `// keep in sync` comment on both sides (no cross-package import).
- Sessions persist on finalise only; no historical backfill.
- All new pi/ source + tests stay tsc-clean (`npm --prefix pi run typecheck`).

---

### Task 1: Screen classifier (`pi/src/activity/screenClass.ts`)

**Files:** Create `pi/src/activity/screenClass.ts`, `pi/src/activity/screenClass.test.ts`.

**Produces:** `type ScreenClass`, `classify(screen, racingOpen)`, the exported sets.

```ts
// keep in sync with src/lib/playerCard.js (SETUP / HOLD_SCREENS / inRaceCtx)
export type ScreenClass = 'racing' | 'menus' | 'character_select' | 'kart_select' | 'track_select' | 'ghost';

// Screens that CONTINUE an open racing session (pause/reset loaders + the results state).
// = playerCard.js HOLD_SCREENS plus POST_TIME_TRIAL (its inRaceCtx results screen).
export const HELD_SCREENS = new Set<string>([
  'RACE_MENU', 'HOME', 'RESET', 'GHOST_RESET', 'UNKNOWN_RESET',
  'UNKNOWN_RACE_ACTIVE', 'PHOTO_MODE', 'EXIT_PHOTO_MODE', 'POST_TIME_TRIAL',
]);
export const GHOST_SCREENS = new Set<string>(['GHOST', 'START_REPLAY', 'REPLAY_MENU']);

/** Classify a frame's screen. `racingOpen` = the player currently has an open racing session,
 *  which is what lets held screens (a pause mid-grind) continue racing instead of reading as
 *  menus (mirrors the card's holds-gated HOLD_SCREENS). */
export function classify(screen: string | null | undefined, racingOpen: boolean): ScreenClass {
  if (screen === 'RACING') return 'racing';
  if (screen && HELD_SCREENS.has(screen)) return racingOpen ? 'racing' : 'menus';
  if (screen === 'CHARACTER_SELECT') return 'character_select';
  if (screen === 'KART_SELECT') return 'kart_select';
  if (screen === 'COURSE_SELECT') return 'track_select';
  if (screen && GHOST_SCREENS.has(screen)) return 'ghost';
  return 'menus';
}
```

**Tests:** RACING→racing; each select screen→its class; ghost screens→ghost; a held screen with
`racingOpen=true`→racing and with `false`→menus; `MAIN_MENU`/`START_TIME_TRIAL`/`null`/unknown→
menus. **Parity test:** copy the card's `HOLD_SCREENS` and `SETUP` keys into the test as literals
and assert `HELD_SCREENS` ⊇ the card `HOLD_SCREENS`, `POST_TIME_TRIAL ∈ HELD_SCREENS`, and that
every card SETUP key classifies to a non-`menus` class (or, for `START_TIME_TRIAL`, document the
deliberate `menus` mapping). Commit.

---

### Task 2: SessionTracker (`pi/src/activity/sessionTracker.ts`)

**Files:** Create `pi/src/activity/sessionTracker.ts`, `pi/src/activity/sessionTracker.test.ts`.

**Consumes:** `classify` (Task 1), `ActivityInput` (`activity/types.ts`).
**Produces:** `class SessionTracker`, `interface SessionView`, `interface FrameInput`.

```ts
export interface FrameInput { screen: string | null; courseId: number | null;
  character: string | null; costume: string | null; }

export interface SessionView {
  session_id: number; state: 'open' | 'final';
  player_id: number; cls: ScreenClass;
  course_id: number | null; character: string | null; costume: string | null;
  started_ts: number; ended_ts: number | null;
  attempts: number | null; pbs: number | null;   // racing only, else null
}

export interface TrackerDeps {
  now: () => number;
  emitOpen: (s: SessionView) => void;
  emitFinal: (s: SessionView) => void;   // caller persists (Task 4 wires persist+broadcast)
  emitDrop: (sessionId: number) => void;
}
```

**Internal state per player:** `{ session: Session | null; pendingKey: string | null;
pendingSince: number }`, plus a module counter `nextSessionId`.

**Key derivation** (given current open session + frame):
- `racingOpen = session?.cls === 'racing'`.
- `cls = classify(frame.screen, racingOpen)`.
- if `cls === 'racing'`: `courseId = frame.screen === 'RACING' ? frame.courseId
  : session?.courseId ?? frame.courseId` (held screens inherit the open course); `key =
  'racing:' + courseId`.
- else `key = cls`.

**`onFrame(playerId, frame)`** (uses `deps.now()`):
1. Derive `key` (above). If a session is open and `key === currentKey`: clear `pendingKey`;
   for a racing session, refresh `character`/`costume` if currently null and the frame has them;
   return.
2. Else `key` differs (or no session): debounce.
   - if `pendingKey !== key`: `pendingKey = key; pendingSince = now`; return (start timer).
   - if `pendingKey === key && now - pendingSince >= STABLE_MS`: **commit** at `t = pendingSince`:
     finalise the open session at `endedTs = t` (§finalise), then open a new session
     `{ startedTs: t, cls, courseId, character, costume, attempts: 0, pbs: 0 }`, emit open,
     clear pending.
   - else (pending but not yet stable): return.

**`noteRun(playerId, courseId)`** (definitive racing signal, no debounce):
- if open session is `racing:courseId`: `attempts++`; emit open (updated). 
- else: finalise any open session at `now`; open `racing:courseId` (`startedTs = now`,
  `attempts = 1`); emit open; clear pending.

**`notePb(playerId, courseId)`:** if open session is `racing:courseId`: `pbs++`.

**`onOffline(playerId)`:** finalise the open session at `now`; clear state.

**Finalise(session, endedTs):**
- **Drop** (emit `emitDrop(session_id)`, do not persist) if `cls === 'racing' && attempts === 0`
  (the debounce already prevents short-blip fragmentation, so no duration floor is needed).
- Else build the `final` `SessionView` (ended_ts set) → `emitFinal`. (Task 4's `emitFinal`
  both persists to `activity_events` and broadcasts.)

**`openSessions(): SessionView[]`** — every player's current open session as a `SessionView`
(for the connect snapshot).

**Tests** (inject `now` via a mutable closure; capture emit calls):
- open-at-start: first menus frame → after `STABLE_MS` an `open` fires with `started_ts` = the
  first frame's time.
- a one-frame foreign-key blip inside a stable session does not transition (debounce swallows).
- genuine transition: menus→character_select after `STABLE_MS` finalises menus (ended_ts =
  pendingSince) and opens character_select with the same `started_ts`.
- `noteRun` with no session opens racing (attempts 1) immediately; a second `noteRun`
  increments; `noteRun` for a new course finalises the old and opens the new.
- `notePb` sets `pbs`.
- drop rule: a racing session finalised with `attempts === 0` → `emitDrop`, no `emitFinal`; a
  short menus session is kept (`emitFinal`).
- `onOffline` finalises.
- held screen continues racing: RACING(courseA) then RACE_MENU frames keep `racing:A` (no
  transition, no extra open).

Commit.

---

### Task 3: Persist + type `session` activity events

**Files:** Modify `pi/src/activity/types.ts` (add `'session'` to `ActivityType`),
`pi/src/db/activity.ts` (no query change needed — `insertActivityEvents`/`resolveActivity`/
`recentActivity` already pass arbitrary `type`+`payload`; add a `sessionInput(view)` helper that
maps a final `SessionView` → `ActivityInput` with `type:'session'`, `player_id`, `course_id`,
`payload:{ cls, character, costume, started_ts, ended_ts, duration_ms, attempts, pbs }`).

**Test:** insert a `session` input, read it back via `recentActivity`, assert the payload +
player/course resolve. Commit.

---

### Task 4: Wire the tracker (presence + runs + stream); remove old paths

**Files:** Modify `pi/src/presence/hub.ts`, `pi/src/server.ts`, `pi/src/api/app.ts`,
`pi/src/api/runs.ts`, `pi/src/api/screen.ts`, `pi/src/activity/cascade.ts`,
`pi/src/activity/hub.ts`. Delete `pi/src/activity/screens.ts(.test.ts)`,
`pi/src/activity/grind.ts(.test.ts)`.

**Stream protocol** (`activity/hub.ts` → carry tagged messages):
```ts
export type ActivityStreamMsg =
  | { kind: 'event'; event: ActivityEvent }
  | { kind: 'session'; state: 'open'|'final'|'drop'; session?: SessionView; session_id?: number }
  | { kind: 'sessions_snapshot'; sessions: SessionView[] };
```
`ActivityHub` becomes `publish(msg: ActivityStreamMsg)`. `commitActivity`/`publish` of milestone
events wrap as `{ kind:'event', event }`.

**Tracker construction** (`server.ts`): build a `SessionTracker` whose `emitOpen` →
`activity.publish({kind:'session',state:'open',session})`; `emitDrop` →
`{state:'drop',session_id}`; `emitFinal` → persist via `insertActivityEvents([sessionInput(v)])`
then `activity.publish({kind:'session',state:'final',session})`. The `SessionView`'s
player/course display fields are resolved at emit time (name/color/slug) — add a small resolver
in `server.ts` using the db (or extend the view at the hub boundary).

**Presence hook** (`hub.ts`): the hub takes an optional `onFrame(playerId, FrameInput)` /
`onOffline(playerId)` callback set (constructor dep or setters). `update()` calls
`onFrame(playerId, { screen: frame.screen, courseId: courseIdBySlug(slugify(frame.course)),
character: frame.character, costume: frame.costume })` after building the entry; `setOffline()`
and `sweep()`→`setOffline()` already call `onOffline`. (Guard course lookup nulls.)

**Runs hook** (`runs.ts`): replace the `GrindTracker` block — on every attempt call
`sessionTracker.noteRun(playerId, courseId)`; on a PB call `sessionTracker.notePb(playerId,
courseId)`. Remove the `closedByMove` emit and the `tracker.close()` attempts-in-cascade.

**Cascade** (`cascade.ts`): drop the `attempts` parameter + its emitted event.

**Screen route** (`screen.ts`): drop `screenActivityInputs`/`commitActivity`; keep
`insertScreenIntervals`. Remove the now-unused imports.

**Stream connect** (`app.ts` `/v1/activity/stream`): on `onOpen`, send
`{kind:'sessions_snapshot', sessions: sessionTracker.openSessions()}` before subscribing.
Thread the tracker into `makeWs`.

**Tests:** update/trim `runs` integration expectations (PB cascade no longer carries attempts;
`noteRun`/`notePb` invoked — spy the tracker). Run full pi suite + typecheck. Commit.

---

### Task 5: Frontend activity store + client (`web/src/activityClient.js`, `src/lib/stores.js`)

**Files:** Modify `web/src/activityClient.js`, `src/lib/stores.js`, tests alongside.

- Activity store holds a list of **rows** with a stable `key`:
  - milestone → `evt:<id>` (from REST + `{kind:'event'}`).
  - persisted session (REST) → `evt:<id>` with `cls/duration` from payload, `state:'final'`.
  - live session → `sess:<session_id>`, upserted on `{kind:'session',state}`; `drop` removes it.
- `sessions_snapshot` seeds/replaces all `sess:*` rows.
- Sorting: by **feed timestamp desc** — `started_ts` for sessions (live + persisted payload),
  `ts` for milestones.
- Keep the REST history loader; merge by key (REST `evt:*` + live `sess:*`).

**Tests:** open upserts then final locks (same key); drop removes; snapshot seeds; ordering by
feed ts mixes a live session below a newer milestone; a persisted-session REST row and a live
session never collide (different keys). Commit.

---

### Task 6: Session row formatting (`web/src/lib/activityFormat.js`)

**Files:** Modify `web/src/lib/activityFormat.js` (+ tests). Reuse the existing four-column
grammar + icon placeholders.

- `racing` open → what = `racing` + ` · ` + ticking `m:ss` + (attempts ≥ 1 ? ` · ${n}` : '').
- `racing` final → `raced {courseIcon} as {costume? costume+' ' }{character} · {attempts} ·
  {mm:ss} · {pbs>0 ? (pbs===1?'new PB':pbs+' PBs') : 'no PB'}`.
- `menus` → `in the menus · {dur}`.
- `character_select`/`kart_select`/`track_select` → `choosing a character|kart|track · {dur}`.
- `ghost` → `watching a ghost · {dur}`.
- Duration: live rows compute `now - started_ts`; final rows use `duration_ms`. Format as a
  ticking clock `m:ss` (or `h:mm:ss` past an hour) so live rows visibly tick each second and long
  grinds read naturally (`24:13`).
- Player name in the player's colour; everything else neutral.

**Tests:** each class's open + final string; the duration formatter; no em-dashes; colour only on
the name field. Commit.

---

### Task 7: Render ticking sessions + re-add the feed

**Files:** Modify `web/src/ActivityLog.svelte` (consume the new row model + `now` tick),
`web/src/CardWall.svelte` (re-add `<ActivityLog {now} />` — it already owns a shared `now`
clock). 

- Rows iterate the sorted store; each row formats via Task 6 with the shared `now` so open
  durations tick on CardWall's existing clock (already 30fps while anyone races, else 1s — fine
  for a seconds-resolution feed; bump the idle tick only if a lone open session needs sub-second,
  which it doesn't).
- Verify the live page renders: no console errors, feed populates from REST, a synthetic
  `sess:*` open row ticks. (Manual dev check; the unit tests cover logic.)

Commit.

---

### Final review

Dispatch a whole-branch code review (most-capable model) over `merge-base main HEAD..HEAD`
against this plan + the spec's Global Constraints. Fix Critical/Important in one pass. Then
present for the user's live test via finishing-a-development-branch.

## Self-review notes

- Spec coverage: classifier (T1) · tracker/debounce/drop (T2) · persist (T3) · wiring + old-path
  removal + protocol + snapshot (T4) · store/keys/order (T5) · format (T6) · render/tick/re-add
  (T7). All spec sections mapped.
- Type consistency: `SessionView` defined once in T2, consumed by T4/T5/T6; `ScreenClass` from
  T1 used in T2's view + T6's formatter.
- presence/runs ordering (T4): `noteRun` opens racing only with presence corroboration (or
  presence-less / already-racing), so a run landing before presence flips is covered while a
  stray / late / ghost POST in a menu is ignored (no phantom 0s session). Verified by the
  noteRun-corroboration tests.
</content>
