# Remove player "Alex" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Erase player "Alex" from every surface (DB, Discord bot, Pi stats, website, pbenguin desktop, asset configs) and invalidate his token, via an idempotent Pi boot migration plus removal of all hardcoded/bundled references.

**Architecture:** A new idempotent `purgeRemovedPlayers` boot migration deletes all of Alex's DB rows and his `players` row (which invalidates his token), wired into `server.ts` after the trail migration. The recovery source JSON is scrubbed so a fresh DB never re-imports him. Every hardcoded reference across bot config, stats map, website wordmark/GIFs, desktop colours/PNGs, and the asset source config is removed. All player-list surfaces are otherwise API/DB-driven and clear automatically.

**Tech Stack:** Node/TS (`tsx`, vitest, `node:sqlite`), Svelte/Vite (web + desktop `src/`), JSON config, Python (data scrub).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-13-remove-player-alex-design.md`.
- Delete Alex **entirely** (no "hidden" flag). His whole `players` row is removed.
- The DB migration MUST be **idempotent** (re-running is a no-op) and **must never block boot** (guard each statement; roll back the transaction on error).
- DB delete order is FK-safe (`foreign_keys = ON` in `openDb`): children before `runs`, then remaining player-referencing tables, then the `players` row.
- Pi tests run from `pi/` with `npm test` (vitest); typecheck with `npm run typecheck` (non-gating but keep clean). Web tests run from `web/` with `npm test`.
- Pi-served binaries (`web/public/**`) are ordinary git files, never Git LFS.
- Commit after each task. Branch: `remove-player-alex` (already created).

---

### Task 1: DB purge migration + unit test

**Files:**
- Create: `pi/src/db/purgeRemovedPlayers.ts`
- Test: `pi/src/db/purgeRemovedPlayers.test.ts`

**Interfaces:**
- Consumes: `openDb`, `applySchema` from `pi/src/db/connect.ts`.
- Produces: `purgeRemovedPlayers(db: DatabaseSync): void` — deletes all rows for each display name in the module-level `REMOVED_PLAYERS` list (currently `['Alex']`) and the `players` row itself; idempotent.

- [ ] **Step 1: Write the failing test**

Create `pi/src/db/purgeRemovedPlayers.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { purgeRemovedPlayers } from './purgeRemovedPlayers';

/** Seed a DB with a keeper player (Paul, id 1) and Alex (id 3), Alex having rows in every
 *  player-referencing table, plus Paul rows that must survive. */
function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,auth_token_hash) VALUES (1,'Paul','hashP'),(3,'Alex','hashA')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,3)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  // runs: Paul id 10 (keeper), Alex id 20
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1),(20,1,3,1,150,'finished','live',112000,1)");
  db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (10,0,54000),(10,1,54000),(20,0,56000),(20,1,56000)");
  db.exec("INSERT INTO run_trails(run_id,codec,n,max_t_ms,data) VALUES (10,1,1,1000,X'00'),(20,1,1,1000,X'00')");
  db.exec("INSERT INTO ghost_imports(run_id,player_id,course_id,cc,action) VALUES (20,3,1,150,'new')");
  db.exec("INSERT INTO screen_intervals(season_id,player_id,screen,started_ms,ended_ms) VALUES (1,3,'racing',1,2)");
  db.exec("INSERT INTO activity_events(ts,type,season_id,player_id) VALUES (1,'presence',1,3),(2,'presence',1,1)");
  db.exec("INSERT INTO player_alignment(player_id,dx,dy,scale,sample_count) VALUES (1,0,0,1,1),(3,0,0,1,1)");
  return db;
}

const count = (db: any, sql: string, ...args: any[]) =>
  (db.prepare(sql).get(...args) as { c: number }).c;

