# Discord bot — Stage 1 (announcements) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A TypeScript/discord.js bot in `pi/src/bot/` that connects to the server's `/v1/events` WebSocket and posts the legacy PB (green) and WR (grey) embeds to a Discord channel, recomputing the rich fields (overtaken / positions / still-ahead / reign) from the shared `mkw.db`.

**Architecture:** Separate Node process in the existing `pi/` package. Events arrive over a WebSocket client (Node's built-in global `WebSocket`); rich data is read from the shared SQLite DB (WAL, concurrent-read-safe) by reusing/extending `pi/src/db/`. Pure builders (`format`, `enrich`, `embeds`, `dispatch`, reign queries) are unit-tested; the I/O shells (`ws`, `client`, `index`) are thin and smoke-tested. No server changes.

**Tech Stack:** TypeScript, discord.js v14, `node:sqlite`, vitest, tsx.

**Spec:** `docs/superpowers/specs/2026-06-07-discord-bot-server-driven-design.md`

**Scope note:** This is Stage 1 of 2. Stage 2 (slash commands `/leaderboard`, `/nemesis`, `/wr`) is a separate plan that builds on these reads + formatters.

---

## File structure (Stage 1)

```
pi/src/bot/
  config.ts          env -> BotConfig (token, channel, db path, ws url)
  players.config.ts  ID_TO_NAME + THUMBNAIL_GIFS + gifFor()/nameForId() (committed, non-secret)
  types.ts           PbEmbedData / WrEmbedData / Positions / OvertakenEntry / StillAhead
  format.ts          formatTimeDifference, formatDuration, formatOvertaken, formatPositions
  enrich.ts          buildPbData / buildWrData (event + DB -> embed data)
  embeds/pb.ts       pbTitle + buildPbEmbed
  embeds/wr.ts       buildWrEmbed
  ws.ts              parseEvent + startEventStream (reconnecting WS client)
  dispatch.ts        dispatch(db, event, send) -> builds + emits the right embed
  client.ts          Announcer: discord.js client, ready-buffer, channel send
  index.ts           entry: wire config -> db -> announcer -> ws -> dispatch
pi/src/db/
  reign.ts           wrReign + trackReign (+ ReignInfo type)
```
Reuses existing: `db/connect.ts` (`openDb`), `db/reads.ts` (`courseLeaderboard`, `overallLeaderboard`, `currentWr`), `db/seasons.ts` (`activeSeasonId`, `courseIdBySlug`), `db/slug.ts` (`slugify`), `db/ingest.ts` (`timeToMs`), `wr/courses.ts` (`mkwrsNameToSlug`), `db/types.ts` (`ServerEvent`).

---

### Task 1: Scaffolding — dependency, script, config, player config

**Files:**
- Modify: `pi/package.json`
- Create: `pi/src/bot/config.ts`
- Create: `pi/src/bot/players.config.ts`
- Test: `pi/src/bot/config.test.ts`

- [ ] **Step 1: Add discord.js + the bot script**

In `pi/package.json`, add to `dependencies`: `"discord.js": "^14.16.0"`. Add to `scripts`:
```json
"bot": "node --no-warnings --import tsx src/bot/index.ts"
```

- [ ] **Step 2: Install**

Run: `cd pi && npm install`
Expected: `discord.js` added, no errors.

- [ ] **Step 3: Write the failing config test**

Create `pi/src/bot/config.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { loadConfig } from './config';

describe('loadConfig', () => {
  it('reads required + defaulted values', () => {
    const c = loadConfig({ DISCORD_BOT_TOKEN: 't', DISCORD_CHANNEL_ID: 'c' } as any);
    expect(c).toEqual({ token: 't', channelId: 'c', guildId: null, dbPath: 'mkw.db', wsUrl: 'ws://127.0.0.1:8787/v1/events' });
  });
  it('honours overrides', () => {
    const c = loadConfig({ DISCORD_BOT_TOKEN: 't', DISCORD_CHANNEL_ID: 'c', DISCORD_GUILD_ID: 'g', MKW_DB: '/x.db', PORT: '9000' } as any);
    expect(c.guildId).toBe('g'); expect(c.dbPath).toBe('/x.db');
    expect(c.wsUrl).toBe('ws://127.0.0.1:9000/v1/events');
  });
  it('throws when a required var is missing', () => {
    expect(() => loadConfig({ DISCORD_CHANNEL_ID: 'c' } as any)).toThrow(/DISCORD_BOT_TOKEN/);
    expect(() => loadConfig({ DISCORD_BOT_TOKEN: 't' } as any)).toThrow(/DISCORD_CHANNEL_ID/);
  });
});
```

- [ ] **Step 4: Run it (fails — no config.ts)**

Run: `cd pi && npx vitest run src/bot/config.test.ts`
Expected: FAIL (cannot find `./config`).

- [ ] **Step 5: Implement config.ts**

Create `pi/src/bot/config.ts`:
```ts
export type BotConfig = {
  token: string;
  channelId: string;
  guildId: string | null;
  dbPath: string;
  wsUrl: string;
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): BotConfig {
  const token = env.DISCORD_BOT_TOKEN ?? '';
  const channelId = env.DISCORD_CHANNEL_ID ?? '';
  if (!token) throw new Error('DISCORD_BOT_TOKEN is required');
  if (!channelId) throw new Error('DISCORD_CHANNEL_ID is required');
  const port = env.PORT ?? '8787';
  return {
    token,
    channelId,
    guildId: env.DISCORD_GUILD_ID ?? null,
    dbPath: env.MKW_DB ?? 'mkw.db',
    wsUrl: env.BOT_WS_URL ?? `ws://127.0.0.1:${port}/v1/events`,
  };
}
```

- [ ] **Step 6: Create players.config.ts**

Create `pi/src/bot/players.config.ts` (GIF lists copied verbatim from `legacy/mkwpb2/kart-off/services/discord_bot.py`; trim if any are dead links):
```ts
export const ID_TO_NAME: Record<string, string> = {
  '477788220982296576': 'Gub',
  '1213316126948335636': 'Paul',
  '201561251963207681': 'Alex',
  '267421165625147392': 'Aliias',
  '867421622347890719': 'Luke',
};

export const NAME_TO_ID: Record<string, string> =
  Object.fromEntries(Object.entries(ID_TO_NAME).map(([id, name]) => [name, id]));

