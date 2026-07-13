# Remove player "Alex" from the kart-off — design

**Date:** 2026-07-13
**Status:** Approved (pending spec review)

## Goal

Alex is no longer a participant. The kart-off has not started officially, so we erase every trace
of him: he must not appear on the website, in pbenguin, in the Discord bot, or in any Pi API
response (leaderboards, turf/territory, roster, activity, players, tracks). His auth token must be
invalidated. His race data is **deleted entirely** (chosen over "keep + hide": leaderboards and the
turf API read from `runs`, not `season_rosters`, so hiding would require a `hidden` flag plus an
exclusion filter on every current and future read query — a permanent footgun. Deletion is clean and
robust, and the season hasn't started so there is nothing worth preserving).

The database change ships as an **idempotent boot migration** that auto-applies on the Pi on the
next tagged deploy (`deploy/update.sh` → restart → migrate-on-boot). No manual DB step.

## Decision summary

- Delete Alex's **entire `players` row** and all rows referencing his `player_id`.
- Invalidate his token as a consequence of deleting the row (his pbenguin uploads/starts then 401).
- Scrub the recovery **source** file so a fresh DB never re-imports him.
- Remove every hardcoded/bundled reference across bot, website, desktop app, and asset configs.

## Surface inventory (verified)

### 1. Database (Pi) — purge migration
Tables holding Alex rows, keyed by `player_id` (found via `display_name = 'Alex' COLLATE NOCASE`):
`runs` (+ `run_laps`, `run_trails` via `ON DELETE CASCADE`), `ghost_imports`, `screen_intervals`,
`activity_events`, `player_alignment`, `season_rosters`, and finally the `players` row itself.
The retired `run_points` table (dropped by `migrateTrails` only when fully migrated, otherwise
**kept**) is also cleared for Alex's runs with a **guarded** delete before the `runs` delete, in case
prod still has it with leftover rows.
`world_records` is **not** touched — its `holder_name` is free text for external WR holders, not a
local participant FK; Alex holds none.

Deleting the `players` row also removes him from the server-side colours map
(`db/reads.ts` `standingsColors`: `SELECT display_name, color FROM players WHERE color IS NOT NULL`),
which feeds the website's territory/turf colour objects.

### 2. Recovery source
`server/data/season0_recovery.json` — a flat JSON array of run objects, each with a `"player"`
field. Remove every element where `player == "Alex"`. This is the source the one-time
`migrateSeason0Recovered` boot step and the manual `server.importer` read; scrubbing it means a
brand-new DB never re-adds him even before the purge migration runs. (The `.bak` sibling is a manual
backup and is left as-is.)

### 3. Discord bot
`pi/src/bot/players.config.ts` — remove:
- `ID_TO_NAME` entry `'201561251963207681': 'Alex'`
- `THUMBNAIL_GIFS.Alex`
- `TEMP_THUMBNAILS.Alex`
- update the `TEMP (2026-06-23)` comment that names "Gub + Alex" → "Gub".

`NAME_TO_ID` is derived from `ID_TO_NAME` (auto-updates). All bot commands (`/leaderboard`, `/wr`,
`/nemesis`) and announcements are DB-driven; the `/nemesis` player autocomplete reads
`season_rosters` (`db/lookups.ts:listPlayers`), so the purge empties it. `gifFor()` already returns
`null` for unknown names, so removing his thumbnails is graceful.

### 4. Pi stats (body/broadcast)
`pi/src/stats/body.ts` — remove `{ person: 'alex', player: 'Alex' }` from `PORKER_MAP` (line ~14).
With no player Alex, this body-measurement→player mapping is dead; removing it prevents his body
stats from being surfaced under a non-existent player.

### 5. Website (`web/`)
- `web/src/lib/wordmark.config.json` — remove the `"alex"` block. **Live**, not dormant:
  `web/src/App.svelte` builds `brandNames = Object.keys(wordmarkConfig.players)` and randomly picks
  one for the navbar wordmark each load, so this block can render "Alex" in the navbar.
- `web/public/players/alex.gif` and `web/public/players/alex__fire.gif` — delete the committed,
  generated territory/course-popup GIF assets. (Pi-served, ordinary git binaries.)

All website pages (live cards, `/players`, `/tracks`, `/turf`, activity, heat, version) enumerate
players from API responses (`/v1/roster`, `/v1/territory`, `/v1/territory/timeline`,
`/v1/players/:slug`, `/v1/activity`) or the presence WebSocket — all cleared by the purge.

### 6. pbenguin desktop app + shared `src/`
- `src/lib/trailSettings.js` — remove the `alex: "#3d7cc2"` entry from `PLAYER_COLORS` (a
  name-keyed fallback shared by app + website; dormant after purge but removed for cleanliness).
- `src/assets/players/alex__on.png`, `alex__off.png`, `alex__onpace.png` — delete the committed,
  generated card-figure assets (bundled via `src/lib/playerFigures.js` `import.meta.glob`).

Desktop player cards/presence (`PlayerPanel`, `PlayerCard`, `TrailSettings`) are IPC/presence-driven
off the server roster — cleared by the purge.

### 7. Asset source config
`assets/player_figures.json` — remove the `"alex"` block. This is the **source** config that the
`scripts/gen_player_figures.py` / `pick_player_figures.py` / `bundle_web_player_gifs.py` generators
turn into the committed assets in §5 and §6. Removing it prevents a future regeneration from
recreating Alex's figures. (No `assets/player_gifs/alex*` source files exist.)

## DB purge migration — detailed design

New file `pi/src/db/purgeRemovedPlayers.ts`, modelled on the existing `db/playerRenames.ts`
(idempotent, boot-applied, generic list for future reuse):

```ts
import type { DatabaseSync } from 'node:sqlite';

// Players removed from the kart-off entirely. Idempotent: once a player's rows are gone,
// re-running is a no-op. Deletes ALL data for the player (runs, laps, trails, activity,
// alignment, roster) and the players row itself — which also invalidates their token.
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
      // then the remaining player-referencing tables, then the players row.
      const del = (sql: string) => { try { db.prepare(sql).run(id); } catch { /* table may not exist on older DBs */ } };
      del('DELETE FROM ghost_imports    WHERE player_id = ?');   // references runs(id) w/o cascade — must precede runs
      del('DELETE FROM run_points       WHERE run_id IN (SELECT id FROM runs WHERE player_id = ?)');  // retired table, may still exist on prod
      del('DELETE FROM runs             WHERE player_id = ?');   // cascades run_laps, run_trails
      del('DELETE FROM screen_intervals WHERE player_id = ?');
      del('DELETE FROM activity_events  WHERE player_id = ?');
      del('DELETE FROM player_alignment WHERE player_id = ?');
      del('DELETE FROM season_rosters   WHERE player_id = ?');
      del('DELETE FROM players          WHERE id = ?');
      db.exec('COMMIT');
    } catch (e) {
      db.exec('ROLLBACK');
      // non-fatal: never block boot on the purge
    }
  }
}
```

**Delete-order rationale (foreign_keys = ON):**
- `ghost_imports.run_id → runs(id)` has no cascade, so Alex's `ghost_imports` rows must be deleted
  before his `runs`.
- `run_points` (retired; may still exist on prod, references `runs`) is guarded-deleted for Alex's
  runs before the `runs` delete. Absent on fresh/migrated DBs → the guard no-ops.
- `runs` deletion cascades `run_laps` and `run_trails` (both `ON DELETE CASCADE`).
- `screen_intervals`, `activity_events`, `player_alignment`, `season_rosters` all reference
  `players(id)` and must be cleared before the `players` row.
- Each statement is individually guarded so a table absent on an older DB never blocks boot.
- Wrapped in a transaction for atomicity.

**Wiring** (`pi/src/server.ts`): call `purgeRemovedPlayers(db)` **after** `migrateTrails(db)` and
**before** `backfillActivity(db)`. Ordering matters:
- Must run **after** `migrateSeason0Recovered` (which inserts Alex's recovered runs) so the purge
  removes anything recovery re-adds on a fresh DB.
- Must run **after** `migrateTrails` so Alex's trail data has already moved to `run_trails` (cleared
  by cascade) and his `run_points` rows aren't left stranded mid-migration.
- Running **before** `backfillActivity` avoids generating Alex activity rows that would then need
  re-deleting (backfill derives from `runs`, which are already gone).

## Testing

- New `pi/src/db/purgeRemovedPlayers.test.ts` (vitest): seed an in-memory/temp DB with schema, an
  Alex player + a second player, plus Alex rows in `runs`, `run_laps`, `run_trails`, `season_rosters`,
  `activity_events`, `screen_intervals`, `player_alignment`, `ghost_imports`. Assert after one run:
  all Alex rows gone across every table, the **second player's rows untouched**, and his `players`
  row deleted (token invalidated). Assert a **second run is a clean no-op**, and that running against
  a DB with no Alex is a no-op.
- Re-run `pi` (`npm test`) and `web` (`npm test`) suites after the config/fixture edits.
- Existing tests do not break: no test asserts the production configs contain Alex, and the
  `*.test.*` fixtures that use "Alex" as a placeholder seed their own data. As a **clarity-only**
  pass (not correctness), rename real-roster-style "Alex" fixtures to a neutral placeholder so the
  codebase stops implying he is a participant: `web/src/lib/turf.test.js`, `pi/src/turf/rank.test.ts`,
  `src/lib/trailSettings.test.js`, `src/lib/presence.test.js`, `pi/src/db/reads.test.ts`. Optional;
  skip any that would obscure a meaningful fixture.

## Deploy

All changes ship in one git tag:
- Boot migration runs against the live `~/mkw-data/mkw.db` on Pi restart (idempotent, safe to re-run).
- `deploy/update.sh` rebuilds the web bundle (drops the wordmark block + deleted GIFs) and restarts
  the bot (drops `players.config.ts` entries) and server.
- Deleted committed assets ship with the tag.

## Out of scope / notes

- Alex's **local** pbenguin token (on his own machine) is not reachable; server-side deletion is the
  enforcement — his app 401s on the next upload/start and no longer appears in others' rosters.
- `server/data/season0_recovery.json.bak` is left untouched (manual backup).
- `world_records` is untouched (external WR holders, not participants).
