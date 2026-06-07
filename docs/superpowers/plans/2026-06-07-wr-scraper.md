# WR Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server-side job on the pi that periodically scrapes the mkwrs.com Mario Kart World WR table and makes the canonical `world_records` store mirror the current WR per course (append-only history, metadata backfill, live `wr_update` events).

**Architecture:** New `pi/src/wr/` package separating pure mechanism (time parse, course mapping, HTML parse, reconcile) from policy (an in-process scheduler started by `server.ts`, plus a `scrape-wr` CLI). The current WR per `(course, cc)` is marked by a new `is_current` column (one current per course, enforced by a partial unique index); each scrape mirrors the page, moving `is_current` to a slower record on a revert/DQ. All logic is unit-tested offline by injecting the HTML fetcher.

**Tech Stack:** TypeScript, Node 22 (`node:sqlite`, global `fetch`), Hono `EventHub`, `node-html-parser`, vitest. All work is under `pi/`; the shared schema is `server/schema.sql`.

**Spec:** `docs/superpowers/specs/2026-06-07-wr-scraper-design.md`

**Working directory for all commands:** `pi/`. Commands below are shown with a `cd pi && ` prefix for clarity. If you run them through the Bash tool, that works as-is. On **Windows PowerShell 5.1** `&&` is a parse error, so instead `Set-Location pi` once and run each command without the `cd pi && ` prefix (and use `$env:VAR='x'; cmd` for env vars). Tests: `npm test`, or a single file via `npx vitest run src/wr/<file>.test.ts`.

---

### Task 1: `is_current` schema + migration + partial unique index

**Files:**
- Modify: `server/schema.sql` (add the column)
- Modify: `pi/src/db/connect.ts` (ALTER + one-time seed + index)
- Test: `pi/src/db/connect.test.ts` (new)

- [ ] **Step 1: Write the failing migration test**

Create `pi/src/db/connect.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from './connect';

/** Build a pre-`is_current` world_records table (a fresh applySchema DB already has the
 *  column, so we hand-build the old shape) with several WRs per course. */
function legacyShapeDb(): DatabaseSync {
  const db = new DatabaseSync(':memory:');
  db.exec(`
    CREATE TABLE courses(id INTEGER PRIMARY KEY, slug TEXT, display_name TEXT);
    INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road'),(2,'mc','Mario Circuit');
    CREATE TABLE world_records(
      id INTEGER PRIMARY KEY, course_id INTEGER, cc INTEGER, holder_name TEXT,
      record_ms INTEGER, record_str TEXT, achieved_at TEXT);
    INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at) VALUES
      (1,150,'A',100000,'1:40.000','2026-01-01'),
      (1,150,'B', 99000,'1:39.000','2026-02-01'),
      (2,150,'C',120000,'2:00.000','2026-01-15');
  `);
  return db;
}

describe('applySchema is_current migration', () => {
  it('flags exactly the latest-achieved WR per course as current', () => {
    const db = legacyShapeDb();
    applySchema(db);
    const cur = db.prepare(
      'SELECT course_id, record_ms FROM world_records WHERE is_current=1 ORDER BY course_id'
    ).all();
    expect(cur).toEqual([
      { course_id: 1, record_ms: 99000 },   // 2026-02-01 beats 2026-01-01
      { course_id: 2, record_ms: 120000 },
    ]);
  });

  it('the partial unique index rejects a second current row for a course', () => {
    const db = legacyShapeDb();
    applySchema(db);
    expect(() =>
      db.prepare('UPDATE world_records SET is_current=1 WHERE course_id=1 AND record_ms=100000').run()
    ).toThrow();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/db/connect.test.ts`
Expected: FAIL (no `is_current` column / no index yet — errors like "no such column: is_current").

- [ ] **Step 3: Add the column to the shared schema**

In `server/schema.sql`, inside `CREATE TABLE IF NOT EXISTS world_records (...)`, add the column after `vehicle TEXT,` and before `provenance`:

```sql
    vehicle      TEXT,
    is_current   INTEGER NOT NULL DEFAULT 0,
    provenance   TEXT NOT NULL DEFAULT 'legacy_import',
```

(Do NOT add the partial index to `schema.sql`: `applySchema` execs the whole file first, which on a pre-existing DB runs before the ALTER adds the column, so a `WHERE is_current=1` index here would reference a missing column and throw.)

- [ ] **Step 4: Add the migration + index to `connect.ts`**

In `pi/src/db/connect.ts`, replace the body of `applySchema` so it reads:

```ts
export function applySchema(db: DatabaseSync): void {
  db.exec(readFileSync(SCHEMA_PATH, 'utf8'));
  // Additive migrations for existing DBs (CREATE TABLE IF NOT EXISTS won't add columns).
  try { db.exec('ALTER TABLE players ADD COLUMN color TEXT'); } catch { /* already present */ }
  try {
    db.exec('ALTER TABLE world_records ADD COLUMN is_current INTEGER NOT NULL DEFAULT 0');
    // One-time seed (only runs the first time the column is added): flag the
    // latest-achieved WR per (course,cc) as current.
    db.exec(`UPDATE world_records SET is_current = 1 WHERE id = (
      SELECT w2.id FROM world_records w2
      WHERE w2.course_id = world_records.course_id AND w2.cc = world_records.cc
      ORDER BY w2.achieved_at DESC, w2.id DESC LIMIT 1)`);
  } catch { /* already present + seeded */ }
  // Idempotent: at most one current WR per (course,cc). Created here (not in schema.sql)
  // so the column is guaranteed present for both fresh and migrated DBs.
  db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_wr_current ON world_records(course_id, cc) WHERE is_current=1');
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/db/connect.test.ts`
Expected: PASS (both tests).