export const THUMBNAIL_GIFS: Record<string, string[]> = {
  Paul: [
    'https://i.imgur.com/K9Qu1XM.gif', 'https://i.imgur.com/Wepl7A2.gif', 'https://i.imgur.com/kqiz9rj.gif',
    'https://i.imgur.com/oYFbGcD.gif', 'https://i.imgur.com/h3c0sli.gif', 'https://i.imgur.com/cBKo7cG.gif',
    'https://i.imgur.com/YHwWXsf.gif', 'https://i.imgur.com/aA4Gl9f.gif', 'https://i.imgur.com/DQxOwCS.gif',
    'https://i.imgur.com/JGHkIIS.gif', 'https://i.imgur.com/FtFhD6a.gif', 'https://i.imgur.com/ALgqFVz.gif',
    'https://i.imgur.com/7vjuvuq.gif',
  ],
  Aliias: ['https://i.imgur.com/lfS1SkJ.gif', 'https://i.imgur.com/l5eJXfl.gif', 'https://i.imgur.com/eiHaLw6.gif', 'https://i.imgur.com/KV8VW7x.gif'],
  Alex: ['https://i.imgur.com/0ZUvDVI.gif', 'https://i.imgur.com/OIPESbG.gif'],
  Luke: ['https://i.imgur.com/PcksQkq.gif', 'https://i.imgur.com/YadWWyh.gif', 'https://i.imgur.com/dK3KtfE.gif', 'https://i.imgur.com/SYlI3Tg.gif'],
  Gub: ['https://i.imgur.com/3u7SCNw.gif', 'https://i.imgur.com/nARULQI.gif'],
};

/** Random GIF for a player, or null when none is configured (legacy KeyError fix). */
export function gifFor(name: string): string | null {
  const list = THUMBNAIL_GIFS[name];
  return list && list.length ? list[Math.floor(Math.random() * list.length)] : null;
}

export function nameForId(id: string): string | null {
  return ID_TO_NAME[id] ?? null;
}
```

- [ ] **Step 7: Add a guard test for the GIF fallback**

Append to `pi/src/bot/config.test.ts`:
```ts
import { gifFor, nameForId } from './players.config';

describe('players.config', () => {
  it('gifFor returns null for an unknown player (no crash)', () => {
    expect(gifFor('NobodySpecial')).toBeNull();
  });
  it('gifFor returns a configured url for a known player', () => {
    expect(gifFor('Paul')).toMatch(/^https:\/\/i\.imgur\.com\//);
  });
  it('nameForId maps a known discord id', () => {
    expect(nameForId('1213316126948335636')).toBe('Paul');
    expect(nameForId('0')).toBeNull();
  });
});
```

- [ ] **Step 8: Run config + players tests (pass)**

Run: `cd pi && npx vitest run src/bot/config.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 9: Commit**

```bash
git add pi/package.json pi/package-lock.json pi/src/bot/config.ts pi/src/bot/players.config.ts pi/src/bot/config.test.ts
git commit -m "feat(bot): scaffolding - discord.js dep, bot script, config + player map"
```

---

### Task 2: format.ts — time-difference + duration

**Files:**
- Create: `pi/src/bot/format.ts`
- Test: `pi/src/bot/format.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/bot/format.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { formatTimeDifference, formatDuration } from './format';

describe('formatTimeDifference', () => {
  it('formats zero, positive, negative (ms)', () => {
    expect(formatTimeDifference(0)).toBe('±0.000s');
    expect(formatTimeDifference(1234)).toBe('+1.234s');
    expect(formatTimeDifference(-842)).toBe('-0.842s');
    expect(formatTimeDifference(123)).toBe('+0.123s');
  });
});

describe('formatDuration', () => {
  it('buckets to the largest whole unit, singular labels (legacy style)', () => {
    expect(formatDuration(5_000)).toBe('5 SECOND');
    expect(formatDuration(120_000)).toBe('2 MINUTE');
    expect(formatDuration(3 * 3600_000)).toBe('3 HOUR');
    expect(formatDuration(2 * 86400_000)).toBe('2 DAY');
    expect(formatDuration(40 * 86400_000)).toBe('1 MONTH');
    expect(formatDuration(400 * 86400_000)).toBe('1 YEAR');
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/format.test.ts`
Expected: FAIL (cannot find `./format`).

- [ ] **Step 3: Implement (the two functions)**

Create `pi/src/bot/format.ts`:
```ts
/** "+1.234s" / "-0.842s" / "±0.000s" — ports legacy TimeUtils.format_time_difference. */
export function formatTimeDifference(ms: number): string {
  if (ms === 0) return '±0.000s';
  const sign = ms > 0 ? '+' : '';
  return `${sign}${(ms / 1000).toFixed(3)}s`;
}

/** "3 DAY" / "2 HOUR" ... — ports legacy DiscordBot._format_duration (singular labels). */
export function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s} SECOND`;
  if (s < 3600) return `${Math.floor(s / 60)} MINUTE`;
  if (s < 86400) return `${Math.floor(s / 3600)} HOUR`;
  if (s < 2592000) return `${Math.floor(s / 86400)} DAY`;
  if (s < 31536000) return `${Math.floor(s / 2592000)} MONTH`;
  return `${Math.floor(s / 31536000)} YEAR`;
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/format.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/format.ts pi/src/bot/format.test.ts
git commit -m "feat(bot): time-difference + duration formatters"
```

---

### Task 3: types.ts + format.ts — overtaken + positions

**Files:**
- Create: `pi/src/bot/types.ts`
- Modify: `pi/src/bot/format.ts`
- Test: `pi/src/bot/format.test.ts`

- [ ] **Step 1: Create the shared types**

Create `pi/src/bot/types.ts`:
```ts
import type { ReignInfo } from '../db/reign';

export type OvertakenEntry = { name: string; diff_str: string };   // name 'WR' for the world record
export type StillAhead = { name: string; diff_str: string } | null;
export type Positions = {
  track: { old: number | null; new: number | null };
  total: { old: number | null; new: number | null };
};

export type PbEmbedData = {
  player: string;
  track: string;            // course display name
  time: string;             // total time string
  improvement_str: string;  // formatted delta vs the player's previous PB
  is_new_track_record: boolean;
  reign: ReignInfo;
  positions: Positions;
  overtaken: OvertakenEntry[];
  still_ahead: StillAhead;
};

export type WrEmbedData = {
  holder: string;
  track: string;                       // course display name
  record: string;                      // total time string
  improvement_str: string | null;      // null => "First WR"
  reign: ReignInfo;
};
```
> Note: `ReignInfo` is defined in Task 5 (`db/reign.ts`). vitest/tsx run without type-checking, so the type-only import does not block Task 3's tests; the type resolves once Task 5 lands.

- [ ] **Step 2: Write the failing test**

Append to `pi/src/bot/format.test.ts`:
```ts
import { formatOvertaken, formatPositions } from './format';

describe('formatOvertaken', () => {
  it('returns No-one when empty', () => {
    expect(formatOvertaken([])).toBe('`No-one`');
  });
  it('aligns names + decimals, WR kept as a name', () => {
    const out = formatOvertaken([
      { name: 'WR', diff_str: '+1.200s' },
      { name: 'Luke', diff_str: '+0.034s' },
    ]);
    expect(out).toBe('`WR    (+1.200s)`\n`Luke  (+0.034s)`');
  });
});

describe('formatPositions', () => {
  it('renders track + total transitions', () => {
    expect(formatPositions({ track: { old: 3, new: 1 }, total: { old: 4, new: 2 } }))
      .toBe('`Track: 3 → 1`\n`Total: 4 → 2`');
  });
  it('uses New when there is no old position', () => {
    expect(formatPositions({ track: { old: null, new: 1 }, total: { old: null, new: 5 } }))
      .toBe('`Track: New → 1`\n`Total: New → 5`');
  });
  it('omits an unchanged total and falls back to New record', () => {
    expect(formatPositions({ track: { old: null, new: null }, total: { old: 2, new: 2 } }))
      .toBe('`New record`');
  });
});
```

- [ ] **Step 2b: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/format.test.ts`
Expected: FAIL (`formatOvertaken`/`formatPositions` not exported).

- [ ] **Step 3: Implement (append to format.ts)**

Append to `pi/src/bot/format.ts`:
```ts
import type { OvertakenEntry, Positions } from './types';

function parseDiff(diff: string): { sign_and_whole: string; decimal: string } {
  if (diff.endsWith('s')) {
    const t = diff.slice(0, -1);
    if (t.includes('.')) { const [b, a] = t.split('.'); return { sign_and_whole: b, decimal: a }; }
    return { sign_and_whole: t, decimal: '000' };
  }
  return { sign_and_whole: diff, decimal: '' };
}

/** Monospace, name-padded, decimal-aligned overtaken list — ports legacy _format_overtaken. */
export function formatOvertaken(list: OvertakenEntry[]): string {
  if (list.length === 0) return '`No-one`';
  const names = list.map((p) => p.name);
  const maxName = Math.max(...names.map((n) => n.length));
  const parts = list.map((p) => parseDiff(p.diff_str));
  const maxBefore = Math.max(...parts.map((pt) => pt.sign_and_whole.length));
  return list.map((p, i) => {
    const name = names[i];
    const pt = parts[i];
    const padded = name + ' '.repeat(Math.max(2, maxName - name.length + 2));
    const before = pt.sign_and_whole.padStart(maxBefore);
    const aligned = pt.decimal ? `${before}.${pt.decimal}s` : `${before}s`;
    return `\`${padded}(${aligned})\``;
  }).join('\n');
}