describe('purgeRemovedPlayers', () => {
  it('deletes every Alex row across all tables and the players row', () => {
    const db = seeded();
    purgeRemovedPlayers(db);
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Alex'")).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM runs WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM run_laps WHERE run_id=20')).toBe(0);   // cascade
    expect(count(db, 'SELECT COUNT(*) c FROM run_trails WHERE run_id=20')).toBe(0); // cascade
    expect(count(db, 'SELECT COUNT(*) c FROM ghost_imports WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM screen_intervals WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM activity_events WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM player_alignment WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM season_rosters WHERE player_id=3')).toBe(0);
  });

  it('leaves the keeper player and their rows untouched', () => {
    const db = seeded();
    purgeRemovedPlayers(db);
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Paul'")).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM runs WHERE player_id=1')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM run_laps WHERE run_id=10')).toBe(2);
    expect(count(db, 'SELECT COUNT(*) c FROM run_trails WHERE run_id=10')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM activity_events WHERE player_id=1')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM season_rosters WHERE player_id=1')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM player_alignment WHERE player_id=1')).toBe(1);
  });

  it('is idempotent — a second run is a no-op and does not throw', () => {
    const db = seeded();
    purgeRemovedPlayers(db);
    expect(() => purgeRemovedPlayers(db)).not.toThrow();
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Paul'")).toBe(1);
  });

  it('is a no-op on a DB with no Alex', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    expect(() => purgeRemovedPlayers(db)).not.toThrow();
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Paul'")).toBe(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npm test -- purgeRemovedPlayers`
Expected: FAIL — cannot resolve `./purgeRemovedPlayers` (module not found).

- [ ] **Step 3: Write minimal implementation**

Create `pi/src/db/purgeRemovedPlayers.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';

// Players removed from the kart-off entirely. Idempotent: once a player's rows are gone, re-running
// is a no-op. Deletes ALL data for the player (runs, laps, trails, activity, alignment, roster) and
// the players row itself — which also invalidates their auth token. Applied on every boot.
const REMOVED_PLAYERS = ['Alex'];   // display_name, matched COLLATE NOCASE

export function purgeRemovedPlayers(db: DatabaseSync): void {
  for (const name of REMOVED_PLAYERS) {
    const row = db.prepare('SELECT id FROM players WHERE display_name = ? COLLATE NOCASE')
      .get(name) as { id: number } | undefined;
    if (!row) continue;                       // idempotent: already gone
    const id = row.id;
    db.exec('BEGIN');
    try {
      // FK-safe order (foreign_keys=ON): children first, then runs (cascades laps/trails),
      // then the remaining player-referencing tables, then the players row. Each statement is
      // guarded so a table absent on an older/fresh DB never blocks boot.
      const del = (sql: string) => { try { db.prepare(sql).run(id); } catch { /* table may not exist */ } };
      del('DELETE FROM ghost_imports    WHERE player_id = ?');   // references runs(id) w/o cascade — precede runs
      del('DELETE FROM run_points       WHERE run_id IN (SELECT id FROM runs WHERE player_id = ?)'); // retired table; may persist on prod
      del('DELETE FROM runs             WHERE player_id = ?');   // cascades run_laps, run_trails
      del('DELETE FROM screen_intervals WHERE player_id = ?');
      del('DELETE FROM activity_events  WHERE player_id = ?');
      del('DELETE FROM player_alignment WHERE player_id = ?');
      del('DELETE FROM season_rosters   WHERE player_id = ?');
      del('DELETE FROM players          WHERE id = ?');
      db.exec('COMMIT');
    } catch {
      db.exec('ROLLBACK');   // non-fatal: never block boot on the purge
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npm test -- purgeRemovedPlayers`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/purgeRemovedPlayers.ts pi/src/db/purgeRemovedPlayers.test.ts
git commit -m "feat(pi): idempotent purgeRemovedPlayers migration (deletes a player entirely)"
```

---

### Task 2: Wire the migration into server boot

**Files:**
- Modify: `pi/src/server.ts` (imports near line 5; call after `migrateTrails(db)` at line 28)

**Interfaces:**
- Consumes: `purgeRemovedPlayers` from Task 1.

- [ ] **Step 1: Add the import**

In `pi/src/server.ts`, after the existing migration imports (near line 5, alongside `migrateTrails`), add:

```ts
import { purgeRemovedPlayers } from './db/purgeRemovedPlayers';
```

- [ ] **Step 2: Add the call in FK/ordering-safe position**

In `pi/src/server.ts`, immediately after the `migrateTrails(db);` line (currently line 28) and before `backfillActivity(db)`, add:

```ts
purgeRemovedPlayers(db);       // remove ex-participants entirely (after recovery + trail migration, before activity backfill)
```

The resulting order must read: `applySchema` → `migrateSeason0Recovered` → `migratePlayerRenames` → `migrateTrails` → **`purgeRemovedPlayers`** → `backfillActivity`.

- [ ] **Step 3: Verify placement and typecheck**

Run: `cd pi && npx tsc --noEmit` (expect no new errors)
Run (placement check): `grep -n "migrateTrails\|purgeRemovedPlayers\|backfillActivity" src/server.ts`
Expected: `purgeRemovedPlayers` appears on a line **after** `migrateTrails` and **before** `backfillActivity`.

- [ ] **Step 4: Commit**

```bash
git add pi/src/server.ts
git commit -m "feat(pi): run purgeRemovedPlayers on boot (after trail migration)"
```

---

### Task 3: Scrub the recovery source JSON

**Files:**
- Modify: `server/data/season0_recovery.json` (remove every element where `player == "Alex"`)

- [ ] **Step 1: Record the before-counts**

Run: `python -c "import json; d=json.load(open('server/data/season0_recovery.json')); from collections import Counter; c=Counter(x['player'] for x in d); print('total',len(d)); print(dict(c))"`
Expected: prints total count and a per-player breakdown including a non-zero `Alex`. Note Alex's count and the other players' counts.

- [ ] **Step 2: Filter out Alex and write back**

Run:
```bash
python -c "import json; p='server/data/season0_recovery.json'; d=json.load(open(p)); d=[x for x in d if x.get('player')!='Alex']; json.dump(d, open(p,'w'), ensure_ascii=False)"
```

- [ ] **Step 3: Verify Alex is gone and others are intact**

Run: `python -c "import json; d=json.load(open('server/data/season0_recovery.json')); from collections import Counter; c=Counter(x['player'] for x in d); print('total',len(d)); print(dict(c)); assert 'Alex' not in c, 'Alex still present'"`
Expected: no `Alex` key; other players' counts unchanged from Step 1; JSON still parses.

- [ ] **Step 4: Commit**

```bash
git add server/data/season0_recovery.json
git commit -m "chore(data): drop Alex's runs from the Season 0 recovery source"
```

---

### Task 4: Remove Pi hardcoded references (bot config + stats map)

**Files:**
- Modify: `pi/src/bot/players.config.ts` (delete Alex's `ID_TO_NAME`, `THUMBNAIL_GIFS`, `TEMP_THUMBNAILS` entries; fix the TEMP comment)
- Modify: `pi/src/stats/body.ts` (delete Alex from `PORKER_MAP`)

- [ ] **Step 1: Edit `pi/src/bot/players.config.ts`**

Delete the `ID_TO_NAME` line:
```ts
  '201561251963207681': 'Alex',
```
Delete the `THUMBNAIL_GIFS.Alex` line:
```ts
  Alex: ['https://i.imgur.com/0ZUvDVI.gif', 'https://i.imgur.com/OIPESbG.gif'],
```
Delete the `TEMP_THUMBNAILS.Alex` line:
```ts
  Alex: 'https://i.imgur.com/aw8z3He.png',
```
Update the TEMP comment (lines ~26–28) from:
```ts
// TEMP (2026-06-23): Gub + Alex thumbnails are pinned to a single static image instead of
// their GIFs above; everyone else keeps their GIFs. To restore, delete TEMP_THUMBNAILS and
// the override line in gifFor().
```
to:
```ts
// TEMP (2026-06-23): Gub's thumbnail is pinned to a single static image instead of its GIFs
// above; everyone else keeps their GIFs. To restore, delete TEMP_THUMBNAILS and the override
// line in gifFor().
```

- [ ] **Step 2: Edit `pi/src/stats/body.ts`**

Delete the `PORKER_MAP` line:
```ts
  { person: 'alex', player: 'Alex' },
```

- [ ] **Step 3: Run Pi tests + typecheck**

Run: `cd pi && npm test`
Expected: PASS (no bot/stats test references Alex; all green).
Run: `cd pi && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add pi/src/bot/players.config.ts pi/src/stats/body.ts
git commit -m "chore(pi): drop Alex from bot roster config and porker stats map"
```

---

### Task 5: Remove frontend + bundled asset references

**Files:**
- Modify: `web/src/lib/wordmark.config.json` (delete `.players.alex`)
- Modify: `src/lib/trailSettings.js` (delete the `alex` colour line)
- Modify: `assets/player_figures.json` (delete top-level `alex`)
- Delete: `web/public/players/alex.gif`, `web/public/players/alex__fire.gif`
- Delete: `src/assets/players/alex__on.png`, `src/assets/players/alex__off.png`, `src/assets/players/alex__onpace.png`

- [ ] **Step 1: Remove the live navbar wordmark entry**

Run (comma-safe JSON edit):
```bash
python -c "import json; p='web/src/lib/wordmark.config.json'; d=json.load(open(p)); d.get('players',{}).pop('alex',None); json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
```
Verify: `python -c "import json; d=json.load(open('web/src/lib/wordmark.config.json')); assert 'alex' not in d['players']; print(list(d['players']))"` → prints players without `alex`.

- [ ] **Step 2: Remove the desktop/shared trail colour**

In `src/lib/trailSettings.js`, delete this single line from the `PLAYER_COLORS` object:
```js
  alex:   "#3d7cc2",   // blue
```

- [ ] **Step 3: Remove the asset source-config entry**

Run:
```bash
python -c "import json; p='assets/player_figures.json'; d=json.load(open(p)); d.pop('alex',None); json.dump(d, open(p,'w'), indent=2, ensure_ascii=False)"
```
Verify: `python -c "import json; d=json.load(open('assets/player_figures.json')); assert 'alex' not in d; print(list(d))"`.

- [ ] **Step 4: Delete the generated/bundled assets**

Run:
```bash
git rm web/public/players/alex.gif web/public/players/alex__fire.gif \
       src/assets/players/alex__on.png src/assets/players/alex__off.png src/assets/players/alex__onpace.png
```

- [ ] **Step 5: Run web tests + build**

Run: `cd web && npm test`
Expected: PASS (no production web test asserts Alex; fixtures self-seed).
Run: `cd web && npm run build`
Expected: build succeeds (no missing-asset import errors — Alex assets were referenced only via name-keyed `import.meta.glob`, which now yields nothing for `alex`).

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/wordmark.config.json src/lib/trailSettings.js assets/player_figures.json
git commit -m "chore(web,app): remove Alex from wordmark, trail colours, figure config, and delete his assets"
```

---

### Task 6 (optional): Test-fixture clarity rename

**Not required for correctness** — existing tests pass unchanged. Do this only to stop the codebase implying Alex is still a participant. Skip any fixture where "Alex" is a meaningful, self-contained placeholder and renaming would obscure intent.

**Files (rename `Alex`/`alex` placeholder → a neutral name, e.g. `Robin`/`robin`):**
- Modify: `web/src/lib/turf.test.js`
- Modify: `pi/src/turf/rank.test.ts`
- Modify: `src/lib/trailSettings.test.js`
- Modify: `src/lib/presence.test.js`
- Modify: `pi/src/db/reads.test.ts`

- [ ] **Step 1: In each file, replace the placeholder name**

For each file above, replace the fixture string `"Alex"` (and any `alex` key that pairs with it) with `"Robin"` (`robin`). Keep IDs, colours, and assertions otherwise identical — only the display string changes. Example (`src/lib/trailSettings.test.js`): `{ player_id: 3, display_name: "Alex", is_me: false }` → `{ player_id: 3, display_name: "Robin", is_me: false }`, and update the matching assertion row `["Alex", "last_pb", ...]` → `["Robin", "last_pb", ...]`.

- [ ] **Step 2: Run both suites**

Run: `cd pi && npm test`
Run: `cd web && npm test`
Expected: PASS for both.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/turf.test.js pi/src/turf/rank.test.ts src/lib/trailSettings.test.js src/lib/presence.test.js pi/src/db/reads.test.ts
git commit -m "test: rename 'Alex' placeholder fixtures to a neutral name"
```

---

### Task 7: Final verification sweep

**Files:** none modified (gate only; commit only if a stray reference is found and fixed).

- [ ] **Step 1: Grep the whole repo for any remaining Alex reference in shipping code**

Run:
```bash
grep -rn -i "alex" \
  --include=*.ts --include=*.js --include=*.svelte --include=*.json --include=*.py \
  pi/src web/src src server scripts assets \
  | grep -v -i "node_modules\|\.test\.\|/__fixtures__/"
```
Expected: **no output** (every production reference removed). If any line appears, it is either (a) a leftover to remove — fix it and note the file, or (b) a legitimate non-participant reference (verify and leave). Test-fixture and WR-scraper `__fixtures__` hits are intentionally excluded and out of scope.

- [ ] **Step 2: Confirm the DB migration + suites are green end-to-end**

Run: `cd pi && npm test`
Run: `cd web && npm test`
Run (root Python tests, if the harness runs them): `python -m pytest tests/test_bundle_web_player_gifs.py -q`
Expected: all PASS.

- [ ] **Step 3: (If Step 1 required a fix) commit**

```bash
git add -A
git commit -m "chore: remove stray Alex reference found in final sweep"
```

---

## Self-review notes

- **Spec coverage:** DB purge (Task 1–2), recovery scrub (Task 3), bot + stats (Task 4), website wordmark/GIFs + desktop colours/PNGs + asset source (Task 5), tests/fixtures (Task 1 test, Task 6), deploy (idempotent boot migration in Task 2 wiring), final verification (Task 7). All spec sections mapped.
- **Token invalidation:** covered by deleting the `players` row (Task 1) — asserted implicitly (row gone) and by seeding `auth_token_hash` in the test.
- **Ordering:** `purgeRemovedPlayers` runs after `migrateSeason0Recovered` and `migrateTrails`, before `backfillActivity` (Task 2 Step 2), matching the spec.
