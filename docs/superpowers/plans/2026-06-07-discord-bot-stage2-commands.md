# Discord bot — Stage 2 (slash commands) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the legacy bot's three slash commands — `/leaderboard [track]` (track + overall), `/wr <track>`, `/nemesis [player]` — to the Stage 1 bot, rewired to the shared `mkw.db` reads, with discord.js v14 command registration, autocomplete, and nemesis pagination.

**Architecture:** Builds on Stage 1 (`pi/src/bot/`). Pure data-assembly + formatters are unit-tested (vitest, seeded in-memory DB / exact-output); the discord.js interaction glue (command registration, autocomplete, button pagination) is a thin shell verified by a wiring/load check. Reuses Stage 1's `format.ts`, `db/reads.ts`, `db/reign.ts`, `players.config.ts`, `client.ts`.

**Tech Stack:** TypeScript, discord.js v14, `node:sqlite`, vitest, tsx.

**Spec:** `docs/superpowers/specs/2026-06-07-discord-bot-server-driven-design.md` (Stage 2 in Phasing).

**Porting note:** Several formatters are verbatim ports of `legacy/mkwpb2/kart-off/services/discord_bot.py`. Each port task cites the exact legacy method + lines and locks the output with **exact-string tests**. **The legacy source is the ultimate authority on spacing** — if a dictated expected-string below disagrees with what the faithful port produces, the *port* (matched against the legacy padding math) wins: correct the test expectation to the port's output and note it. (The legacy pads `padded_time = time + (maxTime-len+1)` spaces and the leaderboard diff has its own leading space → **two** spaces before `(`; nemesis has no leading space on its `(...)`. Don't "improve" the legacy alignment.)

---

## File structure (Stage 2)

```
pi/src/bot/
  format.ts            + msToDisplay, alignDiffColumn (shared), formatTrackLeaderboard,
                         formatTotalLeaderboard, formatNemesisTracks  (extends Stage 1 file)
  embeds/commands.ts   trackLeaderboardEmbed / totalLeaderboardEmbed / wrInfoEmbed / nemesisPageEmbed
  commands/
    views.ts           pure data-assembly: buildTrackBoard / buildOverallBoard / buildWrInfo / buildNemesis
    defs.ts            SlashCommandBuilder definitions + the autocomplete option lists
    install.ts         register commands on ready + interactionCreate router + nemesis pagination
  client.ts            (modify) expose the discord.js Client so install.ts can attach
  index.ts             (modify) call installCommands(announcer.client, db, cfg)
pi/src/db/
  leaderboards.ts      overallStandings (+ golf points), wrAggregate, nemesisRows
  reign.ts             (modify) + courseLeaderReign, overallReign
  lookups.ts           listCourses, listPlayers  (autocomplete sources)
```
Reuses: `format.ts` (Stage 1 helpers incl. private `parseDiff`), `db/reads.ts` (`courseLeaderboard`, `currentWr`), `db/reign.ts` (`trackReign`, `ReignInfo`), `db/seasons.ts` (`activeSeasonId`, `courseIdBySlug`), `db/slug.ts` (`slugify`), `players.config.ts` (`gifFor`, `nameForId`), `client.ts` (`Announcer`).

---

### Task 1: format.ts — msToDisplay + shared alignDiffColumn + DRY formatOvertaken

**Files:**
- Modify: `pi/src/bot/format.ts`
- Test: `pi/src/bot/format.test.ts`

- [ ] **Step 1: Write failing tests** (append to `format.test.ts`)

```ts
import { msToDisplay, alignDiffColumn } from './format';

describe('msToDisplay', () => {
  it('formats sub-minute and minute+ times (ports TimeUtils.milliseconds_to_display)', () => {
    expect(msToDisplay(23456)).toBe('23.456');
    expect(msToDisplay(83456)).toBe('1:23.456');
    expect(msToDisplay(120000)).toBe('2:00.000');
    expect(msToDisplay(59999)).toBe('59.999');
  });
});

describe('alignDiffColumn', () => {
  it('right-justifies the integer part to a common width; empty stays empty', () => {
    expect(alignDiffColumn(['+1.200s', '+12.030s', '', null])).toEqual([' +1.200s', '+12.030s', '', '']);   // legacy rjust: sign moves with the number
  });
});
```

- [ ] **Step 2: Run (fail)** — `cd pi && npx vitest run src/bot/format.test.ts` → FAIL (exports missing).

- [ ] **Step 3: Implement** (append to `format.ts`; `parseDiff` already exists privately in this file)

```ts
/** "1:23.456" / "23.456" — ports legacy TimeUtils.milliseconds_to_display. */
export function msToDisplay(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const msPart = ms % 1000;
  if (totalSeconds >= 60) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}.${String(msPart).padStart(3, '0')}`;
  }
  return `${totalSeconds}.${String(msPart).padStart(3, '0')}`;
}

/** Decimal-align a column of "+1.234s" diff strings: the sign+whole part is right-justified to
 *  the column's max width. '' / null entries stay ''. Shared by the leaderboard + nemesis + overtaken
 *  formatters (factors the legacy duplicated alignment). Returns the inner string (no parens). */
