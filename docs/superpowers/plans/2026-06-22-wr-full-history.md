# WR Full-History Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scrape every mkwrs `display.php` per-track WR history page into the `world_records` table — full progression with per-lap splits, coins, mushrooms, character/costume/kart, nation, date, and video — with name-mismatch flagging, plus a bulk CLI and a slow drip verifier.

**Architecture:** New per-track history layer under `pi/src/wr/` alongside the unchanged main-page scraper. A header-driven parser handles the 4 verified table variants (flat 3-lap; stacked 4/5/6-lap) and skips interleaved patch rows. A reconcile step mirrors each course's history into `world_records` (mkwrs is the single source of truth). Names resolve via canonical slug sets + alias maps; anything unresolved is flagged.

**Tech Stack:** TypeScript (ESM), `node:sqlite` `DatabaseSync`, `node-html-parser`, `vitest`, `tsx`.

## Global Constraints

- **Module system:** ESM (`pi/package.json` has `"type": "module"`); use `import`/`export`, `.ts` extensions omitted in imports.
- **Reuse, do not re-implement:** `slugify` from `../db/slug`; `mkwrsTimeToMs` + `msToTimeStr` from `./time`; `resolveCourseId` from `./courses`.
- **mkwrs is the single source of truth.** Nothing in `world_records` predates its per-course history. Removal/DQ applies across **all provenances**; removal is **soft** (`removed_at`, never hard delete).
- **cc default = 150** everywhere.
- **Migrations are additive:** `pi/src/db/connect.ts:applySchema` uses `try { db.exec('ALTER TABLE … ADD COLUMN …') } catch { /* present */ }`, one per column. Mirror new columns into `server/schema.sql` for the canonical schema.
- **Politeness (network code only):** browser User-Agent, `Referer: https://mkwrs.com/mkworld/`, sequential requests, per-request `AbortController` + `clearTimeout` (never `AbortSignal.timeout()` — it leaves a pending timer that trips libuv teardown on Windows). No `process.exit()` in CLIs; set `process.exitCode` on failure.
- **Test runner:** from `pi/`, `npm test` (all) or `npx vitest run src/wr/<file>.test.ts` (one file). Fixtures load via `readFileSync(new URL('./__fixtures__/…', import.meta.url), 'utf8')`.
- **Commit** after each task with a `feat(wr):` / `test(wr):` message.

---

### Task 1: DB migration — history columns + flags/meta tables

**Files:**
- Modify: `pi/src/db/connect.ts` (inside `applySchema`, after the existing `idx_wr_current` line)
- Modify: `server/schema.sql` (the `world_records` table + two new tables)
- Test: `pi/src/db/historySchema.test.ts`

**Interfaces:**
- Produces: `world_records` gains columns `nation, character_slug, kart_slug, costume_slug, lap_splits_ms, coins, mushrooms, date_precision, removed_at, source_raw` (all TEXT, nullable). New tables `wr_name_flags` and `wr_meta` (see DDL below). All reachable after `applySchema(db)`.

- [ ] **Step 1: Write the failing test**

Create `pi/src/db/historySchema.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

function cols(db: any, table: string): Set<string> {
  return new Set((db.prepare(`PRAGMA table_info(${table})`).all() as any[]).map((r) => r.name));
}

describe('history schema migration', () => {
  const db = openDb(':memory:');
  applySchema(db);

  it('adds the history columns to world_records', () => {
    const c = cols(db, 'world_records');
    for (const name of ['nation', 'character_slug', 'kart_slug', 'costume_slug',
      'lap_splits_ms', 'coins', 'mushrooms', 'date_precision', 'removed_at', 'source_raw']) {
      expect(c.has(name)).toBe(true);
    }
  });

  it('creates wr_name_flags with a unique (category, raw_value)', () => {
    db.exec(`INSERT INTO wr_name_flags(category, raw_value) VALUES ('kart','X')`);
    expect(() => db.exec(`INSERT INTO wr_name_flags(category, raw_value) VALUES ('kart','X')`)).toThrow();
  });

  it('creates wr_meta key/value', () => {
    db.exec(`INSERT INTO wr_meta(key, value) VALUES ('history_cursor','3')`);
    const row = db.prepare(`SELECT value FROM wr_meta WHERE key='history_cursor'`).get() as any;
    expect(row.value).toBe('3');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/db/historySchema.test.ts`
Expected: FAIL (columns/tables absent).

- [ ] **Step 3: Add the migration to `applySchema`**

In `pi/src/db/connect.ts`, immediately after the existing `db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_wr_current …')` line, insert:

```typescript
  // --- WR full-history capture (additive) ---
  for (const col of [
    'nation TEXT', 'character_slug TEXT', 'kart_slug TEXT', 'costume_slug TEXT',
    'lap_splits_ms TEXT', 'coins TEXT', 'mushrooms TEXT',
    'date_precision TEXT', 'removed_at TEXT', 'source_raw TEXT',
  ]) {
    try { db.exec(`ALTER TABLE world_records ADD COLUMN ${col}`); } catch { /* present */ }
  }
  db.exec(`CREATE TABLE IF NOT EXISTS wr_name_flags (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    slug_guess TEXT,
    example_course_id INTEGER,
    example_wr_id INTEGER,
    occurrences INTEGER NOT NULL DEFAULT 1,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(category, raw_value)
  )`);
  db.exec(`CREATE TABLE IF NOT EXISTS wr_meta (key TEXT PRIMARY KEY, value TEXT)`);
```

- [ ] **Step 4: Mirror into `server/schema.sql`**

In `server/schema.sql`, add the ten columns to the `world_records` `CREATE TABLE` (after `vehicle TEXT,`):

```sql
    nation         TEXT,
    character_slug TEXT,
    kart_slug      TEXT,
    costume_slug   TEXT,
    lap_splits_ms  TEXT,
    coins          TEXT,
    mushrooms      TEXT,
    date_precision TEXT,
    removed_at     TEXT,
    source_raw     TEXT,
```

And after the `world_records` table block add:

```sql
CREATE TABLE IF NOT EXISTS wr_name_flags (
    id                INTEGER PRIMARY KEY,
    category          TEXT NOT NULL,
    raw_value         TEXT NOT NULL,
    slug_guess        TEXT,
    example_course_id INTEGER,
    example_wr_id     INTEGER,
    occurrences       INTEGER NOT NULL DEFAULT 1,
    resolved_at       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(category, raw_value)
);
CREATE TABLE IF NOT EXISTS wr_meta (key TEXT PRIMARY KEY, value TEXT);
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd pi && npx vitest run src/db/historySchema.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add pi/src/db/connect.ts server/schema.sql pi/src/db/historySchema.test.ts
git commit -m "feat(wr): add world_records history columns + wr_name_flags/wr_meta tables"
```

---

### Task 2: `lap.ts` — lap-time and per-lap parsing

**Files:**
- Create: `pi/src/wr/lap.ts`
- Test: `pi/src/wr/lap.test.ts`

**Interfaces:**
- Produces: `lapTimeToMs(raw: string): number | null` (handles `SS.mmm`, `M:SS.mmm`, `-`/empty → null); `parsePerLap(raw: string): number[] | null` (`"8-12-0-0"` → `[8,12,0,0]`; `-`/empty → null).

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/lap.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { lapTimeToMs, parsePerLap } from './lap';

describe('lapTimeToMs', () => {
  it('parses SS.mmm', () => { expect(lapTimeToMs('37.000')).toBe(37000); });
  it('parses sub-minute with real ms', () => { expect(lapTimeToMs('35.263')).toBe(35263); });
  it('parses M:SS.mmm', () => { expect(lapTimeToMs('1:13.164')).toBe(73164); });
  it('returns null for dash and empty', () => {
    expect(lapTimeToMs('-')).toBeNull();
    expect(lapTimeToMs('')).toBeNull();
  });
});