- [ ] **Step 6: Run the full suite to confirm nothing else broke**

Run: `cd pi && npm test`
Expected: PASS, except possibly the existing `currentWr` test in `reads.test.ts` (its seed does not set `is_current`; that is fixed in Task 2). If only that one fails, continue.

- [ ] **Step 7: Commit**

```bash
git add server/schema.sql pi/src/db/connect.ts pi/src/db/connect.test.ts
git commit -m "feat(wr): add is_current marker + migration + partial unique index" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `currentWr` reads the `is_current` row

**Files:**
- Modify: `pi/src/db/reads.ts:28` (`currentWr`)
- Test: `pi/src/db/reads.test.ts` (update seed + add a case)

- [ ] **Step 1: Update the test seed and add the slower-is-current case**

In `pi/src/db/reads.test.ts`, change the WR insert in `seeded()` to flag it current:

```ts
db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'SuperFX',100000,'1:40.000',1)");
```

Then add this test inside the `describe('reads', ...)` block:

```ts
it('currentWr returns the is_current row even when a faster row exists', () => {
  const db = seeded();
  // A faster, non-current row (a removed/DQ'd record) plus moving current to a slower one.
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'Ghost',95000,'1:35.000',0)");
  db.exec("UPDATE world_records SET is_current=0 WHERE record_ms=100000");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'Reverted',101000,'1:41.000',1)");
  expect(currentWr(db, 1, 150)?.record_ms).toBe(101000);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/db/reads.test.ts`
Expected: FAIL — the new case returns `95000` (old ordering picks the latest `achieved_at`/`id`, not the current flag).

- [ ] **Step 3: Update `currentWr`**

In `pi/src/db/reads.ts`, replace the `currentWr` function body:

```ts
export function currentWr(db: DatabaseSync, courseId: number, cc: number) {
  return (db.prepare(
    `SELECT holder_name, record_ms, record_str, achieved_at, video_url, character, vehicle
     FROM world_records WHERE course_id=? AND cc=? AND is_current=1 LIMIT 1`
  ).get(courseId, cc) as any) ?? null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/db/reads.test.ts`
Expected: PASS (both the original `record_ms === 100000` case and the new one).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts
git commit -m "feat(wr): currentWr reads the is_current row" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `time.ts` (mkwrs time <-> ms)

**Files:**
- Create: `pi/src/wr/time.ts`
- Test: `pi/src/wr/time.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/time.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { mkwrsTimeToMs, msToTimeStr } from './time';

describe('mkwrsTimeToMs', () => {
  it('parses M\'SS"mmm with 3-digit ms', () => {
    expect(mkwrsTimeToMs('1\'47"414')).toBe(107414);
  });
  it('normalizes 1- and 2-digit ms (hundredths/tenths)', () => {
    expect(mkwrsTimeToMs('1\'47"41')).toBe(107410);
    expect(mkwrsTimeToMs('1\'47"4')).toBe(107400);
  });
  it('handles surrounding whitespace', () => {
    expect(mkwrsTimeToMs('  2\'00"000 ')).toBe(120000);
  });
  it('throws on garbage', () => {
    expect(() => mkwrsTimeToMs('1:47.414')).toThrow();
    expect(() => mkwrsTimeToMs('nope')).toThrow();
  });
});

describe('msToTimeStr', () => {
  it('formats canonical M:SS.mmm', () => {
    expect(msToTimeStr(107414)).toBe('1:47.414');
    expect(msToTimeStr(120000)).toBe('2:00.000');
  });
  it('round-trips with mkwrsTimeToMs', () => {
    expect(msToTimeStr(mkwrsTimeToMs('1\'39"008'))).toBe('1:39.008');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/wr/time.test.ts`
Expected: FAIL ("Cannot find module './time'").

- [ ] **Step 3: Write the implementation**

Create `pi/src/wr/time.ts`:

```ts
/** Parse a mkwrs time like `1'47"414` into milliseconds. ms field is 1-3 digits
 *  (thousandths/hundredths/tenths) and is normalized to 3 digits. Throws if malformed. */
export function mkwrsTimeToMs(raw: string): number {
  const m = /^(\d+)'(\d{1,2})"(\d{1,3})$/.exec(raw.trim());
  if (!m) throw new Error(`unparseable mkwrs time: ${JSON.stringify(raw)}`);
  const min = Number(m[1]);
  const sec = Number(m[2]);
  const ms = Number(m[3].padEnd(3, '0'));
  return min * 60000 + sec * 1000 + ms;
}

/** Format milliseconds as canonical `M:SS.mmm`. */
export function msToTimeStr(ms: number): string {
  const min = Math.floor(ms / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  const rem = ms % 1000;
  return `${min}:${String(sec).padStart(2, '0')}.${String(rem).padStart(3, '0')}`;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/wr/time.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/time.ts pi/src/wr/time.test.ts
git commit -m "feat(wr): mkwrs time parsing/formatting" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `courses.ts` (mkwrs name -> canonical course)

**Files:**
- Create: `pi/src/wr/courses.ts`
- Create: `pi/src/wr/__fixtures__/courses.ts` (shared test seed + name list)
- Test: `pi/src/wr/courses.test.ts`

- [ ] **Step 1: Create the shared course fixture**

Create `pi/src/wr/__fixtures__/courses.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';

/** The 30 canonical courses as [slug, display_name] (mirrors server/courses.py). */
export const CANONICAL_COURSES: [string, string][] = [
  ['mario_bros_circuit', 'Mario Bros. Circuit'],
  ['crown_city', 'Crown City'],
  ['whistlestop_summit', 'Whistlestop Summit'],
  ['dk_spaceport', 'DK Spaceport'],
  ['desert_hills', 'Desert Hills'],
  ['shy_guy_bazaar', 'Shy Guy Bazaar'],
  ['wario_stadium', 'Wario Stadium'],
  ['airship_fortress', 'Airship Fortress'],
  ['dk_pass', 'DK Pass'],
  ['starview_peak', 'Starview Peak'],
  ['sky_high_sundae', 'Sky-High Sundae'],
  ['warios_galleon', 'Wario’s Galleon'],
  ['koopa_troopa_beach', 'Koopa Troopa Beach'],
  ['faraway_oasis', 'Faraway Oasis'],
  ['peach_stadium', 'Peach Stadium'],
  ['peach_beach', 'Peach Beach'],
  ['salty_salty_speedway', 'Salty Salty Speedway'],
  ['dino_dino_jungle', 'Dino Dino Jungle'],
  ['great_block_ruins', 'Great ? Block Ruins'],
  ['cheep_cheep_falls', 'Cheep Cheep Falls'],
  ['dandelion_depths', 'Dandelion Depths'],
  ['boo_cinema', 'Boo Cinema'],
  ['dry_bones_burnout', 'Dry Bones Burnout'],
  ['moo_moo_meadows', 'Moo Moo Meadows'],
  ['choco_mountain', 'Choco Mountain'],
  ['toads_factory', 'Toad’s Factory'],
  ['bowsers_castle', 'Bowser’s Castle'],
  ['acorn_heights', 'Acorn Heights'],
  ['mario_circuit', 'Mario Circuit'],
  ['rainbow_road', 'Rainbow Road'],
];

/** The 30 track names exactly as they appear on mkwrs.com (note "Wario Shipyard"
 *  and straight apostrophes). Used by the completeness test. */
export const MKWRS_NAMES: string[] = [
  'Mario Bros. Circuit', 'Crown City', 'Whistlestop Summit', 'DK Spaceport', 'Desert Hills',
  'Shy Guy Bazaar', 'Wario Stadium', 'Airship Fortress', 'DK Pass', 'Starview Peak',
  'Sky-High Sundae', 'Wario Shipyard', 'Koopa Troopa Beach', 'Faraway Oasis', 'Peach Stadium',
  'Peach Beach', 'Salty Salty Speedway', 'Dino Dino Jungle', 'Great ? Block Ruins',
  'Cheep Cheep Falls', 'Dandelion Depths', 'Boo Cinema', 'Dry Bones Burnout', 'Moo Moo Meadows',
  'Choco Mountain', "Toad's Factory", "Bowser's Castle", 'Acorn Heights', 'Mario Circuit',
  'Rainbow Road',
];

export function seedCanonicalCourses(db: DatabaseSync): void {
  const stmt = db.prepare('INSERT OR IGNORE INTO courses(slug, display_name) VALUES (?, ?)');
  for (const [slug, name] of CANONICAL_COURSES) stmt.run(slug, name);
}
```

- [ ] **Step 2: Write the failing test**

Create `pi/src/wr/courses.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mkwrsNameToSlug, resolveCourseId } from './courses';
import { MKWRS_NAMES, seedCanonicalCourses } from './__fixtures__/courses';

function db() {
  const d = openDb(':memory:');
  applySchema(d);
  seedCanonicalCourses(d);
  return d;
}

describe('mkwrsNameToSlug', () => {
  it('aliases Wario Shipyard to warios_galleon', () => {
    expect(mkwrsNameToSlug('Wario Shipyard')).toBe('warios_galleon');
  });
  it('slugifies the rest (apostrophes dropped)', () => {
    expect(mkwrsNameToSlug("Toad's Factory")).toBe('toads_factory');
    expect(mkwrsNameToSlug('Great ? Block Ruins')).toBe('great_block_ruins');
  });
});

describe('resolveCourseId', () => {
  it('resolves every one of the 30 mkwrs course names (completeness)', () => {
    const d = db();
    const unresolved = MKWRS_NAMES.filter((n) => resolveCourseId(d, n) === null);
    expect(unresolved).toEqual([]);
  });
  it('returns null for glitch categories and unknown names', () => {
    const d = db();
    expect(resolveCourseId(d, 'Mario Bros. Circuit (Glitch)')).toBeNull();
    expect(resolveCourseId(d, 'Crown City (Glitch)')).toBeNull();
    expect(resolveCourseId(d, 'Totally Fake Track')).toBeNull();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/wr/courses.test.ts`
Expected: FAIL ("Cannot find module './courses'").

- [ ] **Step 4: Write the implementation**

Create `pi/src/wr/courses.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import { slugify } from '../db/slug';

/** mkwrs track names whose slug does not match the canonical slug. */
export const MKWRS_ALIASES: Record<string, string> = {
  'Wario Shipyard': 'warios_galleon',
};

export function mkwrsNameToSlug(name: string): string {
  const trimmed = name.trim();
  return MKWRS_ALIASES[trimmed] ?? slugify(trimmed);
}

/** Resolve a mkwrs track name to a canonical course id, or null for glitch
 *  categories and any name with no canonical course (caller skips + warns). */
export function resolveCourseId(db: DatabaseSync, name: string): number | null {
  if (/\(glitch\)/i.test(name)) return null;
  const slug = mkwrsNameToSlug(name);
  const row = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  return row ? row.id : null;
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/wr/courses.test.ts`
Expected: PASS. If the completeness test lists any name, a slug mismatch exists — add it to `MKWRS_ALIASES`.

- [ ] **Step 6: Commit**

```bash
git add pi/src/wr/courses.ts pi/src/wr/__fixtures__/courses.ts pi/src/wr/courses.test.ts
git commit -m "feat(wr): mkwrs course-name to canonical-course resolution" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `parse.ts` (HTML table -> ScrapedWr[])

**Files:**
- Modify: `pi/package.json` (add `node-html-parser`)
- Create: `pi/src/wr/__fixtures__/mkworld.html` (captured snapshot)
- Create: `pi/src/wr/parse.ts`
- Test: `pi/src/wr/parse.test.ts`

- [ ] **Step 1: Install the parser dependency**

Run: `cd pi && npm install node-html-parser`
Expected: `node-html-parser` appears in `package.json` dependencies and `package-lock.json` updates.

- [ ] **Step 2: Capture the live HTML fixture**

Run (from `pi/`):

```bash
node --input-type=module -e "import('node:fs').then(async fs => { const r = await fetch('https://mkwrs.com/mkworld/', { headers: { 'User-Agent': 'mkw-pi-wr-scraper/1.0' } }); if (!r.ok) throw new Error('HTTP ' + r.status); await fs.promises.mkdir('src/wr/__fixtures__', { recursive: true }); await fs.promises.writeFile('src/wr/__fixtures__/mkworld.html', await r.text()); console.log('saved'); })"
```

Expected: prints `saved` and creates `pi/src/wr/__fixtures__/mkworld.html`.
Then open the file and confirm: a `<table class="wr">` exists; data rows have a track-name link in cell 0, a time+video link in cell 1, player in cell 2, date in cell 4, character in cell 6, vehicle in cell 7; the two `(Glitch)` rows are present. If the markup differs from these assumptions, adjust the selectors/indices in Step 4 accordingly (this is the one fragile, real-world part).

- [ ] **Step 3: Write the failing test**

Create `pi/src/wr/parse.test.ts`. The fixture is frozen, so these structural assertions are deterministic:

```ts
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseWrTable } from './parse';

const html = readFileSync(new URL('./__fixtures__/mkworld.html', import.meta.url), 'utf8');

describe('parseWrTable', () => {
  const rows = parseWrTable(html);

  it('parses the 30 base courses and excludes the 2 (Glitch) rows', () => {
    // The captured fixture lists 30 base tracks + Mario Bros. Circuit (Glitch) +
    // Crown City (Glitch). If your capture includes DLC tracks, set this to the
    // base-course count you see in the file.
    expect(rows.length).toBe(30);
    expect(rows.some((r) => /\(glitch\)/i.test(r.courseName))).toBe(false);
  });

  it('every row is well-formed', () => {
    for (const r of rows) {
      expect(r.courseName.length).toBeGreaterThan(0);
      expect(Number.isInteger(r.recordMs)).toBe(true);
      expect(r.recordMs).toBeGreaterThan(0);
      expect(r.recordStr).toMatch(/^\d+:\d{2}\.\d{3}$/);
    }
  });

  it('extracts fields for a known track (Rainbow Road)', () => {
    const rr = rows.find((r) => r.courseName === 'Rainbow Road');
    expect(rr).toBeDefined();
    expect(rr!.holder && rr!.holder.length).toBeTruthy();
    // mkwrs entities must be decoded so apostrophe names slugify correctly elsewhere.
    expect(rr!.courseName).not.toContain('&');
    if (rr!.videoUrl) expect(rr!.videoUrl).toMatch(/^https?:/);
  });

  it('decodes apostrophe course names (entity decoding)', () => {
    // Toad's Factory / Bowser's Factory etc. must come through with a real apostrophe,
    // never "&#39;" - otherwise course resolution breaks.
    expect(rows.some((r) => /&#?\w+;/.test(r.courseName))).toBe(false);
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/wr/parse.test.ts`
Expected: FAIL ("Cannot find module './parse'").

- [ ] **Step 5: Write the implementation**

Create `pi/src/wr/parse.ts`:

```ts
import { parse } from 'node-html-parser';
import { mkwrsTimeToMs, msToTimeStr } from './time';

export type ScrapedWr = {
  courseName: string;
  recordMs: number;
  recordStr: string;
  holder: string | null;
  date: string | null;
  character: string | null;
  vehicle: string | null;
  videoUrl: string | null;
};

const cellText = (el: { querySelector: (s: string) => any; text: string } | null): string =>
  (el ? (el.querySelector('a')?.text ?? el.text) : '').trim();

export function parseWrTable(html: string): ScrapedWr[] {
  const root = parse(html);
  const table = root.querySelector('table.wr');
  if (!table) throw new Error('mkwrs: table.wr not found');
  const out: ScrapedWr[] = [];
  for (const tr of table.querySelectorAll('tr')) {
    const td = tr.querySelectorAll('td');
    if (td.length < 9) continue;                       // header / short rows
    const courseName = cellText(td[0]);
    if (!courseName || /\(glitch\)/i.test(courseName)) continue;
    const timeLink = td[1].querySelector('a');
    const timeText = (timeLink?.text ?? td[1].text).trim();
    let recordMs: number;
    try { recordMs = mkwrsTimeToMs(timeText); } catch { continue; }   // unparseable -> skip row
    out.push({
      courseName,
      recordMs,
      recordStr: msToTimeStr(recordMs),
      holder: cellText(td[2]) || null,
      date: td[4].text.trim() || null,
      character: td[6].text.trim() || null,
      vehicle: td[7].text.trim() || null,
      videoUrl: timeLink?.getAttribute('href') ?? null,
    });
  }
  return out;
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/wr/parse.test.ts`
Expected: PASS. If the "decodes apostrophe" or Rainbow Road tests fail with encoded entities, replace `.text` reads with `.textContent` (node-html-parser exposes both; `.text` is the decoded getter) and re-run. If `rows.length` is off, reconcile the number with the actual base-course count in your captured fixture.

- [ ] **Step 7: Commit**

```bash
git add pi/package.json pi/package-lock.json pi/src/wr/parse.ts pi/src/wr/parse.test.ts pi/src/wr/__fixtures__/mkworld.html
git commit -m "feat(wr): parse the mkwrs WR table into ScrapedWr rows" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `reconcile.ts` (mirror the table) + `wr_update` event

**Files:**
- Modify: `pi/src/db/types.ts` (add `wr_update` to `ServerEvent`)
- Create: `pi/src/wr/reconcile.ts`
- Test: `pi/src/wr/reconcile.test.ts`

- [ ] **Step 1: Add the `wr_update` event type**

In `pi/src/db/types.ts`, add this member to the end of the `ServerEvent` union (before the closing `;`):

```ts
  | { type: 'wr_update'; course: string; cc: number; holder: string | null;
      total_time: string; prev_holder: string | null; prev_time: string | null;
      improvement_ms: number | null; character: string | null;
      vehicle: string | null; video_url: string | null }
```

- [ ] **Step 2: Write the failing test**

Create `pi/src/wr/reconcile.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import type { ServerEvent } from '../db/types';
import { reconcile } from './reconcile';
import type { ScrapedWr } from './parse';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  const hub = new EventHub();
  const events: ServerEvent[] = [];
  hub.subscribe((e) => events.push(e));
  return { db, hub, events };
}

function wr(over: Partial<ScrapedWr> = {}): ScrapedWr {
  return {
    courseName: 'Rainbow Road', recordMs: 100000, recordStr: '1:40.000',
    holder: 'Alice', date: '2026-06-01', character: 'Mario', vehicle: 'B Dasher',
    videoUrl: 'https://youtu.be/aaa', ...over,
  };
}

function curRow(db: any) {
  return db.prepare('SELECT holder_name, record_ms, is_current, provenance, video_url FROM world_records WHERE course_id=1 AND is_current=1').get();
}

describe('reconcile', () => {
  it('establishes the first current with no event (cur was null)', () => {
    const { db, hub, events } = setup();
    const rep = reconcile(db, hub, [wr()], 150);
    expect(rep.inserted).toBe(1);
    expect(events).toEqual([]);                       // silent first establishment
    expect(curRow(db)).toMatchObject({ record_ms: 100000, is_current: 1, provenance: 'scraped' });
  });

  it('inserts a strictly faster WR, moves current, emits a positive wr_update', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr()], 150);                  // baseline
    events.length = 0;
    const rep = reconcile(db, hub, [wr({ recordMs: 99000, recordStr: '1:39.000', holder: 'Bob' })], 150);
    expect(rep.inserted).toBe(1);
    expect(curRow(db)).toMatchObject({ record_ms: 99000, holder_name: 'Bob', is_current: 1 });
    expect(db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1 AND is_current=1').get()).toEqual({ c: 1 });
    expect(events).toEqual([{
      type: 'wr_update', course: 'Rainbow Road', cc: 150, holder: 'Bob', total_time: '1:39.000',
      prev_holder: 'Alice', prev_time: '1:40.000', improvement_ms: 1000,
      character: 'Mario', vehicle: 'B Dasher', video_url: 'https://youtu.be/aaa',
    }]);
  });

  it('reverts to an existing history row (DQ) without inserting a duplicate', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ recordMs: 100000, holder: 'Alice', recordStr: '1:40.000' })], 150);
    reconcile(db, hub, [wr({ recordMs: 99000, holder: 'Bob', recordStr: '1:39.000' })], 150);
    const before = db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number };
    events.length = 0;
    // Bob's run is removed; the page reverts to Alice 1:40.000.
    const rep = reconcile(db, hub, [wr({ recordMs: 100000, holder: 'Alice', recordStr: '1:40.000' })], 150);
    expect(rep.reflagged).toBe(1);
    expect(rep.inserted).toBe(0);
    const after = db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number };
    expect(after.c).toBe(before.c);                   // no new row
    expect(curRow(db)).toMatchObject({ record_ms: 100000, holder_name: 'Alice', is_current: 1 });
    expect(events[0]).toMatchObject({ type: 'wr_update', improvement_ms: -1000 });   // reverted, slower
  });

  it('backfills a later-added video on the unchanged current, with no event and no new row', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ videoUrl: null })], 150);
    const before = db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number };
    events.length = 0;
    const rep = reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/new' })], 150);
    expect(rep.backfilled).toBe(1);
    expect(rep.inserted).toBe(0);
    expect((db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number }).c).toBe(before.c);
    expect(curRow(db)).toMatchObject({ video_url: 'https://youtu.be/new' });
    expect(events).toEqual([]);
  });

  it('does not overwrite a non-null holder during backfill', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ holder: 'Alice' })], 150);
    reconcile(db, hub, [wr({ holder: 'Alice', character: 'Peach' })], 150);   // same record, new character
    expect(curRow(db)).toMatchObject({ holder_name: 'Alice' });
  });

  it('is idempotent: re-running the same scrape writes nothing', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr()], 150);
    const rep = reconcile(db, hub, [wr()], 150);
    expect(rep).toMatchObject({ inserted: 0, reflagged: 0, backfilled: 0, unchanged: 1 });
  });

  it('keeps a course current when it is absent from the batch', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr()], 150);
    reconcile(db, hub, [], 150);                      // empty scrape
    expect(curRow(db)).toMatchObject({ record_ms: 100000, is_current: 1 });
  });

  it('records unmapped course names without throwing', () => {
    const { db, hub } = setup();
    const rep = reconcile(db, hub, [wr({ courseName: 'Mystery Track' })], 150);
    expect(rep.unmapped).toEqual(['Mystery Track']);
    expect(rep.inserted).toBe(0);
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/wr/reconcile.test.ts`
Expected: FAIL ("Cannot find module './reconcile'").

- [ ] **Step 4: Write the implementation**

Create `pi/src/wr/reconcile.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import { resolveCourseId } from './courses';
import type { ScrapedWr } from './parse';

export type WrReport = {
  inserted: number;
  reflagged: number;
  backfilled: number;
  unchanged: number;
  unmapped: string[];
};

type Row = {
  id: number; holder_name: string | null; record_ms: number; record_str: string;
  video_url: string | null; character: string | null; vehicle: string | null;
};

const isoDate = (date: string | null): string =>
  date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00.000Z` : new Date().toISOString();

/** Update video/character/vehicle (and holder if currently null) on `row` from the
 *  scrape, only where the scraped value is non-empty and differs. Returns true if it wrote. */
function backfill(db: DatabaseSync, row: Row, s: ScrapedWr): boolean {
  const sets: string[] = [];
  const vals: (string | null)[] = [];
  if (row.holder_name == null && s.holder) { sets.push('holder_name=?'); vals.push(s.holder); }
  if (s.videoUrl && s.videoUrl !== row.video_url) { sets.push('video_url=?'); vals.push(s.videoUrl); }
  if (s.character && s.character !== row.character) { sets.push('character=?'); vals.push(s.character); }
  if (s.vehicle && s.vehicle !== row.vehicle) { sets.push('vehicle=?'); vals.push(s.vehicle); }
  if (sets.length === 0) return false;
  db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, row.id);
  return true;
}

export function reconcile(db: DatabaseSync, hub: EventHub, scraped: ScrapedWr[], cc = 150): WrReport {
  const report: WrReport = { inserted: 0, reflagged: 0, backfilled: 0, unchanged: 0, unmapped: [] };
  for (const s of scraped) {
    const courseId = resolveCourseId(db, s.courseName);
    if (courseId === null) { report.unmapped.push(s.courseName); continue; }
    try { reconcileOne(db, hub, s, courseId, cc, report); }
    catch (e) { console.error(`[wr] reconcile failed for ${s.courseName}:`, e); }
  }
  return report;
}

function reconcileOne(db: DatabaseSync, hub: EventHub, s: ScrapedWr, courseId: number, cc: number, report: WrReport): void {
  const cur = db.prepare(
    `SELECT id, holder_name, record_ms, record_str, video_url, character, vehicle
     FROM world_records WHERE course_id=? AND cc=? AND is_current=1`
  ).get(courseId, cc) as Row | undefined;

  // Case 1: same record as current -> backfill metadata in place, no current move.
  if (cur && cur.record_ms === s.recordMs && cur.holder_name === s.holder) {
    if (backfill(db, cur, s)) report.backfilled++; else report.unchanged++;
    return;
  }

  // Case 2: the current WR changed -> mirror the page (one transaction).
  db.exec('BEGIN');
  try {
    if (cur) db.prepare('UPDATE world_records SET is_current=0 WHERE id=?').run(cur.id);
    const existing = db.prepare(
      `SELECT id, holder_name, record_ms, record_str, video_url, character, vehicle
       FROM world_records WHERE course_id=? AND cc=? AND record_ms=? AND holder_name IS ?
       ORDER BY id DESC LIMIT 1`
    ).get(courseId, cc, s.recordMs, s.holder) as Row | undefined;
    if (existing) {
      db.prepare('UPDATE world_records SET is_current=1 WHERE id=?').run(existing.id);
      backfill(db, existing, s);
      report.reflagged++;
    } else {
      db.prepare(
        `INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str,
           achieved_at, video_url, character, vehicle, provenance, is_current)
         VALUES (?,?,?,?,?,?,?,?,?, 'scraped', 1)`
      ).run(courseId, cc, s.holder, s.recordMs, s.recordStr,
            isoDate(s.date), s.videoUrl, s.character, s.vehicle);
      report.inserted++;
    }
    db.exec('COMMIT');
  } catch (e) { db.exec('ROLLBACK'); throw e; }

  // Emit only when a prior current existed (silent first-scrape establishment).
  if (cur) {
    hub.publish({
      type: 'wr_update', course: s.courseName, cc,
      holder: s.holder, total_time: s.recordStr,
      prev_holder: cur.holder_name, prev_time: cur.record_str,
      improvement_ms: cur.record_ms - s.recordMs,
      character: s.character, vehicle: s.vehicle, video_url: s.videoUrl,
    });
  }
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/wr/reconcile.test.ts`
Expected: PASS (all cases).

- [ ] **Step 6: Commit**

```bash
git add pi/src/db/types.ts pi/src/wr/reconcile.ts pi/src/wr/reconcile.test.ts
git commit -m "feat(wr): reconcile scrapes into world_records (mirror + backfill + wr_update)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: `scrape.ts` (fetch -> parse -> reconcile)

**Files:**
- Create: `pi/src/wr/scrape.ts`
- Test: `pi/src/wr/scrape.test.ts`

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/scrape.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import { scrapeOnce } from './scrape';
import { seedCanonicalCourses } from './__fixtures__/courses';

const html = readFileSync(new URL('./__fixtures__/mkworld.html', import.meta.url), 'utf8');

describe('scrapeOnce', () => {
  it('drives parse->reconcile from an injected fetcher and seeds all current WRs', async () => {
    const db = openDb(':memory:');
    applySchema(db);
    seedCanonicalCourses(db);
    const rep = await scrapeOnce(db, new EventHub(), { fetchHtml: async () => html });
    // First scrape of a freshly seeded DB inserts a current WR for each parsed course.
    expect(rep.inserted).toBe(30);
    expect(rep.unmapped).toEqual([]);
    const currents = db.prepare('SELECT COUNT(*) c FROM world_records WHERE is_current=1').get() as { c: number };
    expect(currents.c).toBe(30);
  });

  it('is idempotent across two runs', async () => {
    const db = openDb(':memory:');
    applySchema(db);
    seedCanonicalCourses(db);
    await scrapeOnce(db, new EventHub(), { fetchHtml: async () => html });
    const rep = await scrapeOnce(db, new EventHub(), { fetchHtml: async () => html });
    expect(rep.inserted).toBe(0);
    expect(rep.reflagged).toBe(0);
    expect(rep.unchanged).toBe(30);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/wr/scrape.test.ts`
Expected: FAIL ("Cannot find module './scrape'").

- [ ] **Step 3: Write the implementation**

Create `pi/src/wr/scrape.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import { parseWrTable } from './parse';
import { reconcile, type WrReport } from './reconcile';

export const DEFAULT_MKWRS_URL = 'https://mkwrs.com/mkworld/';

export type ScrapeOpts = {
  url?: string;
  cc?: number;
  fetchHtml?: (url: string) => Promise<string>;
};

export async function scrapeOnce(db: DatabaseSync, hub: EventHub, opts: ScrapeOpts = {}): Promise<WrReport> {
  const url = opts.url ?? DEFAULT_MKWRS_URL;
  const cc = opts.cc ?? 150;
  const fetchHtml = opts.fetchHtml ?? defaultFetchHtml;
  const html = await fetchHtml(url);
  return reconcile(db, hub, parseWrTable(html), cc);
}

async function defaultFetchHtml(url: string): Promise<string> {
  const res = await fetch(url, {
    headers: { 'User-Agent': 'mkw-pi-wr-scraper/1.0' },
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`mkwrs fetch failed: HTTP ${res.status}`);
  return res.text();
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/wr/scrape.test.ts`
Expected: PASS. (If `inserted`/`unchanged` are not 30, reconcile them with the base-course count in your fixture, matching Task 5.)

- [ ] **Step 5: Commit**

```bash
git add pi/src/wr/scrape.ts pi/src/wr/scrape.test.ts
git commit -m "feat(wr): scrapeOnce orchestrator (injectable fetch)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: scheduler + server wiring + CLI

**Files:**
- Create: `pi/src/wr/scheduler.ts`
- Test: `pi/src/wr/scheduler.test.ts`
- Create: `pi/src/scripts/scrapeWr.ts`
- Modify: `pi/src/server.ts`
- Modify: `pi/package.json` (`scrape-wr` script)

- [ ] **Step 1: Write the failing scheduler test**

Create `pi/src/wr/scheduler.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest';
import { startWrScraper } from './scheduler';

const emptyReport = { inserted: 0, reflagged: 0, backfilled: 0, unchanged: 0, unmapped: [] };

describe('startWrScraper', () => {
  it('runs once immediately, then on the interval, and stops on demand', async () => {
    vi.useFakeTimers();
    try {
      const scrape = vi.fn(async () => emptyReport);
      const stop = startWrScraper({} as any, {} as any, { url: 'x', intervalSec: 1, scrape });
      expect(scrape).toHaveBeenCalledTimes(1);                 // immediate
      await vi.advanceTimersByTimeAsync(1000);
      expect(scrape).toHaveBeenCalledTimes(2);                 // one interval
      stop();
      await vi.advanceTimersByTimeAsync(3000);
      expect(scrape).toHaveBeenCalledTimes(2);                 // stopped
    } finally { vi.useRealTimers(); }
  });

  it('is disabled when intervalSec <= 0', () => {
    const scrape = vi.fn(async () => emptyReport);
    const stop = startWrScraper({} as any, {} as any, { url: 'x', intervalSec: 0, scrape });
    expect(scrape).not.toHaveBeenCalled();
    stop();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/wr/scheduler.test.ts`
Expected: FAIL ("Cannot find module './scheduler'").

- [ ] **Step 3: Write the scheduler**

Create `pi/src/wr/scheduler.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import { scrapeOnce, type ScrapeOpts } from './scrape';
import type { WrReport } from './reconcile';

type ScrapeFn = (db: DatabaseSync, hub: EventHub, opts: ScrapeOpts) => Promise<WrReport>;

export type SchedulerOpts = {
  url?: string;
  intervalSec: number;
  scrape?: ScrapeFn;            // injectable for tests; defaults to scrapeOnce
};

/** Start the in-process WR scraper: one run immediately, then every intervalSec.
 *  Each tick is isolated (a failure logs, never throws); overlapping ticks are skipped.
 *  intervalSec <= 0 disables it. Returns a stop function. */
export function startWrScraper(db: DatabaseSync, hub: EventHub, opts: SchedulerOpts): () => void {
  const { url, intervalSec } = opts;
  const scrape = opts.scrape ?? scrapeOnce;
  if (!intervalSec || intervalSec <= 0) return () => {};

  let running = false;
  const tick = async () => {
    if (running) return;
    running = true;
    try {
      const rep = await scrape(db, hub, { url });
      console.log(`[wr] scrape: ${JSON.stringify(rep)}`);
    } catch (e) {
      console.error('[wr] scrape failed:', e);
    } finally {
      running = false;
    }
  };

  void tick();                                          // immediate, non-blocking
  const id = setInterval(() => void tick(), intervalSec * 1000);
  return () => clearInterval(id);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/wr/scheduler.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire the scheduler into `server.ts`**

In `pi/src/server.ts`, add the import near the others:

```ts
import { startWrScraper } from './wr/scheduler';
```

And after the `injectWebSocket(server);` line, add:

```ts
startWrScraper(db, hub, {
  url: process.env.MKWRS_URL,
  intervalSec: Number(process.env.MKWRS_INTERVAL_SEC ?? 300),
});
```

- [ ] **Step 6: Add the CLI one-shot**

Create `pi/src/scripts/scrapeWr.ts`:

```ts
import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import { scrapeOnce } from '../wr/scrape';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
scrapeOnce(db, new EventHub(), { url: process.env.MKWRS_URL })
  .then((rep) => { console.log('[wr] scrape complete:', JSON.stringify(rep)); process.exit(0); })
  .catch((e) => { console.error('[wr] scrape failed:', e); process.exit(1); });
```

- [ ] **Step 7: Register the CLI script**

In `pi/package.json`, add to `scripts` (after `set-color`):

```json
    "scrape-wr": "node --no-warnings --import tsx src/scripts/scrapeWr.ts"
```

- [ ] **Step 8: Typecheck + full suite**

Run: `cd pi && npx tsc --noEmit`
Expected: no type errors in the files this plan created/changed (`pi/src/wr/**`, `pi/src/db/connect.ts`, `pi/src/db/reads.ts`, `pi/src/db/types.ts`, `pi/src/scripts/scrapeWr.ts`, `pi/src/server.ts`). If `tsc` reports pre-existing errors in unrelated files, confirm none are in your changed files and proceed.

Then run: `cd pi && npm test`
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add pi/src/wr/scheduler.ts pi/src/wr/scheduler.test.ts pi/src/scripts/scrapeWr.ts pi/src/server.ts pi/package.json
git commit -m "feat(wr): in-process scheduler, server wiring, and scrape-wr CLI" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Manual end-to-end verification against a real DB

**Files:** none (verification only)

- [ ] **Step 1: Copy the real DB so live data is untouched**

Run (from `pi/`):

```bash
node -e "require('fs').copyFileSync('mkw.db','mkw.wrtest.db')"
```

- [ ] **Step 2: Run the CLI against the copy**

Run: `cd pi && MKW_DB=mkw.wrtest.db npm run scrape-wr`
(Windows PowerShell: `$env:MKW_DB='mkw.wrtest.db'; npm run scrape-wr`)
Expected: prints `[wr] scrape complete: {"inserted":...,"reflagged":...,"backfilled":...,"unchanged":...,"unmapped":[]}`. `unmapped` should be `[]` (every mkwrs track maps). Most courses will be `unchanged` or `backfilled`; `inserted`/`reflagged` only where mkwrs differs from the imported data.

- [ ] **Step 3: Confirm idempotency**

Run the same command again.
Expected: `inserted: 0`, `reflagged: 0`, and only genuine same-day metadata `backfilled` (often 0). `unmapped` still `[]`.

- [ ] **Step 4: Confirm exactly one current per course**

Run: `cd pi && node -e "const {DatabaseSync}=require('node:sqlite');const db=new DatabaseSync('mkw.wrtest.db');console.log('courses',db.prepare('SELECT COUNT(*) c FROM courses').get());console.log('currents',db.prepare('SELECT COUNT(*) c FROM world_records WHERE is_current=1').get());console.log('dupes',db.prepare('SELECT course_id,cc,COUNT(*) n FROM world_records WHERE is_current=1 GROUP BY course_id,cc HAVING n>1').all());"`
Expected: `currents` equals the number of courses that appear on mkwrs (30), and `dupes` is `[]`.

- [ ] **Step 5: Clean up the test DB**

Run: `cd pi && node -e "const fs=require('fs');for(const f of ['mkw.wrtest.db','mkw.wrtest.db-shm','mkw.wrtest.db-wal'])try{fs.unlinkSync(f)}catch{}"`
Expected: no output (files removed). Confirm `git status` shows only the intended source changes (no stray `*.wrtest.db*`).

- [ ] **Step 6: Final full suite**

Run: `cd pi && npm test`
Expected: all tests PASS.

---

## Notes for the implementer

- `node:sqlite` run scripts pass `--no-warnings` (it is an experimental built-in). `tsx` runs the TS directly; there is no build step.
- The only network access is the live `fetch` in `scrape.ts` (Task 5 fixture capture, Task 9). Every unit test injects HTML or constructs `ScrapedWr` objects, so the suite is offline and deterministic.
- The single fragile point is the mkwrs HTML structure (Task 5). If selectors/indices need adjusting, do it against the captured fixture; the parser test invariants (30 rows, glitch excluded, decoded names, well-formed times) are the contract.
- Do not edit the Python `server/` importer; a fresh import is baselined by the first scrape (silent, no events), and the existing `pi/mkw.db` is baselined by the Task 1 migration seed.