export function alignDiffColumn(diffs: (string | null | undefined)[]): string[] {
  const parts = diffs.map((d) => (d ? parseDiff(d) : null));
  const widths = parts.filter((p): p is { sign_and_whole: string; decimal: string } => p !== null)
                      .map((p) => p.sign_and_whole.length);
  const maxBefore = widths.length ? Math.max(...widths) : 0;
  return parts.map((p) => {
    if (!p) return '';
    const before = p.sign_and_whole.padStart(maxBefore);
    return p.decimal ? `${before}.${p.decimal}s` : `${before}s`;
  });
}
```

- [ ] **Step 4: DRY formatOvertaken to use alignDiffColumn** — replace the body of the existing `formatOvertaken` so it computes the aligned column via `alignDiffColumn(list.map((p) => p.diff_str))` and wraps each as `` `${paddedName}(${aligned[i]})` ``. The existing Stage 1 `formatOvertaken` tests must stay green (identical output). Keep the `Math.max(2, maxName - name.length + 2)` name padding and the `'`No-one`'` empty case.

- [ ] **Step 5: Run (pass)** — `cd pi && npx vitest run src/bot/format.test.ts` → PASS (Stage 1 + new). 

- [ ] **Step 6: Commit**
```bash
git add pi/src/bot/format.ts pi/src/bot/format.test.ts
git commit -m "feat(bot): msToDisplay + shared alignDiffColumn; DRY formatOvertaken"
```

---

### Task 2: format.ts — formatTrackLeaderboard

**Files:** Modify `pi/src/bot/format.ts`; Test `pi/src/bot/format.test.ts`

Port of legacy `_format_track_leaderboard` (`legacy/mkwpb2/kart-off/services/discord_bot.py:650-742`). Signature:
```ts
export type BoardRow = { position: number; name: string; time: string; time_ms: number };
export function formatTrackLeaderboard(rows: BoardRow[], wr: { record: string; record_ms: number } | null): string
```
Behavior (match legacy exactly): if no rows and no wr → `` `No times recorded` ``. Start with a WR line `` `   WR      ${wr.record}` `` when wr present. For each row, the diff is the gap to the **previous row's time** (chained; the first row's "previous" is the WR time if present, else no diff): `diff_ms = time_ms - last_ms` when `last_ms != null && time_ms > last_ms`, formatted via `formatTimeDifference`, else `''`; `last_ms` starts at `wr.record_ms ?? null` and updates to each row's `time_ms`. Decimal-align the diffs with `alignDiffColumn`. Each line: `` `${position}. ${name padded to max+2}${time padded to maxTime+1}${diff ? ' (' + aligned + ')' : ''}` ``.

- [ ] **Step 1: Failing test** (append):
```ts
import { formatTrackLeaderboard } from './format';
describe('formatTrackLeaderboard', () => {
  it('renders WR line + chained gaps, decimal-aligned (legacy format)', () => {
    const out = formatTrackLeaderboard(
      [ { position: 1, name: 'Paul', time: '1:46.000', time_ms: 106000 },
        { position: 2, name: 'Luke', time: '1:48.000', time_ms: 108000 } ],
      { record: '1:40.000', record_ms: 100000 },
    );
    expect(out).toBe(
      '`   WR      1:40.000`\n' +
      '`1. Paul  1:46.000  (+6.000s)`\n' +   // TWO spaces before '(': padded_time trailing space + diff leading space
      '`2. Luke  1:48.000  (+2.000s)`'
    );
  });
  it('handles empty', () => {
    expect(formatTrackLeaderboard([], null)).toBe('`No times recorded`');
  });
});
```
- [ ] **Step 2: Run (fail).**  **Step 3: Implement** the port (use `alignDiffColumn`; cite legacy lines in a JSDoc).  **Step 4: Run (pass).**
- [ ] **Step 5: Commit** — `feat(bot): track leaderboard formatter`.

---

### Task 3: format.ts — formatTotalLeaderboard

**Files:** Modify `pi/src/bot/format.ts`; Test `pi/src/bot/format.test.ts`

Port of legacy `_format_total_leaderboard` (`discord_bot.py:744-833`). Signature:
```ts
export type TotalRow = { position: number; name: string; total_display: string; total_ms: number; points: number };
export function formatTotalLeaderboard(rows: TotalRow[], wrTotalDisplay: string, wrTotalMs: number): string
```
Behavior: empty → `` `No times recorded` ``. First line `` `   WR      ${wrTotalDisplay}` ``. Chained gaps from the previous total (`last_total_ms` starts at `wrTotalMs`, gap only when `last>0 && time>last`). Each row ends with a golf score `` ` [${points}]` `` appended after the diff. Lines: `` `${position}. ${name pad max+2}${total pad maxTime+1}${diff?' ('+aligned+')':''} [${points}]` ``.

- [ ] **Step 1: Failing test** (append):
```ts
import { formatTotalLeaderboard } from './format';
describe('formatTotalLeaderboard', () => {
  it('renders WR aggregate + gaps + golf points', () => {
    const out = formatTotalLeaderboard(
      [ { position: 1, name: 'Paul', total_display: '3:30.000', total_ms: 210000, points: 2 },
        { position: 2, name: 'Luke', total_display: '3:36.000', total_ms: 216000, points: 4 } ],
      '3:20.000', 200000,
    );
    expect(out).toBe(
      '`   WR      3:20.000`\n' +
      '`1. Paul  3:30.000  (+10.000s) [2]`\n' +   // TWO spaces before '(' (padded_time + diff leading space)
      '`2. Luke  3:36.000  ( +6.000s) [4]`'
    );
  });
});
```
> Note the alignment: `+10.000s` (2 integer digits) and ` +6.000s` (right-justified to width 3 incl. sign) share the column — confirm the implementation reproduces this via `alignDiffColumn`.

- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): overall leaderboard formatter with golf points`.