/** Track/total position transitions — ports legacy _format_positions. */
export function formatPositions(pos: Positions): string {
  const t = pos.track;
  const o = pos.total;
  const lines: string[] = [];
  if (t.old && t.new) lines.push(`\`Track: ${t.old} → ${t.new}\``);
  else if (t.new) lines.push(`\`Track: New → ${t.new}\``);
  if (o.old && o.new) {
    if (o.old === o.new) return lines.length ? lines.join('\n') : '`New record`';
    lines.push(`\`Total: ${o.old} → ${o.new}\``);
  } else if (o.new) {
    lines.push(`\`Total: New → ${o.new}\``);
  }
  return lines.length ? lines.join('\n') : '`New record`';
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/format.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/types.ts pi/src/bot/format.ts pi/src/bot/format.test.ts
git commit -m "feat(bot): overtaken + positions formatters and embed-data types"
```

---

### Task 4: db/reign.ts — WR reign

**Files:**
- Create: `pi/src/db/reign.ts`
- Test: `pi/src/db/reign.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/db/reign.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { wrReign } from './reign';

function wrDb() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  // History oldest->newest. Luke held 2 records, then Paul takes it (current).
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES " +
    "(1,150,'Luke',110000,'1:50.000','2026-01-01T00:00:00.000Z',0)," +
    "(1,150,'Luke',108000,'1:48.000','2026-01-10T00:00:00.000Z',0)," +
    "(1,150,'Paul',107000,'1:47.000','2026-02-01T00:00:00.000Z',1)");
  return db;
}

describe('wrReign', () => {
  it('measures the dethroned holder reign from their first contiguous record', () => {
    const r = wrReign(wrDb(), 1, 150, 'Luke', 'Paul');
    expect(r?.previous_holder).toBe('Luke');
    expect(r?.is_same_person).toBe(false);
    // reign started 2026-01-01, so a positive duration
    expect(r!.reign_ms!).toBeGreaterThan(0);
  });
  it('flags is_same_person when the holder re-breaks their own WR', () => {
    const db = wrDb();
    db.exec("UPDATE world_records SET is_current=0");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES (1,150,'Paul',106000,'1:46.000','2026-03-01T00:00:00.000Z',1)");
    const r = wrReign(db, 1, 150, 'Paul', 'Paul');
    expect(r?.is_same_person).toBe(true);
    expect(r?.previous_holder).toBe('Paul');
  });
  it('returns null when there is no previous holder', () => {
    expect(wrReign(wrDb(), 1, 150, null, 'Paul')).toBeNull();
  });
}); 
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/db/reign.test.ts`
Expected: FAIL (cannot find `./reign`).

- [ ] **Step 3: Implement wrReign**

Create `pi/src/db/reign.ts`:
```ts
import type { DatabaseSync } from 'node:sqlite';

export type ReignInfo = {
  previous_holder: string | null;
  reign_ms: number | null;
  is_same_person: boolean;
} | null;

/** Reign of the holder being dethroned (prevHolder), from world_records history.
 *  Walks newest->oldest; the reign starts at the oldest contiguous prevHolder row.
 *  Graceful: null duration when timestamps are missing. */