describe('parsePerLap', () => {
  it('splits single-digit per-lap', () => { expect(parsePerLap('8-0-0')).toEqual([8, 0, 0]); });
  it('handles multi-digit', () => { expect(parsePerLap('8-12-0-0')).toEqual([8, 12, 0, 0]); });
  it('returns null for dash and empty', () => {
    expect(parsePerLap('-')).toBeNull();
    expect(parsePerLap('')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/lap.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `lap.ts`**

Create `pi/src/wr/lap.ts`:

```typescript
/** Parse a lap time `SS.mmm` or `M:SS.mmm` (long tracks) into ms. `-`/empty → null. */
export function lapTimeToMs(raw: string): number | null {
  const t = raw.trim();
  if (!t || t === '-') return null;
  let m = /^(\d+):(\d{1,2})\.(\d{1,3})$/.exec(t);
  if (m) return Number(m[1]) * 60000 + Number(m[2]) * 1000 + Number(m[3].padEnd(3, '0'));
  m = /^(\d{1,2})\.(\d{1,3})$/.exec(t);
  if (m) return Number(m[1]) * 1000 + Number(m[2].padEnd(3, '0'));
  return null;
}

/** Parse a per-lap field like `8-12-0-0` into `[8,12,0,0]`. `-`/empty → null. */
export function parsePerLap(raw: string): number[] | null {
  const t = raw.trim();
  if (!t || t === '-') return null;
  const nums = t.split('-').map((p) => Number(p));
  return nums.some((n) => !Number.isFinite(n)) ? null : nums;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/lap.test.ts`
Expected: PASS (7 assertions).

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/lap.ts pi/src/wr/lap.test.ts
git commit -m "feat(wr): lap-time + per-lap (coins/mushrooms) parsers"
```

---

### Task 3: `roster.ts` — canonical sets + alias maps + `resolveItem`

**Files:**
- Create: `pi/src/wr/roster.ts`
- Test: `pi/src/wr/roster.test.ts`

**Interfaces:**
- Produces: `type ItemCategory = 'character' | 'kart' | 'costume'`; `resolveItem(category: ItemCategory, raw: string): { slug: string | null; slugGuess: string }`. Exports `CHARACTERS`, `KARTS`, `COSTUMES` (`Set<string>`) and `KART_ALIASES` (`Record<string,string>`).
- Consumes: `slugify` from `../db/slug`.

> The slug sets below are the basenames of `captures/en_uk/{characters,karts,costumes}/*.png` (regenerate from there if the roster changes). The 3 kart aliases were verified against all 30 live pages — with them seeded, every character/costume/kart in the current data resolves.

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/roster.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { resolveItem } from './roster';

describe('resolveItem', () => {
  it('resolves a plain character by slug', () => {
    expect(resolveItem('character', 'Baby Daisy').slug).toBe('baby_daisy');
    expect(resolveItem('character', 'Para-Biddybud').slug).toBe('para_biddybud');
  });
  it('resolves a costume by slug', () => {
    expect(resolveItem('costume', 'Conductor').slug).toBe('conductor');
  });
  it('resolves karts by slug and by alias', () => {
    expect(resolveItem('kart', 'Mach Rocket').slug).toBe('mach_rocket');
    expect(resolveItem('kart', 'R.O.B. H.O.G.').slug).toBe('rob_hog');     // slug r_o_b_h_o_g
    expect(resolveItem('kart', 'Biddybuggy').slug).toBe('buggybud');
    expect(resolveItem('kart', 'Tiny Titan').slug).toBe('rally_romper');
  });
  it('returns null + slugGuess for an unknown name', () => {
    const r = resolveItem('kart', 'Totally Fake Kart');
    expect(r.slug).toBeNull();
    expect(r.slugGuess).toBe('totally_fake_kart');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/roster.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `roster.ts`**

Create `pi/src/wr/roster.ts`:

```typescript
import { slugify } from '../db/slug';

export type ItemCategory = 'character' | 'kart' | 'costume';

export const CHARACTERS = new Set<string>([
  'baby_daisy', 'baby_luigi', 'baby_mario', 'baby_peach', 'baby_rosalina', 'birdo', 'bowser',
  'bowser_jr', 'cataquack', 'chargin_chuck', 'cheep_cheep', 'coin_coffer', 'conkdor', 'cow',
  'daisy', 'dolphin', 'donkey_kong', 'dry_bones', 'fish_bone', 'goomba', 'hammer_bro', 'king_boo',
  'koopa_troopa', 'lakitu', 'luigi', 'mario', 'monty_mole', 'nabbit', 'para_biddybud', 'pauline',
  'peach', 'peepa', 'penguin', 'pianta', 'piranha_plant', 'pokey', 'rocky_wrench', 'rosalina',
  'shy_guy', 'sidestepper', 'snowman', 'spike', 'stingby', 'swoop', 'waluigi', 'wario', 'wiggler',
  'yoshi', 'toad', 'toadette',
]);

export const KARTS = new Set<string>([
  'b_dasher', 'baby_blooper', 'big_horn', 'billdozer', 'blastronaut_iii', 'bowser_bruiser',
  'buggybud', 'bumble_v', 'carpet_flyer', 'chargin_truck', 'cloud_9', 'cute_scoot',
  'dolphin_dasher', 'dread_sled', 'fin_twin', 'funky_dorrie', 'hot_rod', 'hyper_pipe',
  'junkyard_hog', 'lil_dumpy', 'lobster_roller', 'loco_moto', 'mach_rocket', 'mecha_trike',
  'pipe_frame', 'plushbuggy', 'rally_bike', 'rally_kart', 'rally_romper', 'rallygator',
  'reel_racer', 'ribbit_revster', 'roadster_royale', 'rob_hog', 'standard_bike', 'standard_kart',
  'stellar_sled', 'tune_thumper', 'w_twin_chopper', 'zoom_buggy',
]);

export const COSTUMES = new Set<string>([
  'aero', 'all_terrain', 'aristocrat', 'aurora', 'aviator', 'biker', 'biker_jr', 'burger_bud',
  'conductor', 'cowboy', 'dune_rider', 'farmer', 'fisherman', 'food_slinger', 'gondolier', 'happi',
  'mariachi', 'matsuri', 'mechanic', 'oasis', 'pirate', 'pit_crew', 'pro_racer', 'road_ruffian',
  'runner', 'sailor', 'sightseeing', 'slope_styler', 'soft_server', 'supercharged', 'swimwear',
  'touring', 'vacation', 'wampire', 'wicked_wasp', 'work_crew', 'yukata', 'engineer', 'explorer',
]);

/** User-editable alias maps, keyed by slugify(rawName) → canonical slug. mkwrs uses MK8-era
 *  display names for a few returning karts; our roster uses the in-game MKWorld names. */
export const CHARACTER_ALIASES: Record<string, string> = {};
export const KART_ALIASES: Record<string, string> = {
  r_o_b_h_o_g: 'rob_hog',       // 'R.O.B. H.O.G.'
  biddybuggy: 'buggybud',       // 'Biddybuggy'  -> Buggybud
  tiny_titan: 'rally_romper',   // 'Tiny Titan'  -> Rally Romper
};
export const COSTUME_ALIASES: Record<string, string> = {};

const TABLE: Record<ItemCategory, { set: Set<string>; aliases: Record<string, string> }> = {
  character: { set: CHARACTERS, aliases: CHARACTER_ALIASES },
  kart: { set: KARTS, aliases: KART_ALIASES },
  costume: { set: COSTUMES, aliases: COSTUME_ALIASES },
};

/** Resolve a raw mkwrs name to a canonical slug. slugify → canonical set → alias map → null. */
export function resolveItem(category: ItemCategory, raw: string): { slug: string | null; slugGuess: string } {
  const slugGuess = slugify(raw);
  const { set, aliases } = TABLE[category];
  if (set.has(slugGuess)) return { slug: slugGuess, slugGuess };
  if (aliases[slugGuess]) return { slug: aliases[slugGuess], slugGuess };
  return { slug: null, slugGuess };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/roster.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/roster.ts pi/src/wr/roster.test.ts
git commit -m "feat(wr): canonical char/kart/costume roster + alias resolution"
```

---

### Task 4: `flags.ts` — name-mismatch flags

**Files:**
- Create: `pi/src/wr/flags.ts`
- Test: `pi/src/wr/flags.test.ts`

**Interfaces:**
- Consumes: Task 1 schema (`wr_name_flags`); `resolveItem`, `ItemCategory` from `./roster`.
- Produces:
  - `upsertFlag(db, f: { category: ItemCategory | 'course'; rawValue: string; slugGuess?: string; exampleCourseId?: number; exampleWrId?: number }): void`
  - `resolveFlags(db): number` — re-checks unresolved flags via `resolveItem`; stamps `resolved_at`; returns how many it resolved.
  - `reportFlags(db): string` — human-readable list of unresolved flags grouped by category.

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/flags.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { upsertFlag, resolveFlags, reportFlags } from './flags';

function freshDb() { const db = openDb(':memory:'); applySchema(db); return db; }

describe('flags', () => {
  it('inserts then increments occurrences on the same (category, raw_value)', () => {
    const db = freshDb();
    upsertFlag(db, { category: 'kart', rawValue: 'Mystery Kart', slugGuess: 'mystery_kart' });
    upsertFlag(db, { category: 'kart', rawValue: 'Mystery Kart', slugGuess: 'mystery_kart' });
    const row = db.prepare(`SELECT occurrences FROM wr_name_flags WHERE raw_value='Mystery Kart'`).get() as any;
    expect(row.occurrences).toBe(2);
  });

  it('resolveFlags stamps resolved_at for names now resolvable (alias) and reportFlags hides them', () => {
    const db = freshDb();
    upsertFlag(db, { category: 'kart', rawValue: 'R.O.B. H.O.G.', slugGuess: 'r_o_b_h_o_g' });
    upsertFlag(db, { category: 'kart', rawValue: 'Still Unknown', slugGuess: 'still_unknown' });
    expect(resolveFlags(db)).toBe(1);                       // R.O.B. resolves via alias
    expect(reportFlags(db)).toContain('Still Unknown');
    expect(reportFlags(db)).not.toContain('R.O.B. H.O.G.');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/flags.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `flags.ts`**

Create `pi/src/wr/flags.ts`:

```typescript
import type { DatabaseSync } from 'node:sqlite';
import { resolveItem, type ItemCategory } from './roster';

export type FlagInput = {
  category: ItemCategory | 'course';
  rawValue: string;
  slugGuess?: string;
  exampleCourseId?: number;
  exampleWrId?: number;
};

/** Record an unresolved name. Idempotent on (category, raw_value): increments occurrences and
 *  clears any stale resolved_at (it is unresolved again right now). */
export function upsertFlag(db: DatabaseSync, f: FlagInput): void {
  db.prepare(
    `INSERT INTO wr_name_flags(category, raw_value, slug_guess, example_course_id, example_wr_id, occurrences)
     VALUES (?,?,?,?,?,1)
     ON CONFLICT(category, raw_value) DO UPDATE SET
       occurrences = occurrences + 1,
       slug_guess = excluded.slug_guess,
       resolved_at = NULL`
  ).run(f.category, f.rawValue, f.slugGuess ?? null, f.exampleCourseId ?? null, f.exampleWrId ?? null);
}

/** Re-check every unresolved flag (non-course categories) against the current roster/aliases and
 *  stamp resolved_at on any that now resolve. Returns the count resolved. */
export function resolveFlags(db: DatabaseSync): number {
  const rows = db.prepare(
    `SELECT id, category, raw_value FROM wr_name_flags WHERE resolved_at IS NULL`
  ).all() as { id: number; category: string; raw_value: string }[];
  let n = 0;
  for (const r of rows) {
    if (r.category === 'course') continue;
    if (resolveItem(r.category as ItemCategory, r.raw_value).slug !== null) {
      db.prepare(`UPDATE wr_name_flags SET resolved_at = datetime('now') WHERE id=?`).run(r.id);
      n++;
    }
  }
  return n;
}

/** Human-readable list of unresolved flags, grouped by category. */
export function reportFlags(db: DatabaseSync): string {
  const rows = db.prepare(
    `SELECT category, raw_value, slug_guess, occurrences FROM wr_name_flags
     WHERE resolved_at IS NULL ORDER BY category, occurrences DESC, raw_value`
  ).all() as { category: string; raw_value: string; slug_guess: string | null; occurrences: number }[];
  if (rows.length === 0) return 'No unresolved name flags.';
  const out: string[] = [`${rows.length} unresolved name flag(s):`];
  let cat = '';
  for (const r of rows) {
    if (r.category !== cat) { cat = r.category; out.push(`\n[${cat}]`); }
    out.push(`  ${r.raw_value}  (slug ${r.slug_guess ?? '?'}, x${r.occurrences})`);
  }
  return out.join('\n');
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/flags.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/flags.ts pi/src/wr/flags.test.ts
git commit -m "feat(wr): wr_name_flags upsert/resolve/report helpers"
```

---

### Task 5: `history_parse.ts` — per-track page parser

**Files:**
- Create: `pi/src/wr/history_parse.ts`
- Create fixtures: `pi/src/wr/__fixtures__/history/{mario_bros_circuit,mario_circuit,rainbow_road,koopa_troopa_beach,dk_spaceport}.html`
- Test: `pi/src/wr/history_parse.test.ts`

**Interfaces:**
- Consumes: `mkwrsTimeToMs`, `msToTimeStr` from `./time`; `lapTimeToMs`, `parsePerLap` from `./lap`; `parse` from `node-html-parser`.
- Produces:
  - `type ScrapedHistoryRow = { recordMs: number; recordStr: string; dateIso: string | null; datePrecision: 'day' | 'pre_release'; holderName: string | null; holderKey: string | null; nation: string | null; lapSplitsMs: (number | null)[]; coins: number[] | null; mushrooms: number[] | null; characterRaw: string | null; kartRaw: string | null; videoUrl: string | null }`
  - `parseHistory(html: string): ScrapedHistoryRow[]` (page order = oldest→newest; last = current WR)
  - `splitCharacter(raw: string): { character: string | null; costume: string | null }`

- [ ] **Step 1: Create the fixtures**

The five representative pages are already saved under `temp/wrpages/`. Copy them:

```bash
mkdir -p pi/src/wr/__fixtures__/history
cp temp/wrpages/mario_bros_circuit.html pi/src/wr/__fixtures__/history/
cp temp/wrpages/mario_circuit.html      pi/src/wr/__fixtures__/history/
cp temp/wrpages/rainbow_road.html       pi/src/wr/__fixtures__/history/
cp temp/wrpages/koopa_troopa_beach.html pi/src/wr/__fixtures__/history/
cp temp/wrpages/dk_spaceport.html       pi/src/wr/__fixtures__/history/
```

(If `temp/wrpages/` is gone, re-fetch with a browser UA: `https://mkwrs.com/mkworld/display.php?track=Mario+Bros.+Circuit` etc. — see Task 7's `trackUrl`.)

- [ ] **Step 2: Write the failing test**

Create `pi/src/wr/history_parse.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseHistory, splitCharacter } from './history_parse';

const fx = (name: string) =>
  readFileSync(new URL(`./__fixtures__/history/${name}.html`, import.meta.url), 'utf8');

describe('splitCharacter', () => {
  it('splits Char (Costume)', () => {
    expect(splitCharacter('Toadette (Conductor)')).toEqual({ character: 'Toadette', costume: 'Conductor' });
  });
  it('treats a bare name as base costume', () => {
    expect(splitCharacter('Baby Daisy')).toEqual({ character: 'Baby Daisy', costume: null });
  });
});

describe('parseHistory — flat 3-lap (Mario Bros. Circuit)', () => {
  const rows = parseHistory(fx('mario_bros_circuit'));
  it('parses the full progression, oldest first', () => {
    expect(rows.length).toBeGreaterThan(80);
    expect(rows[0].datePrecision).toBe('pre_release');         // first row is Pre-release
    expect(rows[rows.length - 1].recordMs).toBe(107414);       // current WR 1'47"414
  });
  it('extracts a known row (current WR by Toadette/Conductor)', () => {
    const cur = rows[rows.length - 1];
    expect(cur.holderName).toBe('あつき');
    expect(cur.nation).toBe('JP');
    expect(cur.lapSplitsMs).toEqual([37000, 35263, 35151]);
    expect(cur.coins).toEqual([8, 0, 0]);
    expect(cur.mushrooms).toEqual([1, 1, 1]);
    expect(cur.characterRaw).toBe('Toadette (Conductor)');
    expect(cur.kartRaw).toBe('Mach Rocket');
    expect(cur.videoUrl).toMatch(/^https?:/);
  });
  it('tolerates a no-video plain-text time and a missing-data (-) row', () => {
    expect(rows.some((r) => r.videoUrl === null)).toBe(true);
    expect(rows.some((r) => r.coins === null)).toBe(true);
  });
});

describe('parseHistory — stacked variants', () => {
  it('Rainbow Road: 4 laps, M:SS.mmm lap, multi-digit coins, stacked char/kart', () => {
    const rows = parseHistory(fx('rainbow_road'));
    const cur = rows[rows.length - 1];
    expect(cur.lapSplitsMs.length).toBe(4);
    expect(cur.lapSplitsMs[3]).toBe(73164);                    // 1:13.164
    expect(cur.coins).toEqual([8, 12, 0, 0]);
    expect(cur.mushrooms).toEqual([0, 1, 1, 1]);
    expect(cur.characterRaw).toBe('Wiggler');
    expect(cur.kartRaw).toBe('Big Horn');
  });
  it('DK Spaceport: 6 laps', () => {
    const rows = parseHistory(fx('dk_spaceport'));
    const cur = rows[rows.length - 1];
    expect(cur.lapSplitsMs.length).toBe(6);
    expect(cur.coins!.length).toBe(6);
    expect(cur.mushrooms!.length).toBe(6);
  });
  it('Koopa Troopa Beach: 5 laps', () => {
    const rows = parseHistory(fx('koopa_troopa_beach'));
    expect(rows[rows.length - 1].lapSplitsMs.length).toBe(5);
  });
});

describe('parseHistory — patch-row skip (Mario Circuit)', () => {
  it('never emits a patch/info row as a record', () => {
    const rows = parseHistory(fx('mario_circuit'));
    expect(rows.every((r) => !/Patch Released/i.test(r.kartRaw ?? ''))).toBe(true);
    expect(rows.every((r) => Number.isInteger(r.recordMs) && r.recordMs > 0)).toBe(true);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/history_parse.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 4: Implement `history_parse.ts`**

Create `pi/src/wr/history_parse.ts`:

```typescript
import { parse, type HTMLElement } from 'node-html-parser';
import { mkwrsTimeToMs, msToTimeStr } from './time';
import { lapTimeToMs, parsePerLap } from './lap';

/** Pre-release rows ("Time set before 2025-06-05 00:00 UTC") get a sentinel just before release. */
const RELEASE_SENTINEL = '2025-06-04T00:00:00.000Z';

export type ScrapedHistoryRow = {
  recordMs: number;
  recordStr: string;
  dateIso: string | null;
  datePrecision: 'day' | 'pre_release';
  holderName: string | null;
  holderKey: string | null;
  nation: string | null;
  lapSplitsMs: (number | null)[];
  coins: number[] | null;
  mushrooms: number[] | null;
  characterRaw: string | null;
  kartRaw: string | null;
  videoUrl: string | null;
};

type Layout = { laps: number; stacked: boolean };

/** The history table is the `table.wr` with the most rows (the current-WR table has 1 record). */
function selectHistoryTable(root: HTMLElement): HTMLElement | null {
  const tables = root.querySelectorAll('table.wr');
  if (tables.length === 0) return null;
  return tables.reduce((a, b) =>
    b.querySelectorAll('tr').length > a.querySelectorAll('tr').length ? b : a);
}

function detectLayout(headerTr: HTMLElement): Layout {
  const ths = headerTr.querySelectorAll('th').map((th) => th.text.trim());
  const laps = ths.filter((t) => /^Lap \d+$/.test(t)).length;
  const stacked = ths.some((t) => /Coins & Shrooms/i.test(t) || /Combination/i.test(t));
  return { laps, stacked };
}

function isPatchRow(tds: HTMLElement[]): boolean {
  return tds.some((td) => td.getAttribute('colspan') != null);
}

function parseDate(td: HTMLElement): { iso: string | null; precision: 'day' | 'pre_release' } {
  const span = td.querySelector('span');
  if (span && /pre-?release/i.test(span.text)) return { iso: RELEASE_SENTINEL, precision: 'pre_release' };
  const txt = td.text.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(txt)) return { iso: `${txt}T00:00:00.000Z`, precision: 'day' };
  return { iso: null, precision: 'day' };
}

function parseTimeCell(td: HTMLElement): { ms: number; videoUrl: string | null } | null {
  const a = td.querySelector('a');
  const txt = (a?.text ?? td.text).trim();
  let ms: number;
  try { ms = mkwrsTimeToMs(txt); } catch { return null; }
  return { ms, videoUrl: a?.getAttribute('href') ?? null };
}

function parsePlayer(td: HTMLElement): { name: string | null; key: string | null } {
  const a = td.querySelector('a');
  const name = (a?.text ?? td.text).trim() || null;
  const m = /player=([^&"]+)/.exec(a?.getAttribute('href') ?? '');
  return { name, key: m ? m[1] : null };
}

function parseNation(td: HTMLElement): string | null {
  const img = td.querySelector('img');
  if (!img) return null;
  const m = /([A-Za-z]{2,3})\.png$/.exec(img.getAttribute('src') ?? '');
  return m ? m[1] : (img.getAttribute('alt') || null);
}

/** Split `Character (Costume)` on the LAST parenthesis group; a bare name is base costume. */
export function splitCharacter(raw: string): { character: string | null; costume: string | null } {
  const t = (raw ?? '').trim();
  if (!t || t === '-') return { character: null, costume: null };
  const m = /^(.*?)\s*\(([^)]*)\)\s*$/.exec(t);
  return m ? { character: m[1].trim(), costume: m[2].trim() } : { character: t, costume: null };
}

function buildRow(layout: Layout, primary: HTMLElement[], cont: HTMLElement[]): ScrapedHistoryRow | null {
  const n = layout.laps;
  const time = parseTimeCell(primary[1]);
  if (!time) return null;                                   // unparseable time → not a record
  const date = parseDate(primary[0]);
  const player = parsePlayer(primary[2]);
  const lapSplitsMs: (number | null)[] = [];
  for (let k = 0; k < n; k++) lapSplitsMs.push(lapTimeToMs(primary[5 + k]?.text.trim() ?? ''));

  let coins: number[] | null, mushrooms: number[] | null;
  let characterRaw: string | null, kartRaw: string | null;
  if (layout.stacked) {
    coins = parsePerLap(primary[5 + n]?.text.trim() ?? '');
    characterRaw = primary[6 + n]?.text.trim() || null;
    mushrooms = cont[0] ? parsePerLap(cont[0].text.trim()) : null;
    kartRaw = cont[1]?.text.trim() || null;
  } else {
    coins = parsePerLap(primary[5 + n]?.text.trim() ?? '');
    mushrooms = parsePerLap(primary[6 + n]?.text.trim() ?? '');
    characterRaw = primary[7 + n]?.text.trim() || null;
    kartRaw = primary[8 + n]?.text.trim() || null;
  }
  return {
    recordMs: time.ms, recordStr: msToTimeStr(time.ms),
    dateIso: date.iso, datePrecision: date.precision,
    holderName: player.name, holderKey: player.key, nation: parseNation(primary[3]),
    lapSplitsMs, coins, mushrooms, characterRaw, kartRaw, videoUrl: time.videoUrl,
  };
}

/** Parse a display.php page's full WR history. Returns rows in page order (oldest → newest). */
export function parseHistory(html: string): ScrapedHistoryRow[] {
  const root = parse(html);
  const table = selectHistoryTable(root);
  if (!table) return [];
  const rows = table.querySelectorAll('tr');
  if (rows.length < 2) return [];
  const layout = detectLayout(rows[0]);
  const data = rows.slice(1);
  const out: ScrapedHistoryRow[] = [];

  for (let i = 0; i < data.length; i++) {
    const tds = data[i].querySelectorAll('td');
    if (tds.length === 0) continue;                         // stray header row
    if (isPatchRow(tds)) continue;                          // patch/info row

    if (layout.stacked) {
      if (tds[0].getAttribute('rowspan') == null) continue; // orphan continuation → skip
      const next = data[i + 1]?.querySelectorAll('td') ?? [];
      const cont = (next.length === 2 && !isPatchRow(next)) ? next : [];
      const row = buildRow(layout, tds, cont);
      if (row) out.push(row);
      i++;                                                  // consume the continuation row
    } else {
      const row = buildRow(layout, tds, []);
      if (row) out.push(row);
    }
  }
  return out;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/history_parse.test.ts`
Expected: PASS (all describe blocks).

- [ ] **Step 6: Commit**

```bash
git add pi/src/wr/history_parse.ts pi/src/wr/history_parse.test.ts pi/src/wr/__fixtures__/history/
git commit -m "feat(wr): per-track display.php history parser (flat/stacked + patch skip)"
```

---

### Task 6: `history_reconcile.ts` — mirror a course's history into `world_records`

**Files:**
- Create: `pi/src/wr/history_reconcile.ts`
- Test: `pi/src/wr/history_reconcile.test.ts`

**Interfaces:**
- Consumes: `ScrapedHistoryRow`, `splitCharacter` from `./history_parse`; `resolveItem` from `./roster`; `upsertFlag` from `./flags`.
- Produces:
  - `type HistoryReport = { course: string; inserted: number; enriched: number; unchanged: number; removed: number; flagged: number }`
  - `reconcileHistory(db: DatabaseSync, courseId: number, courseName: string, cc: number, rows: ScrapedHistoryRow[]): HistoryReport`

**Semantics:** natural key `(course_id, cc, record_ms, holder_name)`. Match → enrich (fill null/changed canonical fields, clear `removed_at`). No match → insert (`provenance='scraped_history'`, `is_current=0`). After the loop: set `is_current` on the last (newest) row, soft-`removed_at` any unseen row for this course (all provenances), and `upsertFlag` each unresolved name.

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/history_reconcile.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { reconcileHistory } from './history_reconcile';
import type { ScrapedHistoryRow } from './history_parse';

function freshDb() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec(`INSERT INTO courses(id, slug, display_name) VALUES (7,'mario_bros_circuit','Mario Bros. Circuit')`);
  return db;
}

function row(over: Partial<ScrapedHistoryRow>): ScrapedHistoryRow {
  return {
    recordMs: 100000, recordStr: '1:40.000', dateIso: '2025-07-01T00:00:00.000Z',
    datePrecision: 'day', holderName: 'Alice', holderKey: 'Alice', nation: 'US',
    lapSplitsMs: [33000, 33000, 34000], coins: [8, 0, 0], mushrooms: [1, 1, 1],
    characterRaw: 'Toadette (Conductor)', kartRaw: 'Mach Rocket', videoUrl: 'https://y/1', ...over,
  };
}

describe('reconcileHistory', () => {
  it('inserts new rows, resolves names, and marks only the newest as current', () => {
    const db = freshDb();
    const rep = reconcileHistory(db, 7, 'Mario Bros. Circuit', 150, [
      row({ recordMs: 110000, holderName: 'Old' }),
      row({ recordMs: 100000, holderName: 'Alice' }),
    ]);
    expect(rep.inserted).toBe(2);
    const all = db.prepare(`SELECT holder_name, is_current, character_slug, kart_slug, costume_slug,
      lap_splits_ms, coins FROM world_records WHERE course_id=7 ORDER BY record_ms DESC`).all() as any[];
    expect(all.find((r) => r.holder_name === 'Alice').is_current).toBe(1);
    expect(all.find((r) => r.holder_name === 'Old').is_current).toBe(0);
    const a = all.find((r) => r.holder_name === 'Alice');
    expect([a.character_slug, a.costume_slug, a.kart_slug]).toEqual(['toadette', 'conductor', 'mach_rocket']);
    expect(JSON.parse(a.coins)).toEqual([8, 0, 0]);
  });

  it('enriches an existing legacy row in place (matched by natural key, not duplicated)', () => {
    const db = freshDb();
    db.exec(`INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, provenance)
             VALUES (7,150,'Alice',100000,'1:40.000','legacy_import')`);
    const rep = reconcileHistory(db, 7, 'Mario Bros. Circuit', 150, [row({})]);
    expect(rep.inserted).toBe(0);
    expect(rep.enriched).toBe(1);
    const cnt = db.prepare(`SELECT COUNT(*) c FROM world_records WHERE course_id=7`).get() as any;
    expect(cnt.c).toBe(1);                                  // enriched, not re-inserted
    const r = db.prepare(`SELECT nation, kart_slug FROM world_records WHERE course_id=7`).get() as any;
    expect([r.nation, r.kart_slug]).toEqual(['US', 'mach_rocket']);
  });

  it('soft-removes a row no longer present and flags an unresolved kart', () => {
    const db = freshDb();
    db.exec(`INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, provenance)
             VALUES (7,150,'Ghost',999999,'9:99.999','scraped_history')`);
    const rep = reconcileHistory(db, 7, 'Mario Bros. Circuit', 150, [row({ kartRaw: 'Totally Fake Kart' })]);
    expect(rep.removed).toBe(1);
    expect(rep.flagged).toBe(1);
    const ghost = db.prepare(`SELECT removed_at FROM world_records WHERE holder_name='Ghost'`).get() as any;
    expect(ghost.removed_at).not.toBeNull();
    const flag = db.prepare(`SELECT raw_value FROM wr_name_flags WHERE category='kart'`).get() as any;
    expect(flag.raw_value).toBe('Totally Fake Kart');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/history_reconcile.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `history_reconcile.ts`**

Create `pi/src/wr/history_reconcile.ts`:

```typescript
import type { DatabaseSync } from 'node:sqlite';
import { splitCharacter, type ScrapedHistoryRow } from './history_parse';
import { resolveItem } from './roster';
import { upsertFlag } from './flags';

export type HistoryReport = {
  course: string; inserted: number; enriched: number; unchanged: number; removed: number; flagged: number;
};

type ExistingRow = {
  id: number; nation: string | null; character_slug: string | null; kart_slug: string | null;
  costume_slug: string | null; lap_splits_ms: string | null; coins: string | null;
  mushrooms: string | null; video_url: string | null; character: string | null; vehicle: string | null;
  date_precision: string | null; source_raw: string | null; removed_at: string | null;
};

const J = (v: unknown): string | null => (v == null ? null : JSON.stringify(v));

export function reconcileHistory(
  db: DatabaseSync, courseId: number, courseName: string, cc: number, rows: ScrapedHistoryRow[],
): HistoryReport {
  const report: HistoryReport = { course: courseName, inserted: 0, enriched: 0, unchanged: 0, removed: 0, flagged: 0 };
  if (rows.length === 0) return report;
  const now = new Date().toISOString();
  const seen: number[] = [];

  const findExisting = db.prepare(
    `SELECT id, nation, character_slug, kart_slug, costume_slug, lap_splits_ms, coins, mushrooms,
            video_url, character, vehicle, date_precision, source_raw, removed_at
     FROM world_records WHERE course_id=? AND cc=? AND record_ms=? AND holder_name IS ?`
  );

  db.exec('BEGIN');
  try {
    for (const r of rows) {
      const { character, costume } = splitCharacter(r.characterRaw ?? '');
      const ch = character ? resolveItem('character', character) : null;
      const co = costume ? resolveItem('costume', costume) : null;
      const ka = r.kartRaw ? resolveItem('kart', r.kartRaw) : null;
      const sourceRaw = JSON.stringify(r);

      const existing = findExisting.get(courseId, cc, r.recordMs, r.holderName) as ExistingRow | undefined;
      let wrId: number;
      if (existing) {
        wrId = existing.id;
        const sets: string[] = [], vals: unknown[] = [];
        const set = (col: string, val: unknown, cur: unknown) => {
          if (val != null && val !== cur) { sets.push(`${col}=?`); vals.push(val); }
        };
        set('nation', r.nation, existing.nation);
        set('character_slug', ch?.slug ?? null, existing.character_slug);
        set('kart_slug', ka?.slug ?? null, existing.kart_slug);
        set('costume_slug', co?.slug ?? null, existing.costume_slug);
        set('lap_splits_ms', J(r.lapSplitsMs), existing.lap_splits_ms);
        set('coins', J(r.coins), existing.coins);
        set('mushrooms', J(r.mushrooms), existing.mushrooms);
        set('video_url', r.videoUrl, existing.video_url);
        set('character', r.characterRaw, existing.character);
        set('vehicle', r.kartRaw, existing.vehicle);
        set('date_precision', r.datePrecision, existing.date_precision);
        set('source_raw', sourceRaw, existing.source_raw);
        if (existing.removed_at != null) { sets.push('removed_at=?'); vals.push(null); }  // reappeared
        if (sets.length) {
          db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, wrId);
          report.enriched++;
        } else { report.unchanged++; }
      } else {
        const res = db.prepare(
          `INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, achieved_at,
             video_url, character, vehicle, nation, character_slug, kart_slug, costume_slug,
             lap_splits_ms, coins, mushrooms, date_precision, source_raw, provenance, is_current)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'scraped_history', 0)`
        ).run(courseId, cc, r.holderName, r.recordMs, r.recordStr, r.dateIso, r.videoUrl,
              r.characterRaw, r.kartRaw, r.nation, ch?.slug ?? null, ka?.slug ?? null, co?.slug ?? null,
              J(r.lapSplitsMs), J(r.coins), J(r.mushrooms), r.datePrecision, sourceRaw);
        wrId = Number(res.lastInsertRowid);
        report.inserted++;
      }
      seen.push(wrId);

      // Flag unresolved names (present-but-unresolved only; a bare base costume is not flagged).
      if (character && ch && !ch.slug) { upsertFlag(db, { category: 'character', rawValue: character, slugGuess: ch.slugGuess, exampleCourseId: courseId, exampleWrId: wrId }); report.flagged++; }
      if (costume && co && !co.slug) { upsertFlag(db, { category: 'costume', rawValue: costume, slugGuess: co.slugGuess, exampleCourseId: courseId, exampleWrId: wrId }); report.flagged++; }
      if (r.kartRaw && ka && !ka.slug) { upsertFlag(db, { category: 'kart', rawValue: r.kartRaw, slugGuess: ka.slugGuess, exampleCourseId: courseId, exampleWrId: wrId }); report.flagged++; }
    }

    // is_current: the newest row (last in page order) is the current WR.
    const cur = rows[rows.length - 1];
    const curRow = findExisting.get(courseId, cc, cur.recordMs, cur.holderName) as { id: number } | undefined;
    db.prepare('UPDATE world_records SET is_current=0 WHERE course_id=? AND cc=?').run(courseId, cc);
    if (curRow) db.prepare('UPDATE world_records SET is_current=1 WHERE id=?').run(curRow.id);

    // Soft-remove any row for this course not present in this scrape (mkwrs is authoritative).
    const placeholders = seen.map(() => '?').join(',');
    report.removed = Number((db.prepare(
      `SELECT COUNT(*) c FROM world_records
       WHERE course_id=? AND cc=? AND removed_at IS NULL AND id NOT IN (${placeholders})`
    ).get(courseId, cc, ...seen) as { c: number }).c);
    db.prepare(
      `UPDATE world_records SET removed_at=?
       WHERE course_id=? AND cc=? AND removed_at IS NULL AND id NOT IN (${placeholders})`
    ).run(now, courseId, cc, ...seen);

    db.exec('COMMIT');
  } catch (e) { db.exec('ROLLBACK'); throw e; }
  return report;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/history_reconcile.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/history_reconcile.ts pi/src/wr/history_reconcile.test.ts
git commit -m "feat(wr): reconcile per-track history into world_records (enrich/insert/remove/flag)"
```

---

### Task 7: `history_scrape.ts` — track list, polite fetch, orchestration

**Files:**
- Create: `pi/src/wr/history_scrape.ts`
- Test: `pi/src/wr/history_scrape.test.ts`

**Interfaces:**
- Consumes: `parseHistory` from `./history_parse`; `reconcileHistory`, `HistoryReport` from `./history_reconcile`; `resolveCourseId` from `./courses`.
- Produces:
  - `MKWRS_TRACKS: string[]` (the 30 mkwrs display names)
  - `trackUrl(name: string): string`
  - `scrapeTrackHistory(db, track: string, opts?: { cc?: number; fetchHtml?: (url: string) => Promise<string> }): Promise<HistoryReport>`
  - `scrapeAllHistory(db, opts?: { cc?: number; fetchHtml?; minDelayMs?; maxDelayMs?; random?: () => number; sleep?: (ms: number) => Promise<void>; log?: (m: string) => void }): Promise<HistoryReport[]>`

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/history_scrape.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { openDb, applySchema } from '../db/connect';
import { trackUrl, scrapeTrackHistory, scrapeAllHistory, MKWRS_TRACKS } from './history_scrape';

const fx = (name: string) =>
  readFileSync(new URL(`./__fixtures__/history/${name}.html`, import.meta.url), 'utf8');

function dbWith(slug: string, name: string) {
  const db = openDb(':memory:');
  applySchema(db);
  db.prepare(`INSERT INTO courses(id, slug, display_name) VALUES (7,?,?)`).run(slug, name);
  return db;
}

describe('trackUrl', () => {
  it('encodes spaces as + and ? as %3F', () => {
    expect(trackUrl('Great ? Block Ruins')).toBe('https://mkwrs.com/mkworld/display.php?track=Great+%3F+Block+Ruins');
    expect(trackUrl("Toad's Factory")).toContain('Toad%27s+Factory');
  });
  it('lists all 30 tracks', () => { expect(MKWRS_TRACKS.length).toBe(30); });
});

describe('scrapeTrackHistory', () => {
  it('parses + reconciles a fixture via injected fetch', async () => {
    const db = dbWith('rainbow_road', 'Rainbow Road');
    const rep = await scrapeTrackHistory(db, 'Rainbow Road', { fetchHtml: async () => fx('rainbow_road') });
    expect(rep.inserted).toBeGreaterThan(100);
    const cur = db.prepare(`SELECT kart_slug FROM world_records WHERE course_id=7 AND is_current=1`).get() as any;
    expect(cur.kart_slug).toBe('big_horn');
  });
});

describe('scrapeAllHistory', () => {
  it('runs sequentially with no real delay (sleep injected)', async () => {
    const db = dbWith('mario_bros_circuit', 'Mario Bros. Circuit');
    const reps = await scrapeAllHistory(db, {
      fetchHtml: async () => fx('mario_bros_circuit'),
      sleep: async () => {}, random: () => 0,
    });
    expect(reps.length).toBe(30);
    // Only Mario Bros. Circuit maps to a course here; others resolve to null and are skipped cleanly.
    const mbc = reps.find((r) => r.course === 'Mario Bros. Circuit');
    expect(mbc!.inserted).toBeGreaterThan(80);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/history_scrape.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `history_scrape.ts`**

Create `pi/src/wr/history_scrape.ts`:

```typescript
import type { DatabaseSync } from 'node:sqlite';
import { parseHistory } from './history_parse';
import { reconcileHistory, type HistoryReport } from './history_reconcile';
import { resolveCourseId } from './courses';

/** The 30 base mkwrs track names (display form). Order matches the mkworld nav. */
export const MKWRS_TRACKS: string[] = [
  'Mario Bros. Circuit', 'Crown City', 'Whistlestop Summit', 'DK Spaceport', 'Desert Hills',
  'Shy Guy Bazaar', 'Wario Stadium', 'Airship Fortress', 'DK Pass', 'Starview Peak',
  'Sky-High Sundae', 'Wario Shipyard', 'Koopa Troopa Beach', 'Faraway Oasis', 'Peach Stadium',
  'Peach Beach', 'Salty Salty Speedway', 'Dino Dino Jungle', 'Great ? Block Ruins',
  'Cheep Cheep Falls', 'Dandelion Depths', 'Boo Cinema', 'Dry Bones Burnout', 'Moo Moo Meadows',
  'Choco Mountain', "Toad's Factory", "Bowser's Castle", 'Acorn Heights', 'Mario Circuit',
  'Rainbow Road',
];

export const DEFAULT_BASE = 'https://mkwrs.com/mkworld/display.php?track=';

/** Build the display.php URL: spaces → +, ? → %3F, ' → %27 (matches mkwrs's own links). */
export function trackUrl(name: string): string {
  return DEFAULT_BASE + encodeURIComponent(name).replace(/%20/g, '+').replace(/'/g, '%27');
}

async function politeFetch(url: string): Promise<string> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), 30_000);       // manual timer (Windows-safe teardown)
  try {
    const res = await fetch(url, {
      signal: ac.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        Referer: 'https://mkwrs.com/mkworld/',
      },
    });
    if (!res.ok) throw new Error(`mkwrs history fetch failed: HTTP ${res.status} for ${url}`);
    return await res.text();
  } finally { clearTimeout(timer); }
}

export type ScrapeTrackOpts = { cc?: number; fetchHtml?: (url: string) => Promise<string> };

/** Fetch + parse + reconcile one track. Unmapped (glitch/unknown) → empty report. */
export async function scrapeTrackHistory(db: DatabaseSync, track: string, opts: ScrapeTrackOpts = {}): Promise<HistoryReport> {
  const cc = opts.cc ?? 150;
  const courseId = resolveCourseId(db, track);
  if (courseId === null) return { course: track, inserted: 0, enriched: 0, unchanged: 0, removed: 0, flagged: 0 };
  const html = await (opts.fetchHtml ?? politeFetch)(trackUrl(track));
  return reconcileHistory(db, courseId, track, cc, parseHistory(html));
}

export type ScrapeAllOpts = ScrapeTrackOpts & {
  minDelayMs?: number; maxDelayMs?: number;
  random?: () => number; sleep?: (ms: number) => Promise<void>;
  log?: (m: string) => void;
};

const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Scrape all 30 tracks sequentially, with a randomized polite delay between requests. */
export async function scrapeAllHistory(db: DatabaseSync, opts: ScrapeAllOpts = {}): Promise<HistoryReport[]> {
  const { minDelayMs = 20_000, maxDelayMs = 60_000, random = Math.random, sleep = realSleep, log = () => {} } = opts;
  const out: HistoryReport[] = [];
  for (let i = 0; i < MKWRS_TRACKS.length; i++) {
    const track = MKWRS_TRACKS[i];
    try {
      const rep = await scrapeTrackHistory(db, track, opts);
      out.push(rep);
      log(`[wr-history] ${track}: ${JSON.stringify(rep)}`);
    } catch (e) {
      out.push({ course: track, inserted: 0, enriched: 0, unchanged: 0, removed: 0, flagged: 0 });
      log(`[wr-history] ${track}: FAILED ${(e as Error).message}`);
    }
    if (i < MKWRS_TRACKS.length - 1) await sleep(minDelayMs + random() * (maxDelayMs - minDelayMs));
  }
  return out;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/history_scrape.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/history_scrape.ts pi/src/wr/history_scrape.test.ts
git commit -m "feat(wr): per-track + bulk history scrape orchestration (polite fetch)"
```

---

### Task 8: `history_scheduler.ts` — slow round-robin drip verifier

**Files:**
- Create: `pi/src/wr/history_scheduler.ts`
- Test: `pi/src/wr/history_scheduler.test.ts`

**Interfaces:**
- Consumes: `MKWRS_TRACKS`, `scrapeTrackHistory` from `./history_scrape`; `wr_meta` (Task 1).
- Produces: `startWrHistoryScraper(db, opts: { minIntervalSec: number; maxIntervalSec: number; tracks?: string[]; scrapeTrack?: (db, track) => Promise<unknown>; random?: () => number }): () => void` — scrapes ONE track per tick, round-robin via the persisted `wr_meta.history_cursor`; jittered `setTimeout`; `maxIntervalSec <= 0` disables; returns a stop function.

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/history_scheduler.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { startWrHistoryScraper } from './history_scheduler';

function freshDb() { const db = openDb(':memory:'); applySchema(db); return db; }

describe('startWrHistoryScraper', () => {
  it('scrapes one track per tick round-robin and persists the cursor', async () => {
    vi.useFakeTimers();
    try {
      const db = freshDb();
      const seen: string[] = [];
      const scrapeTrack = vi.fn(async (_db: any, track: string) => { seen.push(track); });
      const stop = startWrHistoryScraper(db, {
        minIntervalSec: 100, maxIntervalSec: 100, random: () => 0,
        tracks: ['A', 'B', 'C'], scrapeTrack,
      });
      expect(seen).toEqual(['A']);                          // immediate first tick
      await vi.advanceTimersByTimeAsync(100_000);
      await vi.advanceTimersByTimeAsync(100_000);
      expect(seen).toEqual(['A', 'B', 'C']);
      const cur = db.prepare(`SELECT value FROM wr_meta WHERE key='history_cursor'`).get() as any;
      expect(cur.value).toBe('0');                          // wrapped back to start (3 % 3)
      stop();
    } finally { vi.useRealTimers(); }
  });

  it('is disabled when maxIntervalSec <= 0', () => {
    const scrapeTrack = vi.fn();
    const stop = startWrHistoryScraper(freshDb(), { minIntervalSec: 100, maxIntervalSec: 0, scrapeTrack });
    expect(scrapeTrack).not.toHaveBeenCalled();
    stop();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/history_scheduler.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `history_scheduler.ts`**

Create `pi/src/wr/history_scheduler.ts`:

```typescript
import type { DatabaseSync } from 'node:sqlite';
import { MKWRS_TRACKS, scrapeTrackHistory } from './history_scrape';

function getCursor(db: DatabaseSync): number {
  const row = db.prepare(`SELECT value FROM wr_meta WHERE key='history_cursor'`).get() as { value: string } | undefined;
  const n = row ? Number(row.value) : 0;
  return Number.isFinite(n) ? n : 0;
}

function setCursor(db: DatabaseSync, n: number): void {
  db.prepare(
    `INSERT INTO wr_meta(key, value) VALUES ('history_cursor', ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value`
  ).run(String(n));
}

export type HistorySchedulerOpts = {
  minIntervalSec: number;
  maxIntervalSec: number;
  tracks?: string[];
  scrapeTrack?: (db: DatabaseSync, track: string) => Promise<unknown>;
  random?: () => number;
};

/** Scrape ONE track per tick, round-robin via the persisted wr_meta.history_cursor, at a random
 *  interval in [min,max] re-rolled each cycle (looks like a person, near-zero request volume).
 *  maxIntervalSec <= 0 disables. Returns a stop function. */
export function startWrHistoryScraper(db: DatabaseSync, opts: HistorySchedulerOpts): () => void {
  const tracks = opts.tracks ?? MKWRS_TRACKS;
  const scrapeTrack = opts.scrapeTrack ?? ((d, t) => scrapeTrackHistory(d, t));
  const random = opts.random ?? Math.random;
  if (!opts.maxIntervalSec || opts.maxIntervalSec <= 0) return () => {};

  const lo = Math.max(0, Math.min(opts.minIntervalSec, opts.maxIntervalSec));
  const hi = Math.max(opts.minIntervalSec, opts.maxIntervalSec);
  const nextDelayMs = () => (lo + random() * (hi - lo)) * 1000;

  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = () => { if (!stopped) timer = setTimeout(() => void tick(), nextDelayMs()); };

  const tick = async () => {
    const i = getCursor(db) % tracks.length;
    try {
      await scrapeTrack(db, tracks[i]);
    } catch (e) {
      console.error('[wr-history] drip failed:', e);
    } finally {
      setCursor(db, (i + 1) % tracks.length);
      schedule();
    }
  };

  void tick();                                              // immediate first tick
  return () => { stopped = true; if (timer) clearTimeout(timer); };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/history_scheduler.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/history_scheduler.ts pi/src/wr/history_scheduler.test.ts
git commit -m "feat(wr): slow round-robin history drip verifier"
```

---

### Task 9: CLIs + server wiring + package scripts

**Files:**
- Create: `pi/src/scripts/scrapeWrHistory.ts`
- Create: `pi/src/scripts/wrFlags.ts`
- Modify: `pi/src/server.ts` (after the `startWrScraper(...)` block, ~line 40)
- Modify: `pi/package.json` (scripts)
- Test: `pi/src/wr/history_e2e.test.ts`

**Interfaces:**
- Consumes: `scrapeAllHistory`, `scrapeTrackHistory` from `../wr/history_scrape`; `startWrHistoryScraper` from `../wr/history_scheduler`; `reportFlags`, `resolveFlags` from `../wr/flags`; `openDb`, `applySchema` from `../db/connect`.

- [ ] **Step 1: Write the failing e2e test**

Create `pi/src/wr/history_e2e.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { openDb, applySchema } from '../db/connect';
import { scrapeTrackHistory } from './history_scrape';
import { resolveFlags, reportFlags } from './flags';

const fx = (name: string) =>
  readFileSync(new URL(`./__fixtures__/history/${name}.html`, import.meta.url), 'utf8');

const FIXTURES: [string, string][] = [
  ['mario_bros_circuit', 'Mario Bros. Circuit'],
  ['mario_circuit', 'Mario Circuit'],
  ['rainbow_road', 'Rainbow Road'],
  ['koopa_troopa_beach', 'Koopa Troopa Beach'],
  ['dk_spaceport', 'DK Spaceport'],
];

describe('history e2e over all 5 fixtures', () => {
  it('parses + reconciles every variant with zero unresolved flags and one current per course', async () => {
    const db = openDb(':memory:');
    applySchema(db);
    for (let i = 0; i < FIXTURES.length; i++) {
      const [fixture, name] = FIXTURES[i];
      const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      db.prepare(`INSERT INTO courses(id, slug, display_name) VALUES (?,?,?)`).run(i + 1, slug, name);
      const rep = await scrapeTrackHistory(db, name, { fetchHtml: async () => fx(fixture) });
      expect(rep.inserted).toBeGreaterThan(80);
      const currents = db.prepare(
        `SELECT COUNT(*) c FROM world_records w JOIN courses c2 ON c2.id=w.course_id
         WHERE c2.display_name=? AND w.is_current=1`).get(name) as any;
      expect(currents.c).toBe(1);
    }
    resolveFlags(db);
    expect(reportFlags(db)).toBe('No unresolved name flags.');   // 3 kart aliases cover everything
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/history_e2e.test.ts`
Expected: FAIL until the fixtures + modules exist (they do from Tasks 5–7); this test should actually PASS already if Tasks 5–7 are complete. If it fails, fix the offending module before continuing. (It is included here as the cross-cutting gate.)

- [ ] **Step 3: Implement the `scrape-wr-history` CLI**

Create `pi/src/scripts/scrapeWrHistory.ts`:

```typescript
import { openDb, applySchema } from '../db/connect';
import { scrapeAllHistory, scrapeTrackHistory } from '../wr/history_scrape';
import { resolveFlags, reportFlags } from '../wr/flags';

// Usage: scrape-wr-history --all | --track="Rainbow Road"
const args = process.argv.slice(2);
const trackArg = args.find((a) => a.startsWith('--track='))?.slice('--track='.length);
const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);

async function main() {
  if (trackArg) {
    const rep = await scrapeTrackHistory(db, trackArg);
    console.log('[wr-history]', JSON.stringify(rep));
  } else {
    const reps = await scrapeAllHistory(db, { log: (m) => console.log(m) });
    const totals = reps.reduce((a, r) => ({
      inserted: a.inserted + r.inserted, enriched: a.enriched + r.enriched,
      removed: a.removed + r.removed, flagged: a.flagged + r.flagged,
    }), { inserted: 0, enriched: 0, removed: 0, flagged: 0 });
    console.log('[wr-history] totals:', JSON.stringify(totals));
  }
  resolveFlags(db);
  console.log(reportFlags(db));
}

main().catch((e) => { console.error('[wr-history] failed:', e); process.exitCode = 1; });
```

- [ ] **Step 4: Implement the `wr-flags` CLI**

Create `pi/src/scripts/wrFlags.ts`:

```typescript
import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
resolveFlags(db);                                           // auto-clear any now-resolvable flags first
console.log(reportFlags(db));
```

- [ ] **Step 5: Wire the drip into `server.ts`**

In `pi/src/server.ts`, add the import near the other `wr` import:

```typescript
import { startWrHistoryScraper } from './wr/history_scheduler';
```

Immediately after the existing `startWrScraper(db, hub, { … });` block, add:

```typescript
startWrHistoryScraper(db, {
  minIntervalSec: Number(process.env.MKWRS_HISTORY_MIN_INTERVAL_SEC ?? 7200),    // 2 h
  maxIntervalSec: Number(process.env.MKWRS_HISTORY_ENABLED === '0'
    ? 0
    : process.env.MKWRS_HISTORY_MAX_INTERVAL_SEC ?? 21600),                      // 6 h; 0 disables
});
```

- [ ] **Step 6: Add package scripts**

In `pi/package.json` `scripts`, add:

```json
    "scrape-wr-history": "node --no-warnings --import tsx src/scripts/scrapeWrHistory.ts",
    "wr-flags": "node --no-warnings --import tsx src/scripts/wrFlags.ts",
```

- [ ] **Step 7: Run the full pi suite + a typecheck**

Run: `cd pi && npm test`
Expected: PASS (all suites, including the new wr tests).

Run: `cd pi && npx tsc --noEmit`
Expected: no new errors from `src/wr/` or `src/scripts/` (a pre-existing `src/api/ws.test.ts` error may remain — ignore only that one).

- [ ] **Step 8: Commit**

```bash
git add pi/src/scripts/scrapeWrHistory.ts pi/src/scripts/wrFlags.ts pi/src/server.ts pi/package.json pi/src/wr/history_e2e.test.ts
git commit -m "feat(wr): scrape-wr-history + wr-flags CLIs and drip server wiring"
```

- [ ] **Step 9: Manual verification (real data, do once)**

This is a manual gate, not an automated test. Against a **copy** of the live DB:

```bash
cp pi/mkw.db /tmp/mkw_wrhist_test.db
cd pi && MKW_DB=/tmp/mkw_wrhist_test.db npm run scrape-wr-history -- --all
```

Confirm in the output: totals show a large `inserted`, a **small `removed`** (existing rows match+enrich, not mass remove+reinsert), and `reportFlags` prints `No unresolved name flags.` Then:

```bash
cd pi && MKW_DB=/tmp/mkw_wrhist_test.db node --import tsx -e "import('./src/db/connect.ts').then(async m=>{const db=m.openDb(process.env.MKW_DB);const r=db.prepare('SELECT COUNT(*) c FROM world_records WHERE is_current=1').get();console.log('currents',r.c)})"
```
Expected: `currents 30`.

---

## Self-Review

**1. Spec coverage:**
- §2 source structure (4 variants, patch rows, cell formats) → Task 5 (parser + fixtures).
- §4 schema (10 columns + `wr_name_flags` + `wr_meta`) → Task 1.
- §5 parser → Task 5. §6 reconciliation (roster/aliases/flags) → Tasks 3, 4, 6.
- §7 mirror/DQ semantics (enrich/insert/is_current/soft-remove across provenances) → Task 6.
- §8 orchestration (bulk CLI + drip) → Tasks 7, 8, 9.
- §9 testing (fixtures for every variant, reconcile, resolution, e2e) → Tasks 5–9.
- §10 out of scope (territory consumption) → not implemented (correct).
- §11 risks (header-driven parse, politeness, soft remove) → Tasks 5, 7, 6.

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"; every code step shows complete code; every run step has an exact command + expected result.

**3. Type consistency:** `ScrapedHistoryRow` (Task 5) is consumed unchanged by Tasks 6–7. `HistoryReport` shape `{course,inserted,enriched,unchanged,removed,flagged}` is identical in Tasks 6, 7, 9. `resolveItem` return `{slug,slugGuess}` (Task 3) is used consistently in Tasks 4, 6. `ItemCategory` is shared. `upsertFlag` input shape matches between Tasks 4 and 6. `MKWRS_TRACKS`/`scrapeTrackHistory` signatures match between Tasks 7 and 8.