---

### Task 4: format.ts — formatNemesisTracks

**Files:** Modify `pi/src/bot/format.ts`; Test `pi/src/bot/format.test.ts`

Port of legacy `_format_nemesis_tracks` (`discord_bot.py:455-520`). Signature:
```ts
export type NemesisRow = { track_name: string; time_difference_str: string; ahead_player: string };
export function formatNemesisTracks(rows: NemesisRow[], isTargeted: boolean, startPosition: number): string
```
Behavior: empty → `` `No tracks where you're behind` ``. Position width = digits of `startPosition + len - 1`. Track padded to `maxTrack - len + 2`. Diff decimal-aligned via `alignDiffColumn`, wrapped `(aligned)`. `player_info = isTargeted ? '' : ' [' + ahead_player + ']'`. Line: `` `${pos rjust}. ${padded_track}(${aligned})${player_info}` ``.

- [ ] **Step 1: Failing test** (append):
```ts
import { formatNemesisTracks } from './format';
describe('formatNemesisTracks', () => {
  it('renders positions, padded tracks, aligned gaps, and [ahead] when untargeted', () => {
    const out = formatNemesisTracks(
      [ { track_name: 'Rainbow Road', time_difference_str: '+2.500s', ahead_player: 'Luke' },
        { track_name: 'DK Pass', time_difference_str: '+0.300s', ahead_player: 'Paul' } ],
      false, 1,
    );
    expect(out).toBe(
      '`1. Rainbow Road  (+2.500s) [Luke]`\n' +
      '`2. DK Pass       (+0.300s) [Paul]`'
    );
  });
  it('empty', () => { expect(formatNemesisTracks([], false, 1)).toBe("`No tracks where you're behind`"); });
});
```
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): nemesis tracks formatter`.

---

### Task 5: db/leaderboards.ts — overallStandings + wrAggregate

**Files:** Create `pi/src/db/leaderboards.ts`; Test `pi/src/db/leaderboards.test.ts`

```ts
import type { DatabaseSync } from 'node:sqlite';
import { courseLeaderboard } from './reads';

export type Standing = { player_id: number; display_name: string; total_ms: number; tracks: number; points: number };

/** Overall standings across all courses for (season,cc): each roster player's summed PB time, the
 *  number of courses they have a PB on, and golf points = sum of their per-course rank. Ranked by
 *  (total_ms, points). Ports legacy PersonalBest.get_total_leaderboard (points = sum of positions). */
export function overallStandings(db: DatabaseSync, seasonId: number, cc: number): Standing[] {
  const courses = db.prepare('SELECT DISTINCT course_id FROM runs WHERE season_id=? AND cc=? AND is_pb=1')
    .all(seasonId, cc) as { course_id: number }[];
  const acc = new Map<number, { name: string; total: number; tracks: number; points: number }>();
  for (const { course_id } of courses) {
    const lb = courseLeaderboard(db, seasonId, course_id, cc);   // already ranked fastest-first
    lb.forEach((row, i) => {
      const cur = acc.get(row.player_id) ?? { name: row.display_name, total: 0, tracks: 0, points: 0 };
      cur.total += row.total_time_ms;
      cur.tracks += 1;
      cur.points += i + 1;          // rank on this course
      acc.set(row.player_id, cur);
    });
  }
  const rows = [...acc.entries()].map(([player_id, v]) => ({
    player_id, display_name: v.name, total_ms: v.total, tracks: v.tracks, points: v.points,
  }));
  rows.sort((a, b) => a.total_ms - b.total_ms || a.points - b.points);
  return rows;
}

/** Sum of the current WR record_ms across all courses that have one (for the overall WR aggregate row). */
export function wrAggregate(db: DatabaseSync, cc: number): { total_ms: number; count: number } {
  const row = db.prepare('SELECT COALESCE(SUM(record_ms),0) total, COUNT(*) n FROM world_records WHERE cc=? AND is_current=1')
    .get(cc) as { total: number; n: number };
  return { total_ms: row.total, count: row.n };
}
```