export function wrReign(
  db: DatabaseSync, courseId: number, cc: number,
  prevHolder: string | null, newHolder: string | null,
): ReignInfo {
  if (!prevHolder) return null;
  const rows = db.prepare(
    `SELECT holder_name, achieved_at FROM world_records
     WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC`
  ).all(courseId, cc) as { holder_name: string | null; achieved_at: string | null }[];

  let reignStart: string | null = null;
  for (const r of rows) {
    if (r.holder_name === prevHolder) reignStart = r.achieved_at ?? reignStart;
    else if (reignStart !== null) break;   // passed the contiguous prevHolder block
  }
  const is_same_person = newHolder != null && newHolder === prevHolder;
  if (!reignStart) return { previous_holder: prevHolder, reign_ms: null, is_same_person };
  const ms = Date.now() - Date.parse(reignStart);
  return { previous_holder: prevHolder, reign_ms: Number.isFinite(ms) && ms >= 0 ? ms : null, is_same_person };
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/db/reign.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/reign.ts pi/src/db/reign.test.ts
git commit -m "feat(bot): WR reign query"
```

---

### Task 5: db/reign.ts — track (PB) reign via historical leadership

**Files:**
- Modify: `pi/src/db/reign.ts`
- Test: `pi/src/db/reign.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `pi/src/db/reign.test.ts`:
```ts
import { trackReign } from './reign';

function trackDb() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  // Luke leads from Jan (108000). Paul's new PB (run 99, 106000) just dethroned him.
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,is_pb) VALUES " +
    "(10,1,2,1,150,'finished','live',112000,'2026-01-01T00:00:00.000Z',0)," +
    "(11,1,2,1,150,'finished','live',108000,'2026-01-05T00:00:00.000Z',1)," +
    "(20,1,1,1,150,'finished','live',110000,'2026-01-10T00:00:00.000Z',0)," +
    "(99,1,1,1,150,'finished','live',106000,'2026-03-01T00:00:00.000Z',1)");
  return db;
}

describe('trackReign', () => {
  it('finds the dethroned leader and a positive reign, excluding the new PB run', () => {
    const r = trackReign(trackDb(), 1, 1, 150, 'Paul', 99);
    expect(r?.previous_holder).toBe('Luke');
    expect(r?.is_same_person).toBe(false);
    expect(r!.reign_ms!).toBeGreaterThan(0);
  });
  it('reports is_same_person when the leader improves their own best', () => {
    const db = trackDb();
    // Paul already led from Jan (107000, run 5); run 99 is his improvement.
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,is_pb) VALUES (5,1,1,1,150,'finished','live',107000,'2026-01-02T00:00:00.000Z',0)");
    const r = trackReign(db, 1, 1, 150, 'Paul', 99);
    expect(r?.previous_holder).toBe('Paul');
    expect(r?.is_same_person).toBe(true);
  });
  it('returns nulls when there is no prior finished run', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,is_pb) VALUES (99,1,1,1,150,'finished','live',106000,'2026-03-01T00:00:00.000Z',1)");
    expect(trackReign(db, 1, 1, 150, 'Paul', 99)).toEqual({ previous_holder: null, reign_ms: null, is_same_person: false });
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/db/reign.test.ts`
Expected: FAIL (`trackReign` not exported).

- [ ] **Step 3: Implement trackReign (append to reign.ts)**

Append to `pi/src/db/reign.ts`:
```ts
/** Reign of the course's current champion (excluding `excludeRunId`, the just-inserted PB).
 *  Best-times only improve, so the leaderboard is monotonic and a player's reign = the time
 *  since the lead last changed TO them. Single forward pass over finished runs: track each
 *  player's running best, and whenever the overall leader changes, reset the reign start to
 *  that run's timestamp. The leader at the end of the pass is the pre-new-PB champion. */
export function trackReign(
  db: DatabaseSync, seasonId: number, courseId: number, cc: number,
  newPlayer: string, excludeRunId: number,
): ReignInfo {
  const runs = db.prepare(
    `SELECT p.display_name AS name, r.total_time_ms AS ms, r.ended_at AS ended_at
     FROM runs r JOIN players p ON p.id = r.player_id
     WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.status='finished'
       AND r.total_time_ms IS NOT NULL AND r.ended_at IS NOT NULL AND r.id != ?
     ORDER BY r.ended_at ASC, r.id ASC`
  ).all(seasonId, courseId, cc, excludeRunId) as { name: string; ms: number; ended_at: string }[];
  if (runs.length === 0) return { previous_holder: null, reign_ms: null, is_same_person: false };

  const best = new Map<string, number>();
  let leader: string | null = null;
  let reignStart: string | null = null;
  for (const r of runs) {
    const cur = best.get(r.name);
    if (cur === undefined || r.ms < cur) best.set(r.name, r.ms);
    let lname: string | null = null;
    let lmin = Infinity;
    for (const [n, m] of best) if (m < lmin) { lmin = m; lname = n; }
    if (lname !== leader) { leader = lname; reignStart = r.ended_at; }   // the lead changed here
  }

  const is_same_person = leader === newPlayer;
  const reign_ms = reignStart ? Date.now() - Date.parse(reignStart) : null;
  return {
    previous_holder: leader,
    reign_ms: reign_ms != null && reign_ms >= 0 ? reign_ms : null,
    is_same_person,
  };
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/db/reign.test.ts`
Expected: PASS (all reign tests).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/reign.ts pi/src/db/reign.test.ts
git commit -m "feat(bot): track (PB) reign from historical leadership"
```

---

### Task 6: enrich.ts — buildWrData

**Files:**
- Create: `pi/src/bot/enrich.ts`
- Test: `pi/src/bot/enrich.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/bot/enrich.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { buildWrData } from './enrich';
import type { ServerEvent } from '../db/types';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_bros_circuit','Mario Bros. Circuit')");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES " +
    "(1,150,'Luke',100000,'1:40.000','2026-01-01T00:00:00.000Z',0)," +
    "(1,150,'Paul',99000,'1:39.000','2026-02-01T00:00:00.000Z',1)");
  return db;
}

const wrEvent: Extract<ServerEvent, { type: 'wr_update' }> = {
  type: 'wr_update', course: 'Mario Bros. Circuit', cc: 150, holder: 'Paul',
  total_time: '1:39.000', prev_holder: 'Luke', prev_time: '1:40.000',
  improvement_ms: 1000, character: null, vehicle: null, video_url: null,
};