- [ ] **Step 1: Failing test** — seed 2 courses, 2 players with PBs (Paul faster on both), WRs on both; assert `overallStandings` totals/points/order (Paul points 2, Luke points 4; Paul total < Luke) and `wrAggregate` sums both current WRs. Seed pattern per `pi/src/db/reads.test.ts`.
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): overall standings (golf points) + WR aggregate reads`.

---

### Task 6: db/leaderboards.ts — nemesisRows

**Files:** Modify `pi/src/db/leaderboards.ts`; Test `pi/src/db/leaderboards.test.ts`

Port of legacy `_calculate_nemesis_data` (`discord_bot.py:380-452`) against the new schema.
```ts
import { currentWr } from './reads';   // (only if needed; otherwise omit)

export type NemesisDatum = { track_name: string; diff_ms: number; ahead_player: string };

/** Courses where `playerId` is behind, vs a specific `targetId` or (when null) the course leader.
 *  diff_ms = player's PB - the comparison PB (positive = player is behind). Sorted largest gap first.
 *  Only courses where the player has a PB (and, for targeted, the target also has one) are included.
 *  `nameOf(id)` resolves display names. Ports legacy _calculate_nemesis_data. */
export function nemesisRows(db: DatabaseSync, seasonId: number, cc: number,
                            playerId: number, targetId: number | null): NemesisDatum[] {
  const courses = db.prepare(
    `SELECT c.id, c.display_name FROM courses c
     WHERE EXISTS (SELECT 1 FROM runs r WHERE r.season_id=? AND r.cc=? AND r.course_id=c.id AND r.player_id=? AND r.is_pb=1)`
  ).all(seasonId, cc, playerId) as { id: number; display_name: string }[];
  const out: NemesisDatum[] = [];
  for (const c of courses) {
    const lb = courseLeaderboard(db, seasonId, c.id, cc);
    const mine = lb.find((r) => r.player_id === playerId);
    if (!mine) continue;
    let ahead: { name: string; ms: number } | null = null;
    if (targetId != null) {
      const t = lb.find((r) => r.player_id === targetId);
      if (!t) continue;                       // target has no time here -> skip
      ahead = { name: t.display_name, ms: t.total_time_ms };
    } else {
      let leader = lb[0];
      if (leader.player_id === playerId) leader = lb[1];   // compare to 2nd when player leads
      if (!leader) continue;
      ahead = { name: leader.display_name, ms: leader.total_time_ms };
    }
    out.push({ track_name: c.display_name, diff_ms: mine.total_time_ms - ahead.ms, ahead_player: ahead.name });
  }
  out.sort((a, b) => b.diff_ms - a.diff_ms);
  return out;
}
```
> The handler maps `diff_ms` → `time_difference_str` via `formatTimeDifference` when building `NemesisRow`s.

- [ ] **Step 1: Failing test** — seed 2 courses; player behind leader on both (untargeted) and vs a target; assert order (largest gap first), `ahead_player`, and the "compare to 2nd when player leads" branch. 
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): nemesis comparison read`.

---

### Task 7: db/reign.ts — courseLeaderReign + overallReign

**Files:** Modify `pi/src/db/reign.ts`; Test `pi/src/db/reign.test.ts`

```ts
/** The current course leader's reign (no exclusion) - for the /leaderboard track footer. */
export function courseLeaderReign(db: DatabaseSync, seasonId: number, courseId: number, cc: number): ReignInfo {
  // trackReign with no run excluded returns the current champion + reign; newPlayer '' so is_same_person is false.
  return trackReign(db, seasonId, courseId, cc, '', -1);
}

export type OverallReign = { leader: string | null; reign_ms: number | null };

/** How long the current overall leader has held the top of the OVERALL standings. Forward-replay of
 *  all finished runs: maintain each player's per-course best, recompute the overall leader (min summed
 *  total, points tiebreak) after each run, and reset the reign start when the overall leader changes.
 *  Mirrors legacy get_overall_reign_duration but in one pass. Graceful null when timestamps missing. */
export function overallReign(db: DatabaseSync, seasonId: number, cc: number): OverallReign {
  const runs = db.prepare(
    `SELECT p.display_name AS name, r.course_id AS course, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.cc=? AND r.status='finished' AND r.total_time_ms IS NOT NULL AND r.ended_at IS NOT NULL
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(seasonId, cc) as { name: string; course: number; ms: number; ended_at: string }[];
  if (runs.length === 0) return { leader: null, reign_ms: null };

  const best = new Map<string, Map<number, number>>();   // player -> (course -> best ms)
  let leader: string | null = null;
  let reignStart: string | null = null;
  const overallLeader = (): string | null => {
    let lname: string | null = null, lmin = Infinity, lpts = Infinity;
    // rank each course to compute points; cheap at friend-group scale.
    const courseSet = new Set<number>();
    for (const m of best.values()) for (const c of m.keys()) courseSet.add(c);
    const totals = new Map<string, { total: number; pts: number }>();
    for (const [name, m] of best) totals.set(name, { total: [...m.values()].reduce((a, b) => a + b, 0), pts: 0 });
    for (const c of courseSet) {
      const ranked = [...best.entries()].filter(([, m]) => m.has(c))
        .map(([name, m]) => ({ name, ms: m.get(c)! })).sort((a, b) => a.ms - b.ms);
      ranked.forEach((e, i) => { totals.get(e.name)!.pts += i + 1; });
    }
    for (const [name, t] of totals) if (t.total < lmin || (t.total === lmin && t.pts < lpts)) { lmin = t.total; lpts = t.pts; lname = name; }
    return lname;
  };
  for (const r of runs) {
    let m = best.get(r.name); if (!m) { m = new Map(); best.set(r.name, m); }
    const cur = m.get(r.course);
    if (cur === undefined || r.ms < cur) m.set(r.course, r.ms);
    const l = overallLeader();
    if (l !== leader) { leader = l; reignStart = r.ended_at; }
  }
  const reign_ms = reignStart ? Date.now() - Date.parse(reignStart) : null;
  return { leader, reign_ms: reign_ms != null && reign_ms >= 0 ? reign_ms : null };
}
```

- [ ] **Step 1: Failing test** (append to `reign.test.ts`):
  - `courseLeaderReign`: seed a course where Luke led then Paul took over; assert `previous_holder` = current leader (Paul) and `reign_ms > 0`.
  - `overallReign`: seed 2 courses / 2 players across time so the overall leader flips; assert `leader` is the final overall leader and `reign_ms > 0`; empty DB → `{ leader: null, reign_ms: null }`.
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): course + overall reign reads`.