describe('buildWrData', () => {
  it('resolves the course display name, formats the delta, and includes reign', () => {
    const d = buildWrData(db1(), wrEvent);
    expect(d.holder).toBe('Paul');
    expect(d.track).toBe('Mario Bros. Circuit');
    expect(d.record).toBe('1:39.000');
    expect(d.improvement_str).toBe('-1.000s');     // 1000ms faster, shown as a negative delta
    expect(d.reign?.previous_holder).toBe('Luke');
    expect(d.reign?.is_same_person).toBe(false);
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/enrich.test.ts`
Expected: FAIL (cannot find `./enrich`).

- [ ] **Step 3: Implement buildWrData**

Create `pi/src/bot/enrich.ts`:
```ts
import type { DatabaseSync } from 'node:sqlite';
import type { ServerEvent } from '../db/types';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { timeToMs } from '../db/ingest';
import { courseLeaderboard, overallLeaderboard, currentWr } from '../db/reads';
import { mkwrsNameToSlug } from '../wr/courses';
import { wrReign, trackReign } from '../db/reign';
import { formatTimeDifference } from './format';
import type { PbEmbedData, WrEmbedData, OvertakenEntry, StillAhead } from './types';

type PbEvent = Extract<ServerEvent, { type: 'pb_achieved' }>;
type WrEvent = Extract<ServerEvent, { type: 'wr_update' }>;

function courseDisplayName(db: DatabaseSync, courseId: number): string {
  const row = db.prepare('SELECT display_name FROM courses WHERE id=?').get(courseId) as { display_name: string } | undefined;
  return row?.display_name ?? '';
}

export function buildWrData(db: DatabaseSync, ev: WrEvent): WrEmbedData {
  const courseId = courseIdBySlug(db, mkwrsNameToSlug(ev.course));
  const track = courseId ? courseDisplayName(db, courseId) : ev.course;
  // improvement_ms = prev - new (positive = faster); show as new - prev to match the PB delta.
  const improvement_str = ev.improvement_ms != null ? formatTimeDifference(-ev.improvement_ms) : null;
  const reign = courseId ? wrReign(db, courseId, ev.cc, ev.prev_holder, ev.holder) : null;
  return { holder: ev.holder ?? 'Unknown', track, record: ev.total_time, improvement_str, reign };
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/enrich.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/enrich.ts pi/src/bot/enrich.test.ts
git commit -m "feat(bot): buildWrData enrichment"
```

---

### Task 7: enrich.ts — buildPbData

**Files:**
- Modify: `pi/src/bot/enrich.ts`
- Test: `pi/src/bot/enrich.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `pi/src/bot/enrich.test.ts`:
```ts
import { buildPbData } from './enrich';

function pbDb() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  // Post-PB leaderboard: Paul 1:46 (new PB, run 99) leads Luke 1:48. Paul's prev was 1:50.
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at,is_pb) VALUES " +
    "(50,1,1,1,150,'finished','live',110000,'1:50.000','2026-01-01T00:00:00.000Z',0)," +   // Paul old
    "(60,1,2,1,150,'finished','live',108000,'1:48.000','2026-01-02T00:00:00.000Z',1)," +   // Luke PB
    "(99,1,1,1,150,'finished','live',106000,'1:46.000','2026-03-01T00:00:00.000Z',1)");    // Paul new PB
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES (1,150,'SuperFX',100000,'1:40.000','2026-01-01T00:00:00.000Z',1)");
  return db;
}

const pbEvent: Extract<ServerEvent, { type: 'pb_achieved' }> = {
  type: 'pb_achieved', player: 'Paul', course: 'rr', cc: 150,
  total_time: '1:46.000', delta_vs_prev_ms: -4000, rank: 1,
};

describe('buildPbData', () => {
  it('computes track/total positions, overtaken, still-ahead (WR), and a track record', () => {
    const d = buildPbData(pbDb(), pbEvent);
    expect(d.track).toBe('Rainbow Road');
    expect(d.time).toBe('1:46.000');
    expect(d.improvement_str).toBe('-4.000s');
    expect(d.positions.track).toEqual({ old: 2, new: 1 });   // Paul was behind Luke, now leads
    expect(d.overtaken).toEqual([{ name: 'Luke', diff_str: '+2.000s' }]);   // passed Luke (108000-106000)
    expect(d.still_ahead).toEqual({ name: 'WR', diff_str: '-6.000s' });     // WR is faster (100000-106000)
    expect(d.is_new_track_record).toBe(true);
    expect(d.reign?.previous_holder).toBe('Luke');
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/enrich.test.ts`
Expected: FAIL (`buildPbData` not exported).

- [ ] **Step 3: Implement buildPbData (append to enrich.ts)**

Append to `pi/src/bot/enrich.ts`:
```ts
export function buildPbData(db: DatabaseSync, ev: PbEvent): PbEmbedData {
  const seasonId = activeSeasonId(db);
  const courseId = courseIdBySlug(db, slugify(ev.course));
  const track = courseId ? courseDisplayName(db, courseId) : ev.course;
  const newMs = timeToMs(ev.total_time) ?? 0;
  const prevMs = ev.delta_vs_prev_ms != null ? newMs - ev.delta_vs_prev_ms : null;
  const improvement_str = ev.delta_vs_prev_ms != null ? formatTimeDifference(ev.delta_vs_prev_ms) : '';

  const lb = courseId ? courseLeaderboard(db, seasonId, courseId, ev.cc) : [];
  const wr = courseId ? currentWr(db, courseId, ev.cc) : null;
  const others = lb.filter((r) => r.display_name !== ev.player);

  const newTrackPos = ev.rank;
  const oldTrackPos = prevMs == null ? null : others.filter((r) => r.total_time_ms < prevMs).length + 1;

  const overtaken: OvertakenEntry[] = [];
  if (wr && newMs < wr.record_ms) overtaken.push({ name: 'WR', diff_str: formatTimeDifference(wr.record_ms - newMs) });
  if (prevMs != null) for (const r of others)
    if (r.total_time_ms > newMs && r.total_time_ms < prevMs)
      overtaken.push({ name: r.display_name, diff_str: formatTimeDifference(r.total_time_ms - newMs) });

  let still_ahead: StillAhead = null;
  if (newTrackPos != null && newTrackPos > 1) {
    const ahead = lb[newTrackPos - 2];
    if (ahead && ahead.display_name !== ev.player)
      still_ahead = { name: ahead.display_name, diff_str: formatTimeDifference(ahead.total_time_ms - newMs) };
  } else if (wr && newMs > wr.record_ms) {
    still_ahead = { name: 'WR', diff_str: formatTimeDifference(wr.record_ms - newMs) };
  }

  const overall = overallLeaderboard(db, seasonId, ev.cc) as { display_name: string; total_time_ms: number }[];
  const myIdx = overall.findIndex((o) => o.display_name === ev.player);
  const newTotalPos = myIdx >= 0 ? myIdx + 1 : null;
  let oldTotalPos: number | null = null;
  if (myIdx >= 0 && prevMs != null) {
    const myOld = overall[myIdx].total_time_ms - newMs + prevMs;
    oldTotalPos = overall.filter((o) => o.display_name !== ev.player && o.total_time_ms < myOld).length + 1;
  }

  const is_new_track_record = newTrackPos === 1 && (oldTrackPos == null || oldTrackPos > 1);
  let reign = null;
  if (is_new_track_record && courseId) {
    const pbRun = db.prepare(
      `SELECT id FROM runs WHERE season_id=? AND player_id=(SELECT id FROM players WHERE display_name=?)
         AND course_id=? AND cc=? AND is_pb=1`
    ).get(seasonId, ev.player, courseId, ev.cc) as { id: number } | undefined;
    reign = trackReign(db, seasonId, courseId, ev.cc, ev.player, pbRun?.id ?? -1);
  }

  return {
    player: ev.player, track, time: ev.total_time, improvement_str, is_new_track_record, reign,
    positions: { track: { old: oldTrackPos, new: newTrackPos }, total: { old: oldTotalPos, new: newTotalPos } },
    overtaken, still_ahead,
  };
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/enrich.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/enrich.ts pi/src/bot/enrich.test.ts
git commit -m "feat(bot): buildPbData enrichment (positions, overtaken, still-ahead, reign)"
```

---

### Task 8: embeds/wr.ts

**Files:**
- Create: `pi/src/bot/embeds/wr.ts`
- Test: `pi/src/bot/embeds/wr.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/bot/embeds/wr.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { buildWrEmbed } from './wr';

describe('buildWrEmbed', () => {
  it('renders title/fields/colour and a reign footer (over)', () => {
    const e = buildWrEmbed({
      holder: 'Paul', track: 'Rainbow Road', record: '1:39.000',
      improvement_str: '-1.000s',
      reign: { previous_holder: 'Luke', reign_ms: 3 * 86400_000, is_same_person: false },
    }).toJSON();
    expect(e.title).toBe('WORLD RECORD BY PAUL');
    expect(e.color).toBe(0xf3f3f3);
    expect(e.fields).toEqual([
      { name: 'TRACK', value: '`Rainbow Road`', inline: true },
      { name: 'TIME', value: '`1:39.000`', inline: true },
      { name: 'DELTA', value: '`-1.000s`', inline: true },
    ]);
    expect(e.footer?.text).toBe('THE 3 DAY REIGN OF LUKE IS OVER');
  });
  it('shows First WR and no footer when there is no delta/reign', () => {
    const e = buildWrEmbed({ holder: 'Luke', track: 'RR', record: '1:40.000', improvement_str: null, reign: null }).toJSON();
    expect(e.fields?.[2]).toEqual({ name: 'DELTA', value: '`First WR`', inline: true });
    expect(e.footer).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/embeds/wr.test.ts`
Expected: FAIL (cannot find `./wr`).

- [ ] **Step 3: Implement buildWrEmbed**

Create `pi/src/bot/embeds/wr.ts`:
```ts
import { EmbedBuilder } from 'discord.js';
import type { WrEmbedData } from '../types';
import { formatDuration } from '../format';

/** Grey WR embed — ports legacy DiscordBot._send_wr_message. */
export function buildWrEmbed(d: WrEmbedData): EmbedBuilder {
  const e = new EmbedBuilder()
    .setTitle(`WORLD RECORD BY ${d.holder.toUpperCase()}`)
    .setColor(0xf3f3f3)
    .addFields(
      { name: 'TRACK', value: `\`${d.track}\``, inline: true },
      { name: 'TIME', value: `\`${d.record}\``, inline: true },
      { name: 'DELTA', value: `\`${d.improvement_str ?? 'First WR'}\``, inline: true },
    );
  if (d.reign && d.reign.reign_ms != null) {
    const dur = formatDuration(d.reign.reign_ms);
    const prev = (d.reign.previous_holder ?? '').toUpperCase();
    e.setFooter({ text: d.reign.is_same_person ? `THE ${dur} REIGN OF ${prev} CONTINUES` : `THE ${dur} REIGN OF ${prev} IS OVER` });
  }
  return e;
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/embeds/wr.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/embeds/wr.ts pi/src/bot/embeds/wr.test.ts
git commit -m "feat(bot): WR embed builder"
```

---

### Task 9: embeds/pb.ts

**Files:**
- Create: `pi/src/bot/embeds/pb.ts`
- Test: `pi/src/bot/embeds/pb.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/bot/embeds/pb.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { buildPbEmbed, pbTitle } from './pb';
import type { PbEmbedData } from '../types';

const base: PbEmbedData = {
  player: 'Paul', track: 'Rainbow Road', time: '1:46.000', improvement_str: '-4.000s',
  is_new_track_record: false, reign: null,
  positions: { track: { old: 2, new: 1 }, total: { old: 3, new: 2 } },
  overtaken: [{ name: 'Luke', diff_str: '+2.000s' }],
  still_ahead: { name: 'WR', diff_str: '-6.000s' },
};

describe('pbTitle', () => {
  it('uses "<NAME> PERSONAL BEST" when not a track record', () => {
    expect(pbTitle(base)).toBe('PAUL PERSONAL BEST');
  });
  it('uses NEW TRACK RECORD when a record without reign', () => {
    expect(pbTitle({ ...base, is_new_track_record: true, reign: null })).toBe('NEW TRACK RECORD');
  });
  it('uses the reign title when dethroning', () => {
    expect(pbTitle({ ...base, is_new_track_record: true, reign: { previous_holder: 'Luke', reign_ms: 2 * 86400_000, is_same_person: false } }))
      .toBe('THE 2 DAY REIGN OF LUKE IS OVER');
  });
});

describe('buildPbEmbed', () => {
  it('renders fields, colour, thumbnail, and the still-ahead footer', () => {
    const e = buildPbEmbed(base, { thumbnail: 'http://gif', footerIcon: 'http://icon' }).toJSON();
    expect(e.title).toBe('PAUL PERSONAL BEST');
    expect(e.color).toBe(0x6cca5f);
    expect(e.thumbnail?.url).toBe('http://gif');
    expect(e.fields).toEqual([
      { name: 'TRACK', value: '`Rainbow Road`' },
      { name: 'TIME', value: '`1:46.000`' },
      { name: 'DELTA', value: '`-4.000s`' },
      { name: 'OVERTOOK', value: '`Luke  (+2.000s)`', inline: true },   // two spaces: formatOvertaken pads max(2, ...)
      { name: 'POSITION', value: '`Track: 2 → 1`\n`Total: 3 → 2`', inline: true },
    ]);
    expect(e.footer).toEqual({ text: 'The WR is still ahead! (-6.000s)', icon_url: 'http://icon' });
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/embeds/pb.test.ts`
Expected: FAIL (cannot find `./pb`).

- [ ] **Step 3: Implement pb.ts**

Create `pi/src/bot/embeds/pb.ts`:
```ts
import { EmbedBuilder } from 'discord.js';
import type { PbEmbedData } from '../types';
import { formatDuration, formatOvertaken, formatPositions } from '../format';

/** PB title — ports legacy DiscordBot._generate_title with the "<NAME> PERSONAL BEST" change. */
export function pbTitle(d: PbEmbedData): string {
  if (!d.is_new_track_record) return `${d.player.toUpperCase()} PERSONAL BEST`;
  if (!d.reign || d.reign.reign_ms == null) return 'NEW TRACK RECORD';
  const dur = formatDuration(d.reign.reign_ms);
  const prev = (d.reign.previous_holder ?? '').toUpperCase();
  return d.reign.is_same_person ? `THE ${dur} REIGN OF ${prev} CONTINUES` : `THE ${dur} REIGN OF ${prev} IS OVER`;
}

/** Green PB embed — ports legacy DiscordBot._send_pb_message. GIF urls are injected so the
 *  builder stays deterministic (random selection happens in dispatch). */
export function buildPbEmbed(d: PbEmbedData, gifs: { thumbnail?: string | null; footerIcon?: string | null } = {}): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(pbTitle(d)).setColor(0x6cca5f);
  if (gifs.thumbnail) e.setThumbnail(gifs.thumbnail);
  e.addFields(
    { name: 'TRACK', value: `\`${d.track}\`` },
    { name: 'TIME', value: `\`${d.time}\`` },
    { name: 'DELTA', value: `\`${d.improvement_str}\`` },
    { name: 'OVERTOOK', value: formatOvertaken(d.overtaken), inline: true },
    { name: 'POSITION', value: formatPositions(d.positions), inline: true },
  );
  if (d.still_ahead) {
    const aheadName = d.still_ahead.name === 'WR' ? 'The WR' : d.still_ahead.name;
    e.setFooter({ text: `${aheadName} is still ahead! (${d.still_ahead.diff_str})`, ...(gifs.footerIcon ? { iconURL: gifs.footerIcon } : {}) });
  }
  return e;
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/embeds/pb.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/embeds/pb.ts pi/src/bot/embeds/pb.test.ts
git commit -m "feat(bot): PB embed builder + title change"
```

---

### Task 10: dispatch.ts

**Files:**
- Create: `pi/src/bot/dispatch.ts`
- Test: `pi/src/bot/dispatch.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/bot/dispatch.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import type { EmbedBuilder } from 'discord.js';
import { openDb, applySchema } from '../db/connect';
import { dispatch } from './dispatch';
import type { ServerEvent } from '../db/types';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at,is_pb) VALUES (99,1,1,1,150,'finished','live',106000,'1:46.000','2026-03-01T00:00:00.000Z',1)");
  return db;
}

describe('dispatch', () => {
  it('emits a PB embed for pb_achieved', () => {
    const sent: EmbedBuilder[] = [];
    const ev: ServerEvent = { type: 'pb_achieved', player: 'Paul', course: 'rr', cc: 150, total_time: '1:46.000', delta_vs_prev_ms: -4000, rank: 1 };
    dispatch(db1(), ev, (e) => sent.push(e));
    expect(sent).toHaveLength(1);
    expect(sent[0].toJSON().title).toBe('NEW TRACK RECORD');   // rank 1, no prior runs => track record
  });
  it('emits a WR embed for wr_update', () => {
    const sent: EmbedBuilder[] = [];
    const ev: ServerEvent = { type: 'wr_update', course: 'Rainbow Road', cc: 150, holder: 'Paul', total_time: '1:39.000', prev_holder: 'Luke', prev_time: '1:40.000', improvement_ms: 1000, character: null, vehicle: null, video_url: null };
    dispatch(db1(), ev, (e) => sent.push(e));
    expect(sent[0].toJSON().title).toBe('WORLD RECORD BY PAUL');
  });
  it('ignores unrelated events', () => {
    const sent: EmbedBuilder[] = [];
    dispatch(db1(), { type: 'run_started', player: 'Paul', course: 'rr', cc: 150 }, (e) => sent.push(e));
    expect(sent).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/dispatch.test.ts`
Expected: FAIL (cannot find `./dispatch`).

- [ ] **Step 3: Implement dispatch.ts**

Create `pi/src/bot/dispatch.ts`:
```ts
import type { DatabaseSync } from 'node:sqlite';
import type { EmbedBuilder } from 'discord.js';
import type { ServerEvent } from '../db/types';
import { buildPbData, buildWrData } from './enrich';
import { buildPbEmbed } from './embeds/pb';
import { buildWrEmbed } from './embeds/wr';
import { gifFor } from './players.config';

/** Build + emit the right embed for the events we announce; ignore the rest. One failure
 *  logs and does not throw (so a bad event can't take down the stream). */
export function dispatch(db: DatabaseSync, ev: ServerEvent, send: (e: EmbedBuilder) => void): void {
  try {
    if (ev.type === 'pb_achieved') {
      const d = buildPbData(db, ev);
      const footerIcon = d.still_ahead ? gifFor(d.still_ahead.name === 'WR' ? d.player : d.still_ahead.name) : null;
      send(buildPbEmbed(d, { thumbnail: gifFor(d.player), footerIcon }));
    } else if (ev.type === 'wr_update') {
      send(buildWrEmbed(buildWrData(db, ev)));
    }
  } catch (err) {
    console.error('[bot] dispatch failed', err);
  }
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/dispatch.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/dispatch.ts pi/src/bot/dispatch.test.ts
git commit -m "feat(bot): event dispatch -> embed"
```

---

### Task 11: ws.ts — reconnecting WebSocket client

**Files:**
- Create: `pi/src/bot/ws.ts`
- Test: `pi/src/bot/ws.test.ts`

- [ ] **Step 1: Write the failing test (pure parser only)**

Create `pi/src/bot/ws.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { parseEvent } from './ws';

describe('parseEvent', () => {
  it('parses a valid ServerEvent', () => {
    expect(parseEvent('{"type":"pb_achieved","player":"Paul"}')?.type).toBe('pb_achieved');
  });
  it('returns null for non-JSON or shapeless payloads', () => {
    expect(parseEvent('not json')).toBeNull();
    expect(parseEvent('{"no":"type"}')).toBeNull();
  });
});
```

- [ ] **Step 2: Run it (fails)**

Run: `cd pi && npx vitest run src/bot/ws.test.ts`
Expected: FAIL (cannot find `./ws`).

- [ ] **Step 3: Implement ws.ts**

Create `pi/src/bot/ws.ts` (uses Node's built-in global `WebSocket`, Node >= 22):
```ts
import type { ServerEvent } from '../db/types';

/** Parse a WS frame into a ServerEvent, or null if it is not one. */
export function parseEvent(data: string): ServerEvent | null {
  try {
    const o = JSON.parse(data);
    return o && typeof o.type === 'string' ? (o as ServerEvent) : null;
  } catch {
    return null;
  }
}

/** Connect to the server's /v1/events stream and call onEvent for each event.
 *  Reconnects with capped exponential backoff. Returns a closer. */
export function startEventStream(
  url: string,
  onEvent: (e: ServerEvent) => void,
  log: (m: string) => void = console.log,
): { close(): void } {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 1000;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(url);
    ws.addEventListener('open', () => { log(`[bot] ws connected ${url}`); backoff = 1000; });
    ws.addEventListener('message', (ev: MessageEvent) => {
      const e = parseEvent(typeof ev.data === 'string' ? ev.data : String(ev.data));
      if (e) onEvent(e);
    });
    ws.addEventListener('close', () => {
      if (closed) return;
      log(`[bot] ws closed; reconnecting in ${backoff}ms`);
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 30000);
    });
    ws.addEventListener('error', () => { try { ws?.close(); } catch { /* ignore */ } });
  };

  connect();
  return { close() { closed = true; try { ws?.close(); } catch { /* ignore */ } } };
}
```

- [ ] **Step 4: Run it (pass)**

Run: `cd pi && npx vitest run src/bot/ws.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/ws.ts pi/src/bot/ws.test.ts
git commit -m "feat(bot): reconnecting WebSocket event client"
```

---

### Task 12: client.ts — discord.js Announcer (ready-buffer + send)

**Files:**
- Create: `pi/src/bot/client.ts`

> No unit test: this is a thin discord.js I/O shell verified by the smoke run in Task 13. Keep all testable logic in the already-tested pure modules.

- [ ] **Step 1: Implement client.ts**

Create `pi/src/bot/client.ts`:
```ts
import { Client, GatewayIntentBits, type EmbedBuilder, type SendableChannels } from 'discord.js';

/** Owns the discord.js client. Buffers embeds that arrive before the gateway is ready and
 *  flushes them on 'ready' (ports the legacy message_queue behaviour). */
export class Announcer {
  private client: Client;
  private channel: SendableChannels | null = null;
  private ready = false;
  private queue: EmbedBuilder[] = [];

  constructor(private token: string, private channelId: string) {
    this.client = new Client({ intents: [GatewayIntentBits.Guilds] });
    this.client.once('ready', async () => {
      const ch = await this.client.channels.fetch(this.channelId).catch(() => null);
      this.channel = ch && ch.isSendable() ? ch : null;
      if (!this.channel) console.error(`[bot] channel ${this.channelId} not found or not sendable`);
      this.ready = true;
      for (const e of this.queue) await this.post(e);
      this.queue = [];
      console.log(`[bot] logged in as ${this.client.user?.tag}`);
    });
  }

  async start(): Promise<void> { await this.client.login(this.token); }

  async send(embed: EmbedBuilder): Promise<void> {
    if (!this.ready || !this.channel) { this.queue.push(embed); return; }
    await this.post(embed);
  }

  private async post(embed: EmbedBuilder): Promise<void> {
    try { await this.channel!.send({ embeds: [embed] }); }
    catch (err) { console.error('[bot] send failed', err); }
  }
}
```

- [ ] **Step 2: Confirm it compiles/loads**

Run: `cd pi && node --no-warnings --import tsx -e "import('./src/bot/client.ts').then(() => console.log('ok'))"`
Expected: prints `ok` (module loads, no import errors).

- [ ] **Step 3: Commit**

```bash
git add pi/src/bot/client.ts
git commit -m "feat(bot): discord.js Announcer with ready-buffer"
```

---

### Task 13: index.ts — wiring + smoke run

**Files:**
- Create: `pi/src/bot/index.ts`
- Create: `pi/.env.example`

- [ ] **Step 1: Implement index.ts**

Create `pi/src/bot/index.ts`:
```ts
import { openDb } from '../db/connect';
import { loadConfig } from './config';
import { Announcer } from './client';
import { startEventStream } from './ws';
import { dispatch } from './dispatch';

const cfg = loadConfig();
const db = openDb(cfg.dbPath);                 // shared with the server (WAL); reads only
const announcer = new Announcer(cfg.token, cfg.channelId);

announcer.start().catch((err) => { console.error('[bot] login failed', err); process.exit(1); });
const stream = startEventStream(cfg.wsUrl, (ev) => dispatch(db, ev, (embed) => { void announcer.send(embed); }));

const shutdown = () => { stream.close(); process.exit(0); };
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
console.log(`[bot] started; ws=${cfg.wsUrl} db=${cfg.dbPath}`);
```

- [ ] **Step 2: Create env example**

Create `pi/.env.example`:
```
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
DISCORD_GUILD_ID=
MKW_DB=mkw.db
PORT=8787
# BOT_WS_URL=ws://127.0.0.1:8787/v1/events
```

- [ ] **Step 3: Full test suite still green**

Run: `cd pi && npm test`
Expected: PASS (all existing + new bot tests).

- [ ] **Step 4: Smoke run (manual, requires a real bot token + channel)**

Start the server in one shell: `cd pi && npm run dev`
In another shell with `pi/.env` filled in:
Run: `cd pi && node --env-file=.env --no-warnings --import tsx src/bot/index.ts`
Expected: logs `[bot] started`, then `[bot] ws connected ...` and `[bot] logged in as <tag>`.
Then exercise a PB: `POST /v1/runs` with a finished PB (or trigger a `wr_update`) and confirm the embed posts to the channel. (If you have no token yet, skip the live post; the unit tests already cover the embed content.)

- [ ] **Step 5: Commit**

```bash
git add pi/src/bot/index.ts pi/.env.example
git commit -m "feat(bot): entry point wiring + env example"
```

---

### Task 14: Docs + memory

**Files:**
- Create: `pi/src/bot/README.md`
- Modify: `C:\Users\Paul\.claude\projects\C--development-mkw-split-rewrite\memory\MEMORY.md` (+ a new memory file)

- [ ] **Step 1: Write the bot README**

Create `pi/src/bot/README.md` documenting: purpose (announce PB/WR from the server), run command (`npm run bot` with `pi/.env`), env vars, that it reads the shared `mkw.db` and consumes `/v1/events`, the player config location (`players.config.ts`), and that Stage 2 adds slash commands.

- [ ] **Step 2: Update memory**

Add a memory file `discord-bot-stage1.md` summarising Stage 1 (TS/discord.js bot in `pi/src/bot/`, WS + shared-DB reads, reign ported, "<NAME> PERSONAL BEST", staged with Stage 2 = slash commands) and a one-line pointer in `MEMORY.md` under the Client->Server Shift section.

- [ ] **Step 3: Commit**

```bash
git add pi/src/bot/README.md
git commit -m "docs(bot): Stage 1 README"
```

---

## Self-Review

**Spec coverage:**
- TS/discord.js bot in `pi/src/bot/`, separate process — Tasks 1, 12, 13. ✓
- Events via `/v1/events` WS client importing `ServerEvent` — Task 11. ✓
- Shared-DB reads (no server changes) — reuses `db/`, Tasks 6–7; reign in `db/reign.ts` Tasks 4–5. ✓
- PB embed with overtaken/positions/still-ahead + recomputation — Tasks 7, 9. ✓
- WR embed with mkwrs-name resolution + reign — Tasks 6, 8. ✓
- Reign (PB + WR) with graceful degradation — Tasks 4, 5. ✓
- Title `<NAME> PERSONAL BEST` — Task 9 (`pbTitle`). ✓
- GIF KeyError fix / defensive identity — Task 1 (`gifFor`/`nameForId`). ✓
- WS reconnect + pre-ready buffering — Tasks 11, 12. ✓
- Config via env, player config committed — Task 1, 13. ✓
- Snapshot/format/reign tests — Tasks 2, 3, 4, 5, 8, 9, 10. ✓
- Slash commands — **deferred to Stage 2 plan** (out of scope here). ✓
- Operational note (systemd) — covered by README (Task 14) + spec. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `ReignInfo` defined in `db/reign.ts` (Task 4), imported by `bot/types.ts` (Task 3) and used by enrich/embeds — consistent. `PbEmbedData`/`WrEmbedData`/`Positions`/`OvertakenEntry`/`StillAhead` defined in `bot/types.ts` (Task 3), used in Tasks 6–10. `buildPbData`/`buildWrData` (enrich) → `buildPbEmbed`/`buildWrEmbed` (embeds) → `dispatch` — names match across tasks. `startEventStream`/`parseEvent` (ws) and `Announcer.send` (client) match their use in `index.ts`. `gifFor`/`nameForId` consistent between Task 1 and Task 10.

**Note on test ordering:** Task 3 creates `bot/types.ts` which type-imports `ReignInfo` from `db/reign.ts` (created in Task 4). vitest + tsx strip types without checking, so Task 3's tests run regardless; the type resolves once Task 4 lands. If executing strictly in order, this is fine.