---

### Task 8: db/lookups.ts — listCourses + listPlayers

**Files:** Create `pi/src/db/lookups.ts`; Test `pi/src/db/lookups.test.ts`

```ts
import type { DatabaseSync } from 'node:sqlite';

export function listCourses(db: DatabaseSync): { slug: string; display_name: string }[] {
  return db.prepare('SELECT slug, display_name FROM courses ORDER BY display_name').all() as any;
}

export function listPlayers(db: DatabaseSync, seasonId: number): { display_name: string }[] {
  return db.prepare(
    `SELECT p.display_name FROM season_rosters sr JOIN players p ON p.id = sr.player_id
     WHERE sr.season_id=? ORDER BY p.display_name`
  ).all(seasonId) as any;
}
```
- [ ] **Step 1: Failing test** — seed courses + a roster; assert ordered lists. **Step 2-4: TDD.** **Step 5: Commit** — `feat(bot): course + player lookups for autocomplete`.

---

### Task 9: commands/views.ts — pure data assembly

**Files:** Create `pi/src/bot/commands/views.ts`; Test `pi/src/bot/commands/views.test.ts`

Pure functions that turn a command + the DB into the data the embeds need (no discord.js). This is the testable core of the handlers.

```ts
import type { DatabaseSync } from 'node:sqlite';
import { activeSeasonId, courseIdBySlug } from '../../db/seasons';
import { slugify } from '../../db/slug';
import { mkwrsNameToSlug } from '../../wr/courses';
import { courseLeaderboard, currentWr } from '../../db/reads';
import { overallStandings, wrAggregate, nemesisRows } from '../../db/leaderboards';
import { courseLeaderReign, overallReign } from '../../db/reign';
import { msToDisplay, formatTrackLeaderboard, formatTotalLeaderboard, formatNemesisTracks } from '../format';
import type { BoardRow, TotalRow, NemesisRow } from '../format';
import { formatTimeDifference } from '../format';
import { nameForId } from '../players.config';

const courseName = (db: DatabaseSync, courseId: number): string =>
  (db.prepare('SELECT display_name FROM courses WHERE id=?').get(courseId) as { display_name: string } | undefined)?.display_name ?? '';
const playerId = (db: DatabaseSync, name: string): number | null =>
  (db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(name) as { id: number } | undefined)?.id ?? null;

export type TrackBoard = { title: string; body: string; leader: string | null; reign_ms: number | null } | { error: string };
export function buildTrackBoard(db: DatabaseSync, courseInput: string, cc = 150): TrackBoard {
  const season = activeSeasonId(db);
  const courseId = courseIdBySlug(db, slugify(courseInput));
  if (courseId == null) return { error: `Track '${courseInput}' not found.` };
  const lb = courseLeaderboard(db, season, courseId, cc);
  const wrRow = currentWr(db, courseId, cc);
  if (lb.length === 0 && !wrRow) return { error: `No times recorded for ${courseName(db, courseId)}.` };
  const rows: BoardRow[] = lb.map((r, i) => ({ position: i + 1, name: r.display_name, time: r.total_time_str ?? msToDisplay(r.total_time_ms), time_ms: r.total_time_ms }));
  const body = formatTrackLeaderboard(rows, wrRow ? { record: wrRow.record_str, record_ms: wrRow.record_ms } : null);
  const reign = courseLeaderReign(db, season, courseId, cc);
  return { title: `${courseName(db, courseId)} Leaderboard`, body, leader: reign?.previous_holder ?? null, reign_ms: reign?.reign_ms ?? null };
}

export type OverallBoard = { title: string; body: string; leader: string | null; reign_ms: number | null };
export function buildOverallBoard(db: DatabaseSync, cc = 150): OverallBoard {
  const season = activeSeasonId(db);
  const standings = overallStandings(db, season, cc);
  const agg = wrAggregate(db, cc);
  const rows: TotalRow[] = standings.map((s, i) => ({ position: i + 1, name: s.display_name, total_display: msToDisplay(s.total_ms), total_ms: s.total_ms, points: s.points }));
  const body = formatTotalLeaderboard(rows, agg.count ? msToDisplay(agg.total_ms) : 'N/A', agg.total_ms);
  const reign = overallReign(db, season, cc);
  return { title: 'Overall Leaderboard', body, leader: reign.leader, reign_ms: reign.reign_ms };
}

export type WrInfo = { title: string; time: string; char: string; kart: string; reign_ms: number | null; video: { url: string; note: string | null } | null } | { error: string };
export function buildWrInfo(db: DatabaseSync, courseInput: string, cc = 150): WrInfo {
  const courseId = courseIdBySlug(db, mkwrsNameToSlug(courseInput)) ?? courseIdBySlug(db, slugify(courseInput));
  if (courseId == null) return { error: `Track '${courseInput}' not found.` };
  const wr = currentWr(db, courseId, cc);
  if (!wr) return { error: `No world record found for ${courseName(db, courseId)}.` };
  let character = wr.character || 'Unknown';
  if (character.includes('(')) character = character.split('(')[0].trim();
  // reign: contiguous same-holder run in world_records history (reuse wrReign with prevHolder=holder)
  const reign = (db.prepare('SELECT holder_name FROM world_records WHERE course_id=? AND cc=? AND is_current=1').get(courseId, cc) as { holder_name: string | null } | undefined);
  const video = wrVideo(db, courseId, cc, wr.video_url ?? null);
  return { title: `${wr.holder_name}'s ${courseName(db, courseId)}`, time: wr.record_str, char: character, kart: wr.vehicle || 'Unknown',
    reign_ms: currentWrReignMs(db, courseId, cc, reign?.holder_name ?? null), video };
}

// helper: reign ms of the current WR holder (contiguous block), ports get_current_reign_duration
function currentWrReignMs(db: DatabaseSync, courseId: number, cc: number, holder: string | null): number | null {
  if (!holder) return null;
  const rows = db.prepare('SELECT holder_name, achieved_at FROM world_records WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC').all(courseId, cc) as { holder_name: string | null; achieved_at: string | null }[];
  let start: string | null = null;
  for (const r of rows) { if (r.holder_name === holder) start = r.achieved_at ?? start; else break; }
  if (!start) return null;
  const ms = Date.now() - Date.parse(start);
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

// helper: current WR video, else the most-recent prior WR row with a video (ports _find_wr_video)
function wrVideo(db: DatabaseSync, courseId: number, cc: number, currentUrl: string | null): { url: string; note: string | null } | null {
  if (currentUrl) return { url: currentUrl, note: null };
  const rows = db.prepare('SELECT video_url, is_current FROM world_records WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC LIMIT 10').all(courseId, cc) as { video_url: string | null; is_current: number }[];
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].video_url && rows[i].is_current !== 1) {
      const pos = i + 1;
      const ord = pos === 2 ? '2nd' : pos === 3 ? '3rd' : `${pos}th`;
      return { url: rows[i].video_url!, note: `Current WR has no video, showing ${ord} most recent:` };
    }
  }
  return null;
}

export type NemesisView = { title: string; rows: NemesisRow[]; targeted: boolean } | { error: string };
export function buildNemesis(db: DatabaseSync, requesterDiscordId: string, targetName: string | null, cc = 150): NemesisView {
  const requester = nameForId(requesterDiscordId);
  if (!requester) return { error: 'You are not registered as a player.' };
  const season = activeSeasonId(db);
  const meId = playerId(db, requester);
  if (meId == null) return { error: 'You are not registered as a player.' };
  const targetId = targetName ? playerId(db, targetName) : null;
  if (targetName && targetId == null) return { error: `No data found for comparison with ${targetName}` };
  const data = nemesisRows(db, season, cc, meId, targetId);
  if (data.length === 0) return { error: `No data found for comparison${targetName ? ' with ' + targetName : ''}` };
  const title = `${requester}'s Nemesis Tracks${targetName ? ' vs ' + targetName : ''}`;
  const rows: NemesisRow[] = data.map((d) => ({ track_name: d.track_name, time_difference_str: formatTimeDifference(d.diff_ms), ahead_player: d.ahead_player }));
  return { title, rows, targeted: targetName != null };
}
```
> `currentWr` returns `holder_name, record_ms, record_str, achieved_at, video_url, character, vehicle` (see `db/reads.ts`).

- [ ] **Step 1: Failing tests** (seeded DB) for: `buildTrackBoard` (title + body shape + leader/reign), unknown course → `{error}`; `buildOverallBoard`; `buildWrInfo` (char cleaning, video fallback) + unknown course; `buildNemesis` (unregistered id → error; untargeted ordering). Assert the `body` strings using the Task 2-4 formatters' known output, and the `error` strings.
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): pure command data-assembly (views)`.

---

### Task 10: embeds/commands.ts — command embeds

**Files:** Create `pi/src/bot/embeds/commands.ts`; Test `pi/src/bot/embeds/commands.test.ts`

```ts
import { EmbedBuilder } from 'discord.js';
import { formatDuration } from '../format';
import type { NemesisRow } from '../format';
import { formatNemesisTracks } from '../format';

const BLUE = 0xc2ddfd;

function reignFooter(e: EmbedBuilder, leader: string | null, reignMs: number | null, thumb: string | null) {
  if (leader && reignMs != null) {
    e.setFooter({ text: `BEHOLD THE ${formatDuration(reignMs)} REIGN OF ${leader.toUpperCase()}`, ...(thumb ? { iconURL: thumb } : {}) });
    if (thumb) e.setThumbnail(thumb);
  }
}

export function trackLeaderboardEmbed(v: { title: string; body: string; leader: string | null; reign_ms: number | null }, thumb: string | null): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(v.title).setColor(BLUE).setDescription(v.body);
  reignFooter(e, v.leader, v.reign_ms, thumb);
  return e;
}
export function totalLeaderboardEmbed(v: { title: string; body: string; leader: string | null; reign_ms: number | null }, thumb: string | null): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(v.title).setColor(BLUE).setDescription(v.body);
  reignFooter(e, v.leader, v.reign_ms, thumb);
  return e;
}
export function wrInfoEmbed(v: { title: string; time: string; char: string; kart: string; reign_ms: number | null }): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(v.title).setColor(BLUE)
    .addFields(
      { name: 'TIME', value: `\`${v.time}\``, inline: true },
      { name: 'CHAR', value: `\`${v.char}\``, inline: true },
      { name: 'KART', value: `\`${v.kart}\``, inline: true },
    );
  if (v.reign_ms != null) e.setFooter({ text: `${formatDuration(v.reign_ms).toUpperCase()} REIGN` });
  return e;
}
export function nemesisPageEmbed(title: string, rows: NemesisRow[], targeted: boolean, startPosition: number, footer: string): EmbedBuilder {
  return new EmbedBuilder().setTitle(title).setColor(BLUE)
    .setDescription(formatNemesisTracks(rows, targeted, startPosition)).setFooter({ text: footer });
}
```
- [ ] **Step 1: Failing snapshot tests** — assert `.toJSON()` title/color/description/footer for each, incl. the reign footer (`BEHOLD THE 3 DAY REIGN OF PAUL`) and the WR `3 DAY REIGN` footer. Match legacy (`_send_track_leaderboard` footer text; `_handle_wr_command` `{dur} REIGN`).
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): command embeds`.

---

### Task 11: commands/defs.ts — slash command definitions + autocomplete data

**Files:** Create `pi/src/bot/commands/defs.ts`; Test `pi/src/bot/commands/defs.test.ts`

```ts
import { SlashCommandBuilder } from 'discord.js';

export const commandDefs = [
  new SlashCommandBuilder().setName('leaderboard').setDescription('Show a track or overall leaderboard')
    .addStringOption((o) => o.setName('track').setDescription('Track name').setRequired(false).setAutocomplete(true)),
  new SlashCommandBuilder().setName('wr').setDescription('Show the current world record for a track')
    .addStringOption((o) => o.setName('track').setDescription('Track name').setRequired(true).setAutocomplete(true)),
  new SlashCommandBuilder().setName('nemesis').setDescription('Tracks where you are furthest behind')
    .addStringOption((o) => o.setName('player').setDescription('Compare vs a specific player').setRequired(false).setAutocomplete(true)),
].map((c) => c.toJSON());

/** Filter helper for autocomplete: case-insensitive substring, capped at 25 (Discord limit). */
export function filterChoices(values: string[], current: string): { name: string; value: string }[] {
  const q = current.toLowerCase();
  return values.filter((v) => v.toLowerCase().includes(q)).slice(0, 25).map((v) => ({ name: v, value: v }));
}
```
- [ ] **Step 1: Failing test** — `commandDefs` has 3 entries with names leaderboard/wr/nemesis and `wr.options[0].required === true`; `filterChoices(['Rainbow Road','DK Pass'], 'ra')` → `[{name:'Rainbow Road',value:'Rainbow Road'}]`; cap at 25.
- [ ] **Step 2-4: TDD.**  **Step 5: Commit** — `feat(bot): slash command definitions + autocomplete filter`.

---

### Task 12: commands/install.ts + client/index wiring

**Files:** Create `pi/src/bot/commands/install.ts`; Modify `pi/src/bot/client.ts`, `pi/src/bot/index.ts`

`install.ts` registers the commands and routes interactions. It is the discord.js I/O shell (no unit test; verified by the load/wiring check). It must:
- On `clientReady`: register `commandDefs` to the guild (`client.application.commands.set(commandDefs, guildId)`) when `guildId` set, else globally (`client.application.commands.set(commandDefs)`).
- `client.on('interactionCreate', ...)`:
  - `interaction.isChatInputCommand()`: route by `commandName` to `handleLeaderboard` / `handleWr` / `handleNemesis` (these call the Task 9 `build*` views, build embeds via Task 10, and reply; on `{error}` reply ephemeral). `/wr` also sends the video as a follow-up channel message when present. Wrap each in try/catch → ephemeral error reply.
  - `interaction.isAutocomplete()`: respond with `filterChoices(listCourses(db).map(c=>c.display_name) | listPlayers(db,season).map(p=>p.display_name), focusedValue)`.
- Nemesis pagination: when `buildNemesis` returns >5 rows, build pages of 5 via `nemesisPageEmbed` (page footer `Page X of Y • N total tracks`, single page `N tracks`), reply with the first page + a `◀`/`▶` ButtonBuilder ActionRow, and attach a `createMessageComponentCollector` (300s) that edits the message on button clicks (ports `NemesisPaginationView`). gif thumbnails via `gifFor(leader)`.

Provide complete code in the task (discord.js v14: `ButtonBuilder`/`ActionRowBuilder`/`ComponentType`, `interaction.reply`, `response.createMessageComponentCollector`). **If the installed discord.js v14 API differs (command registration path, collector API), STOP and report DONE_WITH_CONCERNS with the actual API rather than guessing.**

`client.ts`: add a public getter `get client(): Client { return this._client; }` (rename the private field if needed) so `install.ts` can attach. Keep all Stage 1 behavior.

`index.ts`: after constructing the `Announcer`, call `installCommands(announcer.client, db, { guildId: cfg.guildId })` before/after `announcer.start()` (registration happens on the shared client's `clientReady`).

- [ ] **Step 1:** implement `install.ts` (+ the three `handle*` functions, either inline or in `commands/handlers.ts`), expose `Announcer.client`, wire `index.ts`.
- [ ] **Step 2:** Full suite green: `cd pi && npx vitest run` (no behavior regressions; expect all prior + new tests).
- [ ] **Step 3:** Wiring check (dummy env, short timeout): confirm `[bot] started` prints and no import/registration crash before the expected TokenInvalid. Clean up any throwaway db files.
- [ ] **Step 4: Commit** — `feat(bot): slash command registration + interaction routing + nemesis pagination`.

---

### Task 13: Docs + memory

**Files:** Modify `pi/src/bot/README.md`; update memory (`discord_bot_stage1.md` → note Stage 2 done, or add `discord_bot_stage2.md`; update `MEMORY.md`).

- [ ] **Step 1:** README: add a "Commands" section (`/leaderboard [track]`, `/wr <track>`, `/nemesis [player]`), note autocomplete + that `DISCORD_GUILD_ID` gives instant command sync.
- [ ] **Step 2:** Memory: record Stage 2 complete (commands on shared-DB reads, overall reign + golf points, nemesis pagination), branch `discord-bot-stage2-commands`, test counts.
- [ ] **Step 3: Commit** — `docs(bot): Stage 2 commands README + memory`.

---

## Self-Review

**Spec coverage (Stage 2 in the spec's Phasing + slash-command section):**
- `/leaderboard` track + overall — Tasks 2,3,5,7,9,10,12. ✓
- `/wr` + video fallback — Tasks 9 (`buildWrInfo`/`wrVideo`/reign), 10, 12. ✓
- `/nemesis` + pagination + Discord-id map — Tasks 4,6,9,12. ✓
- Autocomplete from courses/players — Tasks 8,11,12. ✓
- Reign footers (track + overall + WR) — Task 7,9,10. ✓
- Golf points + WR aggregate — Tasks 3,5. ✓
- Factor shared decimal alignment — Task 1 (`alignDiffColumn`, reused). ✓
- discord.js registration + interaction routing — Task 12. ✓

**Placeholder scan:** the verbatim-port formatter tasks (2,3,4) cite exact legacy lines + lock output with exact-string tests rather than re-pasting the legacy code as TS; the implementer ports from the cited source against the tests. All novel code (reads, reign, views, embeds, defs, install) is complete in-plan.

**Type consistency:** `BoardRow`/`TotalRow`/`NemesisRow` defined in `format.ts` (Tasks 2-4), consumed by `views.ts`/`embeds` (9,10). `Standing`/`NemesisDatum` in `leaderboards.ts` (5,6) → `views.ts`. `ReignInfo`/`OverallReign` in `reign.ts` (7). `currentWr` columns (`holder_name`,`record_str`,`record_ms`,`video_url`,`character`,`vehicle`) used consistently. `nameForId` from `players.config` (9). `commandDefs`/`filterChoices` (11) used by `install.ts` (12).

**Note:** Tasks 2-4 reference types (`BoardRow` etc.) they also define at the top of each task; `views.ts` (9) imports them from `format.ts`. Ensure the `export type` lines land in `format.ts` in Tasks 2-4.
