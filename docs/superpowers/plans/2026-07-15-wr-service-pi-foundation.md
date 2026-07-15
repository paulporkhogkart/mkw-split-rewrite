# WR Service — Pi Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pi able to resolve every current WR's loadout to slugs, alert when it can't, and hand out leased WR-processing jobs whose resulting trails it stores and serves.

**Architecture:** Plan 1 of three from `docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md`. Two halves: (a) fix the current-WR reconciler so it resolves character/costume/kart slugs and alerts on unmapped names — load-bearing, because the WR service builds `set_selection` from those slugs and cannot process an unslugged WR; (b) add `wr_trails` + `wr_jobs` and the lease endpoints a worker drives. Nothing here touches the desktop engine or the client. No worker exists yet — this plan's deliverable is exercised by tests and `curl`.

**Tech Stack:** Node/TypeScript executed directly via `tsx` (no build step), Hono for HTTP, `node:sqlite` (`DatabaseSync`), vitest for colocated tests, discord.js v14 for the bot.

## Global Constraints

- **Schema DDL lives at `server/schema.sql`** (repo root, NOT in `pi/`). `pi/src/db/connect.ts:17` `db.exec`s the whole file, then applies additive `ALTER`s. **New TABLES go in `server/schema.sql` only** — `CREATE TABLE IF NOT EXISTS` covers fresh and migrated DBs alike. Only new COLUMNS on existing tables need an `ALTER` in `connect.ts`.
- **Every write takes a Bearer header only**, never `?token=` (a token in a write URL leaks into logs).
- **`npm run typecheck` is non-gating** (not in CI) but source AND tests must stay `tsc`-clean.
- **Tests are colocated** next to the module (`foo.ts` → `foo.test.ts`) and run with `npm test` (vitest) from `pi/`.
- **Slugs strip apostrophes** including curly U+2019 (`db/slug.ts`).
- **cc is always 150.** mkwrs MKWorld is a 150cc-only board; `cc` is a hardcoded constant (`scrape.ts:18`). Default it to 150 everywhere, never scrape it.
- **The trail wire format is a 4-tuple** `[t_ms, cx, cy, score]`. Storage keeps a 5th `lap` field but both existing serializers drop it (`db/reads.ts:104`). Do not diverge.
- **Do not modify `pi/src/wr/history_reconcile.ts`.** It has its own working inline slug resolution and its own tests. Task 1 extracts a shared helper and uses it on the current path only; unifying the history path is a deliberate follow-up, not this plan.
- **Never run `wipe-runs`** or any destructive CLI while implementing. All tests use `openDb(':memory:')`.

---

## Spec correction absorbed by this plan

The spec (§4) says workers get a **dedicated token** "via the existing `mint-token` CLI". Both halves of that are wrong, and this plan supersedes it:

- `mintToken` (`db/players.ts:9`) *throws* for an unknown player and `playerByToken` reads `players WHERE auth_token_hash=?`, so a dedicated worker token would need either a fake `players` row or a whole new table.
- **Decision (Paul, 2026-07-15): the WR service reuses the player token the core pbenguin app already stores.** The service ships as a second binary in the same installer and can read the same credential; the trust model already accepts client-computed trails from that token via `POST /v1/runs`, so this opens no new hole. No new table, no new CLI, no second secret to distribute.

**But a player token identifies a person, not a machine**, and the lease needs per-machine ownership (two of Paul's PCs would both be `lease_owner = 'Paul'`, letting one complete the other's job). So:

- **Auth** = the existing `requireToken(db)` player gate, Bearer-header-only, exactly as `POST /v1/runs` does (`api/runs.ts:24`).
- **Lease identity** = an `X-Worker-Id` header the service generates once per machine and persists locally.

`lease_owner` stores that worker id. A caller could spoof another machine's id, but they would need a valid player token first — and a token holder can already `POST /v1/runs` with fabricated data, so this adds nothing to the existing threat model.

Consequence: `/v1/wr-jobs/*` needs **no** app-gate bypass. It passes the global `requireTokenAny` gate and then each route re-gates header-only with `requireToken`, the same double-gate `/v1/runs` uses.

## File Structure

| File | Responsibility |
|---|---|
| `pi/src/wr/loadout.ts` **(create)** | `resolveLoadout()` — split raw `Character (Costume)` + resolve all three slugs. Pure; no DB. |
| `pi/src/wr/loadout.test.ts` **(create)** | Tests for the above. |
| `pi/src/wr/reconcile.ts` **(modify)** | Current-WR path: write slugs on insert + on raw change; flag/alert unresolved items and unmapped courses; enqueue `wr_jobs`. |
| `pi/src/wr/flags.ts` **(modify)** | `upsertFlag` returns `{isNew}`; `resolveFlags` learns the `course` category. |
| `pi/src/wr/backfillSlugs.ts` **(create)** | Re-resolve slugs on existing rows after an alias is added. |
| `pi/src/db/types.ts` **(modify)** | Add the `wr_name_flag` ServerEvent member. |
| `pi/src/bot/embeds/nameFlag.ts` **(create)** | Discord embed for an unmapped name. |
| `pi/src/bot/dispatch.ts` **(modify)** | Route `wr_name_flag` to the embed. |
| `pi/src/db/wrTrails.ts` **(create)** | `insertWrTrail` / `getWrTrail` / `courseWrTrails` over `trailCodec`. |
| `pi/src/db/wrJobs.ts` **(create)** | `enqueueJob` / `seedWrJobs` / `claimJob` / `heartbeatJob` / `releaseJob` / `completeJob` / `failJob`. |
| `pi/src/api/wrJobs.ts` **(create)** | The four `/v1/wr-jobs/*` routes (player-token gated, `X-Worker-Id` for lease identity). |
| `pi/src/api/reads.ts` **(modify)** | `GET /v1/wr-trails`. |
| `pi/src/api/app.ts` **(modify)** | Mount `wrJobsRoutes`; add `/v1/wr-trails` to `PUBLIC_READS`. |
| `server/schema.sql` **(modify)** | `wr_trails`, `wr_jobs` + index. |

---

### Task 1: Resolve loadout slugs on the current-WR path

**Files:**
- Create: `pi/src/wr/loadout.ts`
- Create: `pi/src/wr/loadout.test.ts`
- Modify: `pi/src/wr/reconcile.ts` (`backfill` at :30-40, `reconcileOne` INSERT at :79-85)
- Test: `pi/src/wr/reconcile.test.ts` (append)

**Interfaces:**
- Consumes: `splitCharacter(raw)` from `./history_parse` → `{character: string|null, costume: string|null}`; `resolveItem(category, raw)` from `./roster` → `{slug: string|null, slugGuess: string}`.
- Produces: `resolveLoadout(characterRaw, kartRaw) → Loadout` — used by Tasks 2 and 4.

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/loadout.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { resolveLoadout } from './loadout';

describe('resolveLoadout', () => {
  it('splits a costume out of the character and resolves all three', () => {
    const lo = resolveLoadout('Toadette (Conductor)', 'Mach Rocket');
    expect(lo.character).toBe('Toadette');
    expect(lo.costume).toBe('Conductor');
    expect(lo.characterSlug).toBe('toadette');
    expect(lo.costumeSlug).toBe('conductor');
    expect(lo.kartSlug).toBe('mach_rocket');
    expect(lo.unresolved).toEqual([]);
  });

  it('treats a bare character as the base costume (costume slug null, NOT unresolved)', () => {
    const lo = resolveLoadout('Bowser', 'Reel Racer');
    expect(lo.costume).toBeNull();
    expect(lo.costumeSlug).toBeNull();
    expect(lo.unresolved).toEqual([]);
  });

  it('applies the kart alias map', () => {
    expect(resolveLoadout('Swoop', 'R.O.B. H.O.G.').kartSlug).toBe('rob_hog');
    expect(resolveLoadout('Swoop', 'Tiny Titan').kartSlug).toBe('rally_romper');
  });

  it('reports unresolvable names with a slug guess and a null slug', () => {
    const lo = resolveLoadout('Zzz Nobody', 'Fake Kart');
    expect(lo.characterSlug).toBeNull();
    expect(lo.kartSlug).toBeNull();
    expect(lo.unresolved).toEqual([
      { category: 'character', raw: 'Zzz Nobody', slugGuess: 'zzz_nobody' },
      { category: 'kart', raw: 'Fake Kart', slugGuess: 'fake_kart' },
    ]);
  });

  it('handles nulls and the mkwrs empty-cell dash', () => {
    const lo = resolveLoadout(null, null);
    expect(lo).toMatchObject({ character: null, costume: null, kart: null,
      characterSlug: null, costumeSlug: null, kartSlug: null, unresolved: [] });
    expect(resolveLoadout('-', '-').characterSlug).toBeNull();
    expect(resolveLoadout('-', '-').unresolved).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/wr/loadout.test.ts`
Expected: FAIL — `Failed to resolve import "./loadout"`.

- [ ] **Step 3: Write the implementation**

Create `pi/src/wr/loadout.ts`:

```ts
import { splitCharacter } from './history_parse';
import { resolveItem, type ItemCategory } from './roster';

export type UnresolvedName = { category: ItemCategory; raw: string; slugGuess: string };

export type Loadout = {
  character: string | null;      // raw, costume stripped
  costume: string | null;        // raw; null == base costume (legitimate, not a failure)
  kart: string | null;           // raw
  characterSlug: string | null;
  costumeSlug: string | null;
  kartSlug: string | null;
  unresolved: UnresolvedName[];  // present-but-unresolvable only
};

/** Resolve a scraped `Character (Costume)` + kart into canonical slugs.
 *  A NULL costume is the base costume and is never reported as unresolved —
 *  only a name that is present AND fails to resolve lands in `unresolved`. */
export function resolveLoadout(characterRaw: string | null, kartRaw: string | null): Loadout {
  const { character, costume } = splitCharacter(characterRaw ?? '');
  const kartTrim = (kartRaw ?? '').trim();
  const kart = !kartTrim || kartTrim === '-' ? null : kartTrim;

  const unresolved: UnresolvedName[] = [];
  const resolve = (category: ItemCategory, raw: string | null): string | null => {
    if (!raw) return null;
    const { slug, slugGuess } = resolveItem(category, raw);
    if (!slug) unresolved.push({ category, raw, slugGuess });
    return slug;
  };

  return {
    character, costume, kart,
    characterSlug: resolve('character', character),
    costumeSlug: resolve('costume', costume),
    kartSlug: resolve('kart', kart),
    unresolved,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/wr/loadout.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Write the failing reconcile test**

Append to `pi/src/wr/reconcile.test.ts` (inside the existing `describe('reconcile', ...)`):

```ts
  it('writes loadout slugs on a fresh insert', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ character: 'Toadette (Conductor)', vehicle: 'Mach Rocket' })]);
    const row = db.prepare(
      'SELECT character, character_slug, costume_slug, kart_slug FROM world_records WHERE is_current=1'
    ).get() as any;
    expect(row.character).toBe('Toadette (Conductor)');   // raw is still stored
    expect(row.character_slug).toBe('toadette');
    expect(row.costume_slug).toBe('conductor');
    expect(row.kart_slug).toBe('mach_rocket');
  });

  it('re-resolves slugs when the raw value changes on the current row', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ character: 'Toadette (Conductor)', vehicle: 'Mach Rocket' })]);
    // same record + holder -> Case 1 backfill path, with a corrected loadout
    reconcile(db, hub, [wr({ character: 'Bowser (Biker)', vehicle: 'Reel Racer' })]);
    const row = db.prepare(
      'SELECT character, character_slug, costume_slug, kart_slug FROM world_records WHERE is_current=1'
    ).get() as any;
    expect(row.character).toBe('Bowser (Biker)');
    expect(row.character_slug).toBe('bowser');
    expect(row.costume_slug).toBe('biker');
    expect(row.kart_slug).toBe('reel_racer');
  });

  it('clears the costume slug when the raw drops back to a base costume', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ character: 'Toadette (Conductor)' })]);
    reconcile(db, hub, [wr({ character: 'Toadette' })]);
    const row = db.prepare('SELECT costume_slug FROM world_records WHERE is_current=1').get() as any;
    expect(row.costume_slug).toBeNull();
  });
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd pi && npx vitest run src/wr/reconcile.test.ts`
Expected: FAIL — `expected null to be 'toadette'` (the INSERT has no slug columns yet).

- [ ] **Step 7: Wire slugs into reconcile.ts**

In `pi/src/wr/reconcile.ts`, add the import below the existing `./courses` import:

```ts
import { resolveLoadout } from './loadout';
```

Replace `backfill` (currently lines 30-40) entirely:

```ts
/** Update video/character/vehicle (and holder if currently null) on `row` from the
 *  scrape, only where the scraped value is non-empty and differs. A changed raw
 *  character/vehicle also re-resolves its slugs, so an mkwrs correction propagates.
 *  Returns true if it wrote. */
function backfill(db: DatabaseSync, row: Row, s: ScrapedWr): boolean {
  const sets: string[] = [];
  const vals: (string | null)[] = [];
  if (row.holder_name == null && s.holder) { sets.push('holder_name=?'); vals.push(s.holder); }
  if (s.videoUrl && s.videoUrl !== row.video_url) { sets.push('video_url=?'); vals.push(s.videoUrl); }
  if (s.character && s.character !== row.character) {
    const lo = resolveLoadout(s.character, null);
    sets.push('character=?', 'character_slug=?', 'costume_slug=?');
    vals.push(s.character, lo.characterSlug, lo.costumeSlug);
  }
  if (s.vehicle && s.vehicle !== row.vehicle) {
    const lo = resolveLoadout(null, s.vehicle);
    sets.push('vehicle=?', 'kart_slug=?');
    vals.push(s.vehicle, lo.kartSlug);
  }
  if (sets.length === 0) return false;
  db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, row.id);
  return true;
}
```

Replace the INSERT in `reconcileOne` (currently lines 79-85):

```ts
    } else {
      const lo = resolveLoadout(s.character, s.vehicle);
      db.prepare(
        `INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str,
           achieved_at, video_url, character, vehicle,
           character_slug, costume_slug, kart_slug, provenance, is_current)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'scraped', 1)`
      ).run(courseId, cc, s.holder, s.recordMs, s.recordStr,
            isoDate(s.date), s.videoUrl, s.character, s.vehicle,
            lo.characterSlug, lo.costumeSlug, lo.kartSlug);
      report.inserted++;
    }
```

- [ ] **Step 8: Run to verify it passes**

Run: `cd pi && npx vitest run src/wr/`
Expected: PASS — all pre-existing wr tests plus the 3 new ones.

- [ ] **Step 9: Typecheck and commit**

```bash
cd pi && npx tsc --noEmit
git add pi/src/wr/loadout.ts pi/src/wr/loadout.test.ts pi/src/wr/reconcile.ts pi/src/wr/reconcile.test.ts
git commit -m "feat(wr): resolve loadout slugs on the current-WR scrape path"
```

---

### Task 2: Flag and alert unresolvable item names

**Files:**
- Modify: `pi/src/wr/flags.ts` (`upsertFlag` at :14-23)
- Modify: `pi/src/db/types.ts` (`ServerEvent` union at :15-24)
- Modify: `pi/src/wr/reconcile.ts`
- Create: `pi/src/bot/embeds/nameFlag.ts`
- Modify: `pi/src/bot/dispatch.ts`
- Test: `pi/src/wr/flags.test.ts` (create if absent), `pi/src/wr/reconcile.test.ts` (append)

**Interfaces:**
- Consumes: `resolveLoadout` (Task 1); `EventHub.publish(e: ServerEvent)` (`api/events.ts:8`).
- Produces: `upsertFlag(db, f) → {isNew: boolean}` (was `void`; adding a return is backward-compatible with the existing `history_reconcile.ts:82` callers, which ignore it). New `ServerEvent` member `wr_name_flag`.

- [ ] **Step 1: Write the failing test for upsertFlag's return**

Create `pi/src/wr/flags.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { upsertFlag, resolveFlags } from './flags';

function db() { const d = openDb(':memory:'); applySchema(d); return d; }

describe('upsertFlag', () => {
  it('reports isNew only on the first sighting', () => {
    const d = db();
    expect(upsertFlag(d, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).isNew).toBe(true);
    expect(upsertFlag(d, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).isNew).toBe(false);
    expect(upsertFlag(d, { category: 'kart', rawValue: 'Tiny Titan', slugGuess: 'tiny_titan' }).isNew).toBe(false);
    const row = d.prepare('SELECT occurrences FROM wr_name_flags WHERE raw_value=?').get('Tiny Titan') as any;
    expect(row.occurrences).toBe(3);
  });

  it('keeps distinct raw values separate', () => {
    const d = db();
    expect(upsertFlag(d, { category: 'kart', rawValue: 'A', slugGuess: 'a' }).isNew).toBe(true);
    expect(upsertFlag(d, { category: 'kart', rawValue: 'B', slugGuess: 'b' }).isNew).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/wr/flags.test.ts`
Expected: FAIL — `Cannot read properties of undefined (reading 'isNew')`.

- [ ] **Step 3: Make upsertFlag return isNew**

In `pi/src/wr/flags.ts`, replace `upsertFlag` (lines 14-23):

```ts
/** Record an unresolved name. Idempotent on (category, raw_value): increments occurrences and
 *  clears any stale resolved_at (it is unresolved again right now).
 *  Returns `{isNew: true}` only on the very first sighting — callers alert on that alone, or a
 *  15-minute scraper would re-announce the same broken name forever. Trade-off: a name that was
 *  resolved and later breaks again does NOT re-alert; `npm run wr-flags` still lists it. */
export function upsertFlag(db: DatabaseSync, f: FlagInput): { isNew: boolean } {
  const row = db.prepare(
    `INSERT INTO wr_name_flags(category, raw_value, slug_guess, example_course_id, example_wr_id, occurrences)
     VALUES (?,?,?,?,?,1)
     ON CONFLICT(category, raw_value) DO UPDATE SET
       occurrences = occurrences + 1,
       slug_guess = excluded.slug_guess,
       resolved_at = NULL
     RETURNING occurrences`
  ).get(f.category, f.rawValue, f.slugGuess ?? null, f.exampleCourseId ?? null, f.exampleWrId ?? null) as { occurrences: number };
  return { isNew: row.occurrences === 1 };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/wr/flags.test.ts`
Expected: PASS, 2 tests. (If `RETURNING` errors, the SQLite build predates 3.35 — fall back to a `SELECT occurrences` immediately after the upsert, inside the same statement pair.)

- [ ] **Step 5: Add the event type**

In `pi/src/db/types.ts`, append to the `ServerEvent` union (after the `wr_update` member at :21-24):

```ts
  | { type: 'wr_name_flag'; category: 'character' | 'kart' | 'costume' | 'course';
      raw_value: string; slug_guess: string | null; course: string | null };
```

- [ ] **Step 6: Write the failing reconcile-alert test**

Append to `pi/src/wr/reconcile.test.ts`:

```ts
  it('flags + announces an unresolvable kart once, on first sighting only', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Fake Kart' })]);
    const flags = () => events.filter((e) => e.type === 'wr_name_flag');
    expect(flags()).toHaveLength(1);
    expect(flags()[0]).toMatchObject({
      type: 'wr_name_flag', category: 'kart', raw_value: 'Fake Kart',
      slug_guess: 'fake_kart', course: 'Rainbow Road',
    });
    // second scrape of the same broken name -> counted, not re-announced
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Fake Kart' })]);
    expect(flags()).toHaveLength(1);
    const row = db.prepare('SELECT occurrences FROM wr_name_flags WHERE raw_value=?').get('Fake Kart') as any;
    expect(row.occurrences).toBe(2);
  });

  it('does not flag a base costume', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Reel Racer' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(0);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_name_flags').get()).toMatchObject({ n: 0 });
  });
```

- [ ] **Step 7: Run to verify it fails**

Run: `cd pi && npx vitest run src/wr/reconcile.test.ts`
Expected: FAIL — `expected [] to have a length of 1`.

- [ ] **Step 8: Emit flags from reconcile**

In `pi/src/wr/reconcile.ts`, add imports:

```ts
import { upsertFlag } from './flags';
import type { Loadout } from './loadout';
```

Add this helper above `reconcileOne`:

```ts
/** Record + announce any name in `lo` that failed to resolve. Announce only on first sighting;
 *  an unresolved name blocks WR processing for that record, so it needs a human. */
function flagUnresolved(db: DatabaseSync, hub: EventHub, lo: Loadout,
                        courseName: string, courseId: number, wrId: number | null): void {
  for (const u of lo.unresolved) {
    const { isNew } = upsertFlag(db, {
      category: u.category, rawValue: u.raw, slugGuess: u.slugGuess,
      exampleCourseId: courseId, exampleWrId: wrId ?? undefined,
    });
    if (isNew) hub.publish({ type: 'wr_name_flag', category: u.category,
      raw_value: u.raw, slug_guess: u.slugGuess, course: courseName });
  }
}
```

In `reconcileOne`, the Case 1 early-return block (currently :60-63) becomes:

```ts
  // Case 1: same record as current -> backfill metadata in place, no current move.
  if (cur && cur.record_ms === s.recordMs && cur.holder_name === s.holder) {
    if (backfill(db, cur, s)) report.backfilled++; else report.unchanged++;
    flagUnresolved(db, hub, resolveLoadout(s.character, s.vehicle), s.courseName, courseId, cur.id);
    return;
  }
```

And in the Case 2 INSERT branch from Task 1, capture the row id and flag after the transaction commits. Replace the `} else {` branch body so it records the id, and add the flag call after `db.exec('COMMIT')`:

```ts
    } else {
      const lo = resolveLoadout(s.character, s.vehicle);
      const res = db.prepare(
        `INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str,
           achieved_at, video_url, character, vehicle,
           character_slug, costume_slug, kart_slug, provenance, is_current)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'scraped', 1)`
      ).run(courseId, cc, s.holder, s.recordMs, s.recordStr,
            isoDate(s.date), s.videoUrl, s.character, s.vehicle,
            lo.characterSlug, lo.costumeSlug, lo.kartSlug);
      insertedWrId = Number(res.lastInsertRowid);
      report.inserted++;
    }
    db.exec('COMMIT');
  } catch (e) { db.exec('ROLLBACK'); throw e; }

  flagUnresolved(db, hub, resolveLoadout(s.character, s.vehicle), s.courseName, courseId,
                 insertedWrId ?? reflaggedWrId);
```

Declare both ids before `db.exec('BEGIN')` in `reconcileOne`:

```ts
  let insertedWrId: number | null = null;
  let reflaggedWrId: number | null = null;
```

and set `reflaggedWrId = existing.id;` inside the `if (existing) {` branch.

- [ ] **Step 9: Run to verify it passes**

Run: `cd pi && npx vitest run src/wr/`
Expected: PASS — all wr tests.

- [ ] **Step 10: Add the Discord embed**

Create `pi/src/bot/embeds/nameFlag.ts`:

```ts
import { EmbedBuilder } from 'discord.js';

export type NameFlagData = {
  category: string; raw_value: string; slug_guess: string | null; course: string | null;
};

/** Amber alert: mkwrs used a name we cannot map to a canonical slug. WR trail processing
 *  for that record is blocked until someone maps it, so this needs a human. */
export function buildNameFlagEmbed(d: NameFlagData): EmbedBuilder {
  const e = new EmbedBuilder()
    .setColor(0xf59e0b)
    .setTitle('UNMAPPED mkwrs NAME')
    .setDescription(`A **${d.category}** name from mkwrs did not resolve to a known slug. WR processing for that record is blocked until it is mapped.`)
    .addFields(
      { name: 'Raw value', value: `\`${d.raw_value}\``, inline: true },
      { name: 'Slug guess', value: d.slug_guess ? `\`${d.slug_guess}\`` : '—', inline: true },
    );
  if (d.course) e.addFields({ name: 'Seen on', value: d.course, inline: true });
  return e.setFooter({ text: d.category === 'course'
    ? 'Map it in wr/courses.ts (MKWRS_ALIASES), then: npm run wr-flags'
    : 'Map it in wr/roster.ts (canonical set or alias map), then: npm run wr-flags' });
}
```

- [ ] **Step 11: Dispatch it**

In `pi/src/bot/dispatch.ts`, add the import:

```ts
import { buildNameFlagEmbed } from './embeds/nameFlag';
```

and a branch after the `wr_update` branch (currently :17-19):

```ts
    } else if (ev.type === 'wr_name_flag') {
      send(buildNameFlagEmbed(ev));
    }
```

- [ ] **Step 12: Test the dispatch**

Append to `pi/src/bot/dispatch.test.ts`:

```ts
  it('announces an unmapped mkwrs name', () => {
    const db = openDb(':memory:'); applySchema(db);
    const sent: any[] = [];
    dispatch(db, { type: 'wr_name_flag', category: 'kart', raw_value: 'Tiny Titan',
                   slug_guess: 'tiny_titan', course: 'Rainbow Road' }, (e) => sent.push(e));
    expect(sent).toHaveLength(1);
    expect(sent[0].data.title).toBe('UNMAPPED mkwrs NAME');
  });
```

Run: `cd pi && npx vitest run src/bot/dispatch.test.ts`
Expected: PASS. (If `dispatch.test.ts` lacks `openDb`/`applySchema` imports, add `import { openDb, applySchema } from '../db/connect';`.)

- [ ] **Step 13: Typecheck and commit**

```bash
cd pi && npx tsc --noEmit && npx vitest run
git add pi/src/wr/flags.ts pi/src/wr/flags.test.ts pi/src/wr/reconcile.ts pi/src/wr/reconcile.test.ts pi/src/db/types.ts pi/src/bot/embeds/nameFlag.ts pi/src/bot/dispatch.ts pi/src/bot/dispatch.test.ts
git commit -m "feat(wr): flag + Discord-alert unresolvable mkwrs item names"
```

---

### Task 3: Flag and alert unmapped courses

**Files:**
- Modify: `pi/src/wr/reconcile.ts` (`reconcile` loop at :44-49)
- Modify: `pi/src/wr/flags.ts` (`resolveFlags` at :27-40)
- Test: `pi/src/wr/reconcile.test.ts`, `pi/src/wr/flags.test.ts`

**Interfaces:**
- Consumes: `mkwrsNameToSlug(name)` from `./courses` (`courses.ts:9`); `upsertFlag → {isNew}` (Task 2).
- Produces: nothing new — reuses the `wr_name_flag` event with `category: 'course'`.

**Why:** nothing writes a `course` flag today and `resolveFlags` explicitly `continue`s past the category (`flags.ts:33`). An unmapped course only reaches `WrReport.unmapped`, which is console-logged and lost. If mkwrs renames a track, reconciliation silently stops for it and nobody is told.

- [ ] **Step 1: Write the failing tests**

Append to `pi/src/wr/reconcile.test.ts`:

```ts
  it('flags + announces an unmapped course, once', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ courseName: 'Brand New Track' })]);
    const flags = events.filter((e) => e.type === 'wr_name_flag');
    expect(flags).toHaveLength(1);
    expect(flags[0]).toMatchObject({ category: 'course', raw_value: 'Brand New Track',
                                     slug_guess: 'brand_new_track' });
    reconcile(db, hub, [wr({ courseName: 'Brand New Track' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(1);
  });

  it('does not flag a glitch category (legitimately skipped, not broken)', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ courseName: 'Rainbow Road (glitch)' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(0);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_name_flags').get()).toMatchObject({ n: 0 });
  });
```

Append to `pi/src/wr/flags.test.ts`:

```ts
describe('resolveFlags', () => {
  it('resolves a course flag once the course exists', () => {
    const d = db();
    upsertFlag(d, { category: 'course', rawValue: 'Wario Shipyard', slugGuess: 'wario_shipyard' });
    expect(resolveFlags(d)).toBe(0);                        // no such course yet
    d.exec("INSERT INTO courses(id,slug,display_name) VALUES (9,'warios_galleon','Warios Galleon')");
    expect(resolveFlags(d)).toBe(1);                        // MKWRS_ALIASES maps it
    const row = d.prepare('SELECT resolved_at FROM wr_name_flags WHERE raw_value=?').get('Wario Shipyard') as any;
    expect(row.resolved_at).not.toBeNull();
  });

  it('still resolves item flags', () => {
    const d = db();
    upsertFlag(d, { category: 'kart', rawValue: 'Mach Rocket', slugGuess: 'mach_rocket' });
    expect(resolveFlags(d)).toBe(1);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd pi && npx vitest run src/wr/flags.test.ts src/wr/reconcile.test.ts`
Expected: FAIL — `expected [] to have a length of 1`, and `expected 0 to be 1` for the course flag.

- [ ] **Step 3: Flag unmapped courses in reconcile**

In `pi/src/wr/reconcile.ts`, change the import from `./courses`:

```ts
import { resolveCourseId, mkwrsNameToSlug } from './courses';
```

Replace the `reconcile` loop body (currently :44-49):

```ts
  for (const s of scraped) {
    const courseId = resolveCourseId(db, s.courseName);
    if (courseId === null) {
      report.unmapped.push(s.courseName);
      // A (glitch) category resolves to null by design and is not a mapping failure.
      if (!/\(glitch\)/i.test(s.courseName)) {
        const slugGuess = mkwrsNameToSlug(s.courseName);
        const { isNew } = upsertFlag(db, { category: 'course', rawValue: s.courseName, slugGuess });
        if (isNew) hub.publish({ type: 'wr_name_flag', category: 'course',
          raw_value: s.courseName, slug_guess: slugGuess, course: null });
      }
      continue;
    }
    try { reconcileOne(db, hub, s, courseId, cc, report, activity); }
    catch (e) { console.error(`[wr] reconcile failed for ${s.courseName}:`, e); }
  }
```

- [ ] **Step 4: Teach resolveFlags the course category**

In `pi/src/wr/flags.ts`, add the import:

```ts
import { mkwrsNameToSlug } from './courses';
```

Replace the `resolveFlags` loop body (currently :32-39):

```ts
  let n = 0;
  for (const r of rows) {
    const resolved = r.category === 'course'
      ? db.prepare('SELECT 1 FROM courses WHERE slug=?').get(mkwrsNameToSlug(r.raw_value)) != null
      : resolveItem(r.category as ItemCategory, r.raw_value).slug !== null;
    if (resolved) {
      db.prepare(`UPDATE wr_name_flags SET resolved_at = datetime('now') WHERE id=?`).run(r.id);
      n++;
    }
  }
  return n;
```

Update the doc comment above `resolveFlags` — it currently says "(non-course categories)", which is now false:

```ts
/** Re-check every unresolved flag against the current roster/aliases (items) or the courses
 *  table (courses) and stamp resolved_at on any that now resolve. Returns the count resolved. */
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd pi && npx vitest run src/wr/`
Expected: PASS — all wr tests.

- [ ] **Step 6: Typecheck and commit**

```bash
cd pi && npx tsc --noEmit
git add pi/src/wr/reconcile.ts pi/src/wr/flags.ts pi/src/wr/reconcile.test.ts pi/src/wr/flags.test.ts
git commit -m "feat(wr): flag + alert unmapped mkwrs course names"
```

---

### Task 4: Backfill slugs after an alias is added

**Files:**
- Create: `pi/src/wr/backfillSlugs.ts`
- Create: `pi/src/wr/backfillSlugs.test.ts`
- Modify: `pi/src/scripts/wrFlags.ts`

**Interfaces:**
- Consumes: `resolveLoadout` (Task 1).
- Produces: `backfillSlugs(db) → number` (rows updated).

**Why:** `resolveFlags` only stamps `resolved_at`; it does not rewrite the slug columns on rows already inserted. Without this, adding an alias to `roster.ts` clears the flag but leaves `character_slug` NULL forever — and Task 8's claim predicate requires `character_slug IS NOT NULL`, so those WRs would never become processable. This closes the `wr-flags` workflow loop.

- [ ] **Step 1: Write the failing test**

Create `pi/src/wr/backfillSlugs.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { backfillSlugs } from './backfillSlugs';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  return db;
}

const insertWr = (db: any, character: string | null, vehicle: string | null) =>
  db.prepare(`INSERT INTO world_records(course_id, cc, record_ms, record_str, character, vehicle, is_current)
              VALUES (1,150,100000,'1:40.000',?,?,1)`).run(character, vehicle);

describe('backfillSlugs', () => {
  it('fills slugs that are null but now resolvable', () => {
    const db = setup();
    insertWr(db, 'Toadette (Conductor)', 'Mach Rocket');
    expect(backfillSlugs(db)).toBe(1);
    const row = db.prepare('SELECT character_slug, costume_slug, kart_slug FROM world_records').get() as any;
    expect(row).toMatchObject({ character_slug: 'toadette', costume_slug: 'conductor', kart_slug: 'mach_rocket' });
  });

  it('is idempotent — a second run writes nothing', () => {
    const db = setup();
    insertWr(db, 'Bowser', 'Reel Racer');
    expect(backfillSlugs(db)).toBe(1);
    expect(backfillSlugs(db)).toBe(0);
  });

  it('leaves genuinely unresolvable rows alone', () => {
    const db = setup();
    insertWr(db, 'Zzz Nobody', 'Fake Kart');
    expect(backfillSlugs(db)).toBe(0);
    const row = db.prepare('SELECT character_slug FROM world_records').get() as any;
    expect(row.character_slug).toBeNull();
  });

  it('does not touch a base costume (null costume_slug is correct, not missing)', () => {
    const db = setup();
    insertWr(db, 'Bowser', 'Reel Racer');
    backfillSlugs(db);
    expect(backfillSlugs(db)).toBe(0);
    const row = db.prepare('SELECT costume_slug FROM world_records').get() as any;
    expect(row.costume_slug).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/wr/backfillSlugs.test.ts`
Expected: FAIL — `Failed to resolve import "./backfillSlugs"`.

- [ ] **Step 3: Implement**

Create `pi/src/wr/backfillSlugs.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import { resolveLoadout } from './loadout';

type Row = {
  id: number; character: string | null; vehicle: string | null;
  character_slug: string | null; costume_slug: string | null; kart_slug: string | null;
};

/** Re-resolve slugs on rows whose raw names are present but whose slugs are unset. Run after
 *  adding an alias to roster.ts / courses.ts: resolveFlags only clears the flag, it does not
 *  rewrite the slug columns, and an unslugged WR can never be claimed for processing.
 *  Idempotent — returns the number of rows actually updated. */
export function backfillSlugs(db: DatabaseSync): number {
  // A NULL costume_slug is ambiguous (base costume vs unresolved), so candidacy keys off the
  // raw columns; the per-row diff below decides whether anything actually changes.
  const rows = db.prepare(
    `SELECT id, character, vehicle, character_slug, costume_slug, kart_slug
     FROM world_records
     WHERE (character IS NOT NULL OR vehicle IS NOT NULL)`
  ).all() as Row[];

  let n = 0;
  for (const r of rows) {
    const lo = resolveLoadout(r.character, r.vehicle);
    const sets: string[] = [];
    const vals: (string | null)[] = [];
    if (lo.characterSlug !== null && lo.characterSlug !== r.character_slug) { sets.push('character_slug=?'); vals.push(lo.characterSlug); }
    if (lo.costumeSlug !== null && lo.costumeSlug !== r.costume_slug) { sets.push('costume_slug=?'); vals.push(lo.costumeSlug); }
    if (lo.kartSlug !== null && lo.kartSlug !== r.kart_slug) { sets.push('kart_slug=?'); vals.push(lo.kartSlug); }
    if (sets.length === 0) continue;
    db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, r.id);
    n++;
  }
  return n;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/wr/backfillSlugs.test.ts`
Expected: PASS, 4 tests.

- [ ] **Step 5: Wire into the wr-flags CLI**

Replace `pi/src/scripts/wrFlags.ts` entirely:

```ts
import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';
import { backfillSlugs } from '../wr/backfillSlugs';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const resolved = resolveFlags(db);
const filled = backfillSlugs(db);
if (resolved) console.log(`resolved ${resolved} flag(s)`);
if (filled) console.log(`backfilled slugs on ${filled} world_records row(s)`);
console.log(reportFlags(db));
```

- [ ] **Step 6: Verify the CLI runs**

Run: `cd pi && MKW_DB=":memory:" npx tsx src/scripts/wrFlags.ts`
Expected: prints `No unresolved name flags.` and exits 0.

- [ ] **Step 7: Commit**

```bash
cd pi && npx tsc --noEmit
git add pi/src/wr/backfillSlugs.ts pi/src/wr/backfillSlugs.test.ts pi/src/scripts/wrFlags.ts
git commit -m "feat(wr): backfill world_records slugs when wr-flags runs"
```

---

### Task 5: wr_trails and wr_jobs schema + trail accessors

**Files:**
- Modify: `server/schema.sql`
- Create: `pi/src/db/wrTrails.ts`
- Create: `pi/src/db/wrTrails.test.ts`

**Interfaces:**
- Consumes: `encodeTrail(pts)`, `decodeTrail(data)`, `CODEC_BROTLI_V1`, `type TrailPoint` from `./trailCodec` (`trailCodec.ts:12,108,116`).
- Produces: `insertWrTrail(db, wrId, pts) → void`; `getWrTrail(db, wrId) → TrailPoint[]`; `courseWrTrails(db, courseId, cc) → WrTrailRow[]`. Used by Tasks 6, 8 and 9.

- [ ] **Step 1: Add the tables**

In `server/schema.sql`, after the `wr_meta` table definition, add:

```sql
-- A world record's minimap trail. Keyed on world_records.id (NOT runs/players) so a WR set by
-- a stranger can never leak into leaderboards, turf, activity or the roster. Mirrors run_trails
-- exactly so db/trailCodec.ts is reused verbatim.
CREATE TABLE IF NOT EXISTS wr_trails (
    wr_id    INTEGER PRIMARY KEY REFERENCES world_records(id) ON DELETE CASCADE,
    codec    INTEGER NOT NULL,
    n        INTEGER NOT NULL,
    max_t_ms INTEGER NOT NULL,
    data     BLOB NOT NULL
);

-- Lease + failure bookkeeping for WR trail extraction. There is deliberately no status column:
-- a wr_trails row IS "done". A row is enqueued when a WR becomes current; supersession lowers
-- its claim priority but never removes it, so history accumulates for free.
CREATE TABLE IF NOT EXISTS wr_jobs (
    wr_id       INTEGER PRIMARY KEY REFERENCES world_records(id) ON DELETE CASCADE,
    lease_owner TEXT,
    lease_until TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_wr_jobs_claim ON wr_jobs(lease_until);
```

- [ ] **Step 2: Write the failing test**

Create `pi/src/db/wrTrails.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { insertWrTrail, getWrTrail, courseWrTrails } from './wrTrails';
import type { TrailPoint } from './trailCodec';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  return db;
}

const addWr = (db: any, id: number, ms: number, holder: string, isCurrent = 0) =>
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str, achieved_at, is_current)
              VALUES (?,1,150,?,?,?, '2026-04-06T00:00:00.000Z', ?)`)
    .run(id, holder, ms, '1:02.934', isCurrent);

const pts: TrailPoint[] = [
  { t_ms: 14, cx: 1635, cy: 875, score: 0.79, lap: 1 },
  { t_ms: 114, cx: 1636, cy: 870, score: 0.81, lap: 1 },
  { t_ms: 214, cx: 1640, cy: 860, score: 0.99, lap: 2 },
];

describe('wr trails', () => {
  it('round-trips a trail bit-exactly through the brotli codec', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    expect(getWrTrail(db, 10)).toEqual(pts);
  });

  it('stores n and max_t_ms as SQL-visible columns', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    expect(db.prepare('SELECT codec, n, max_t_ms FROM wr_trails WHERE wr_id=10').get())
      .toMatchObject({ codec: 1, n: 3, max_t_ms: 214 });
  });

  it('returns [] for a WR with no trail', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    expect(getWrTrail(db, 10)).toEqual([]);
  });

  it('replaces an existing trail rather than throwing', () => {
    const db = setup(); addWr(db, 10, 62934, 'JaK', 1);
    insertWrTrail(db, 10, pts);
    insertWrTrail(db, 10, pts.slice(0, 2));
    expect(getWrTrail(db, 10)).toHaveLength(2);
  });

  it('lists a course\'s trails fastest-first as 4-tuples, current flagged', () => {
    const db = setup();
    addWr(db, 10, 62934, 'JaK', 1);
    addWr(db, 11, 62978, 'LaRochelle', 0);
    insertWrTrail(db, 11, pts);
    insertWrTrail(db, 10, pts);
    const rows = courseWrTrails(db, 1, 150);
    expect(rows.map((r) => r.wr_id)).toEqual([10, 11]);        // record_ms ASC
    expect(rows[0].is_current).toBe(1);
    expect(rows[0].points[0]).toEqual([14, 1635, 875, 0.79]);  // lap dropped on the wire
  });

  it('omits trail-less and soft-removed WRs', () => {
    const db = setup();
    addWr(db, 10, 62934, 'JaK', 1);
    addWr(db, 11, 62978, 'Ghost', 0);
    insertWrTrail(db, 10, pts);
    insertWrTrail(db, 11, pts);
    db.prepare("UPDATE world_records SET removed_at = datetime('now') WHERE id=11").run();
    expect(courseWrTrails(db, 1, 150).map((r) => r.wr_id)).toEqual([10]);
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/wrTrails.test.ts`
Expected: FAIL — `Failed to resolve import "./wrTrails"`.

- [ ] **Step 4: Implement**

Create `pi/src/db/wrTrails.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';
import { CODEC_BROTLI_V1, decodeTrail, encodeTrail, type TrailPoint } from './trailCodec';

/** Wire shape for GET /v1/wr-trails. `points` are 4-tuples [t_ms, cx, cy, score] — the stored
 *  5th `lap` field is dropped, matching the existing run-trail serializers (db/reads.ts:104). */
export type WrTrailRow = {
  wr_id: number; holder_name: string | null; record_ms: number; record_str: string;
  achieved_at: string | null; is_current: number; video_url: string | null;
  points: [number, number, number, number][];
};

/** Encode + store a WR's trail, replacing any existing one (a re-processed WR overwrites).
 *  Throws on an empty trail (packTrail); the caller decides drop-vs-fail policy. */
export function insertWrTrail(db: DatabaseSync, wrId: number, pts: TrailPoint[]): void {
  const data = encodeTrail(pts);
  db.prepare('INSERT OR REPLACE INTO wr_trails(wr_id, codec, n, max_t_ms, data) VALUES (?,?,?,?,?)')
    .run(wrId, CODEC_BROTLI_V1, pts.length, pts[pts.length - 1].t_ms, data);
}

/** A WR's full trail in t order, or [] when it has none. */
export function getWrTrail(db: DatabaseSync, wrId: number): TrailPoint[] {
  const row = db.prepare('SELECT codec, data FROM wr_trails WHERE wr_id=?').get(wrId) as
    { codec: number; data: Uint8Array } | undefined;
  if (!row) return [];
  if (row.codec !== CODEC_BROTLI_V1) throw new Error(`unknown trail codec ${row.codec} for wr ${wrId}`);
  return decodeTrail(row.data);
}

/** Every trailed WR for a course, fastest first. Soft-removed WRs are excluded. */
export function courseWrTrails(db: DatabaseSync, courseId: number, cc: number): WrTrailRow[] {
  const rows = db.prepare(
    `SELECT w.id AS wr_id, w.holder_name, w.record_ms, w.record_str,
            w.achieved_at, w.is_current, w.video_url
     FROM world_records w JOIN wr_trails t ON t.wr_id = w.id
     WHERE w.course_id=? AND w.cc=? AND w.removed_at IS NULL
     ORDER BY w.record_ms ASC, w.id ASC`
  ).all(courseId, cc) as Omit<WrTrailRow, 'points'>[];
  return rows.map((r) => ({
    ...r,
    points: getWrTrail(db, r.wr_id).map((p) => [p.t_ms, p.cx, p.cy, p.score] as [number, number, number, number]),
  }));
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/wrTrails.test.ts`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
cd pi && npx tsc --noEmit
git add server/schema.sql pi/src/db/wrTrails.ts pi/src/db/wrTrails.test.ts
git commit -m "feat(wr): wr_trails + wr_jobs schema and trail accessors"
```

---

### Task 6: Enqueue jobs on reconcile and seed on boot

**Files:**
- Create: `pi/src/db/wrJobs.ts`
- Create: `pi/src/db/wrJobs.test.ts`
- Modify: `pi/src/wr/reconcile.ts`
- Modify: `pi/src/db/connect.ts` (`applySchema`)

**Interfaces:**
- Produces: `enqueueJob(db, wrId) → void`; `seedWrJobs(db) → number`. Tasks 7-8 add the lease functions to this same file.

- [ ] **Step 1: Write the failing test**

Create `pi/src/db/wrJobs.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { enqueueJob, seedWrJobs } from './wrJobs';
import { insertWrTrail } from './wrTrails';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  return db;
}

const addWr = (db: any, id: number, opts: { current?: number; video?: string | null } = {}) =>
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                achieved_at, video_url, character_slug, is_current)
              VALUES (?,1,150,'JaK',62934,'1:02.934','2026-04-06T00:00:00.000Z',?, 'toadette', ?)`)
    .run(id, opts.video === undefined ? 'https://youtu.be/x' : opts.video, opts.current ?? 1);

describe('enqueueJob', () => {
  it('inserts once and is idempotent', () => {
    const db = setup(); addWr(db, 10);
    enqueueJob(db, 10);
    enqueueJob(db, 10);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_jobs').get()).toMatchObject({ n: 1 });
  });

  it('does not reset attempts on a repeat enqueue', () => {
    const db = setup(); addWr(db, 10);
    enqueueJob(db, 10);
    db.prepare('UPDATE wr_jobs SET attempts=3 WHERE wr_id=10').run();
    enqueueJob(db, 10);
    expect(db.prepare('SELECT attempts FROM wr_jobs WHERE wr_id=10').get()).toMatchObject({ attempts: 3 });
  });
});

describe('seedWrJobs', () => {
  it('seeds current WRs that have a video and no trail', () => {
    const db = setup(); addWr(db, 10);
    expect(seedWrJobs(db)).toBe(1);
    expect(seedWrJobs(db)).toBe(0);            // idempotent
  });

  it('skips WRs with no video, non-current WRs, and already-trailed WRs', () => {
    const db = setup();
    addWr(db, 10, { video: null });            // no video
    addWr(db, 11, { current: 0 });             // not current
    addWr(db, 12);                             // trailed below
    insertWrTrail(db, 12, [{ t_ms: 1, cx: 1, cy: 1, score: 0.9, lap: 1 }]);
    expect(seedWrJobs(db)).toBe(0);
  });

  it('skips soft-removed WRs', () => {
    const db = setup(); addWr(db, 10);
    db.prepare("UPDATE world_records SET removed_at = datetime('now') WHERE id=10").run();
    expect(seedWrJobs(db)).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `Failed to resolve import "./wrJobs"`.

- [ ] **Step 3: Implement**

Create `pi/src/db/wrJobs.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';

/** Enqueue a WR for trail extraction. Idempotent and non-destructive: a repeat enqueue must not
 *  reset attempts, or a poison job would retry forever. */
export function enqueueJob(db: DatabaseSync, wrId: number): void {
  db.prepare('INSERT INTO wr_jobs(wr_id) VALUES (?) ON CONFLICT(wr_id) DO NOTHING').run(wrId);
}

/** Seed jobs for every current, non-removed, videoed WR that has no trail yet. Runs on every
 *  boot; idempotent (ON CONFLICT DO NOTHING preserves an existing row's attempts).
 *  Returns the number of jobs added. */
export function seedWrJobs(db: DatabaseSync): number {
  const info = db.prepare(
    `INSERT INTO wr_jobs(wr_id)
     SELECT w.id FROM world_records w
     WHERE w.is_current = 1
       AND w.removed_at IS NULL
       AND w.video_url IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = w.id)
     ON CONFLICT(wr_id) DO NOTHING`
  ).run();
  return Number(info.changes);
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Enqueue from reconcile**

In `pi/src/wr/reconcile.ts`, add the import:

```ts
import { enqueueJob } from '../db/wrJobs';
```

Immediately after the `flagUnresolved(...)` call added in Task 2 Step 8 (i.e. after the transaction commits), add:

```ts
  // Enqueue for trail extraction. A WR that later falls stays queued — supersession lowers its
  // claim priority but never removes it, so historic trails accumulate without a backfill.
  const wrId = insertedWrId ?? reflaggedWrId;
  if (wrId !== null && s.videoUrl) enqueueJob(db, wrId);
```

- [ ] **Step 6: Test the enqueue**

Append to `pi/src/wr/reconcile.test.ts`:

```ts
  it('enqueues a wr_job for a newly inserted current WR', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr()]);
    const id = (db.prepare('SELECT id FROM world_records WHERE is_current=1').get() as any).id;
    expect(db.prepare('SELECT wr_id FROM wr_jobs').all()).toEqual([{ wr_id: id }]);
  });

  it('does not enqueue a WR with no video', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ videoUrl: null })]);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_jobs').get()).toMatchObject({ n: 0 });
  });
```

Run: `cd pi && npx vitest run src/wr/reconcile.test.ts`
Expected: PASS.

- [ ] **Step 7: Seed on boot**

In `pi/src/db/connect.ts`, add the import at the top:

```ts
import { seedWrJobs } from './wrJobs';
```

and append inside `applySchema`, as the last statement (after the Gub recolour at :86):

```ts
  // Seed WR trail-extraction jobs for the current board. Idempotent; safe on every boot.
  seedWrJobs(db);
```

- [ ] **Step 8: Verify and commit**

Run: `cd pi && npx tsc --noEmit && npx vitest run`
Expected: PASS — full suite.

```bash
git add pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts pi/src/wr/reconcile.ts pi/src/wr/reconcile.test.ts pi/src/db/connect.ts
git commit -m "feat(wr): enqueue trail-extraction jobs on reconcile + seed on boot"
```

---

### Task 7: Atomic job claim

**Files:**
- Modify: `pi/src/db/wrJobs.ts`
- Modify: `pi/src/db/wrJobs.test.ts`

**Interfaces:**
- Produces: `type WrJob`; `DEFAULT_LEASE_SEC`; `claimJob(db, owner, leaseSec?) → WrJob | null`. `owner` is the caller's `X-Worker-Id` (a per-machine id), **not** a player name — see the spec-correction section. `WrJob.attempt` drives the worker's retry tiers (spec §6.4): attempt 3 escalates to 4K.

- [ ] **Step 1: Write the failing test**

Append to `pi/src/db/wrJobs.test.ts`:

```ts
import { claimJob } from './wrJobs';

describe('claimJob', () => {
  it('returns null when nothing is queued', () => {
    expect(claimJob(setup(), 'w1')).toBeNull();
  });

  it('claims a queued job, stamps the lease, and counts the attempt', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    const job = claimJob(db, 'w1');
    expect(job).toMatchObject({
      wr_id: 10, cc: 150, course_slug: 'mario_circuit', course_name: 'Mario Circuit',
      video_url: 'https://youtu.be/x', record_ms: 62934, character_slug: 'toadette', attempt: 1,
    });
    const row = db.prepare('SELECT lease_owner, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: 'w1', attempts: 1 });
  });

  it('does not hand the same job to a second worker while leased', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    expect(claimJob(db, 'w1')).not.toBeNull();
    expect(claimJob(db, 'w2')).toBeNull();
  });

  it('re-offers a job whose lease expired, and burns the attempt (crash recovery)', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    claimJob(db, 'w1');
    db.prepare("UPDATE wr_jobs SET lease_until = datetime('now','-1 minute') WHERE wr_id=10").run();
    const again = claimJob(db, 'w2');
    expect(again).toMatchObject({ wr_id: 10, attempt: 2 });
  });

  it('skips a WR whose character_slug is unresolved (unprocessable)', () => {
    const db = setup(); addWr(db, 10);
    db.prepare('UPDATE world_records SET character_slug=NULL WHERE id=10').run();
    seedWrJobs(db);
    expect(claimJob(db, 'w1')).toBeNull();
  });

  it('skips a job at the attempts cap', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    expect(claimJob(db, 'w1')).toBeNull();
  });

  it('skips an already-trailed WR', () => {
    const db = setup(); addWr(db, 10); enqueueJob(db, 10);
    insertWrTrail(db, 10, [{ t_ms: 1, cx: 1, cy: 1, score: 0.9, lap: 1 }]);
    expect(claimJob(db, 'w1')).toBeNull();
  });

  it('prioritises current over superseded, then newest first', () => {
    const db = setup();
    addWr(db, 10, { current: 0 });   // superseded
    addWr(db, 11, { current: 1 });   // current
    enqueueJob(db, 10); enqueueJob(db, 11);
    expect(claimJob(db, 'w1')!.wr_id).toBe(11);   // current wins
    expect(claimJob(db, 'w2')!.wr_id).toBe(10);   // superseded still processed
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `claimJob is not a function`.

- [ ] **Step 3: Implement**

Append to `pi/src/db/wrJobs.ts`:

```ts
export type WrJob = {
  wr_id: number; cc: number; course_slug: string; course_name: string;
  video_url: string; record_ms: number; lap_splits_ms: number[] | null;
  character_slug: string; costume_slug: string | null; kart_slug: string | null;
  attempt: number;             // 1-based; drives the worker's retry tiers (spec §6.4)
  lease_until: string;
};

export const DEFAULT_LEASE_SEC = 600;   // 10 min: ~10s download + ~100s processing, wide margin

type ClaimRow = {
  wr_id: number; cc: number; course_slug: string; course_name: string;
  video_url: string; record_ms: number; lap_splits_ms: string | null;
  character_slug: string; costume_slug: string | null; kart_slug: string | null;
};

/**
 * Atomically lease the next processable job, or null if there is none.
 *
 * Claimable = enqueued, not removed, has a video, HAS A RESOLVED CHARACTER SLUG (an unslugged
 * WR cannot be turned into a set_selection, so it is unprocessable), has no trail yet, is not
 * under a live lease, and is under the attempts cap.
 *
 * Deliberately NOT filtered on is_current: supersession changes priority, not eligibility — a
 * WR that fell before we got to it is still valid data for that wr_id.
 *
 * attempts increments on CLAIM, not on failure, so a worker that crashes without reporting
 * still burns an attempt and a poison job cannot retry forever. releaseJob() un-does it for a
 * voluntary pause.
 */
export function claimJob(db: DatabaseSync, owner: string, leaseSec = DEFAULT_LEASE_SEC): WrJob | null {
  db.exec('BEGIN IMMEDIATE');
  try {
    const row = db.prepare(
      `SELECT j.wr_id, w.cc, w.video_url, w.record_ms, w.lap_splits_ms,
              w.character_slug, w.costume_slug, w.kart_slug,
              c.slug AS course_slug, c.display_name AS course_name
       FROM wr_jobs j
       JOIN world_records w ON w.id = j.wr_id
       JOIN courses c ON c.id = w.course_id
       WHERE w.removed_at IS NULL
         AND w.video_url IS NOT NULL
         AND w.character_slug IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = j.wr_id)
         AND (j.lease_until IS NULL OR j.lease_until < datetime('now'))
         AND j.attempts < 5
       ORDER BY w.is_current DESC, w.achieved_at DESC, j.enqueued_at ASC
       LIMIT 1`
    ).get() as ClaimRow | undefined;

    if (!row) { db.exec('COMMIT'); return null; }

    const upd = db.prepare(
      `UPDATE wr_jobs
       SET lease_owner=?, lease_until=datetime('now', ?), attempts=attempts+1, updated_at=datetime('now')
       WHERE wr_id=?
       RETURNING attempts, lease_until`
    ).get(owner, `+${leaseSec} seconds`, row.wr_id) as { attempts: number; lease_until: string };

    db.exec('COMMIT');
    return {
      wr_id: row.wr_id, cc: row.cc, course_slug: row.course_slug, course_name: row.course_name,
      video_url: row.video_url, record_ms: row.record_ms,
      lap_splits_ms: row.lap_splits_ms ? (JSON.parse(row.lap_splits_ms) as number[]) : null,
      character_slug: row.character_slug, costume_slug: row.costume_slug, kart_slug: row.kart_slug,
      attempt: upd.attempts, lease_until: upd.lease_until,
    };
  } catch (e) { db.exec('ROLLBACK'); throw e; }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
cd pi && npx tsc --noEmit
git add pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts
git commit -m "feat(wr): atomic wr_jobs claim with lease + priority ordering"
```

---

### Task 8: Heartbeat, release, complete, fail + the routes

**Files:**
- Modify: `pi/src/db/wrJobs.ts`
- Modify: `pi/src/db/wrJobs.test.ts`
- Create: `pi/src/api/wrJobs.ts`
- Create: `pi/src/api/wrJobs.test.ts`
- Modify: `pi/src/api/app.ts`

**Interfaces:**
- Consumes: `requireToken(db)` from `./auth` (`auth.ts:29` — the existing header-only player gate); `insertWrTrail` (Task 5); `claimJob` + `DEFAULT_LEASE_SEC` (Task 7); `type Point` from `db/types.ts:4`.
- Produces: `heartbeatJob` / `releaseJob` / `completeJob` / `failJob`; `wrJobsRoutes(db) → Hono<Env>`.

- [ ] **Step 1: Write the failing db test**

Append to `pi/src/db/wrJobs.test.ts`:

```ts
import { heartbeatJob, releaseJob, completeJob, failJob } from './wrJobs';
import { getWrTrail } from './wrTrails';

describe('lease lifecycle', () => {
  const queued = () => { const db = setup(); addWr(db, 10); seedWrJobs(db); return db; };

  it('heartbeat extends only for the lease owner', () => {
    const db = queued(); claimJob(db, 'w1', 60);
    expect(heartbeatJob(db, 10, 'w1', 600)).toBe(true);
    expect(heartbeatJob(db, 10, 'w2', 600)).toBe(false);   // not the owner
  });

  it('heartbeat fails once the lease has expired', () => {
    const db = queued(); claimJob(db, 'w1');
    db.prepare("UPDATE wr_jobs SET lease_until = datetime('now','-1 minute') WHERE wr_id=10").run();
    expect(heartbeatJob(db, 10, 'w1', 600)).toBe(false);
  });

  it('release clears the lease AND refunds the attempt (a pause must not burn one)', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(releaseJob(db, 10, 'w1')).toBe(true);
    const row = db.prepare('SELECT lease_owner, lease_until, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: null, lease_until: null, attempts: 0 });
    expect(claimJob(db, 'w2')).not.toBeNull();             // immediately re-claimable
  });

  it('release by a non-owner does nothing', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(releaseJob(db, 10, 'w2')).toBe(false);
  });

  it('complete stores the trail and clears the lease', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(completeJob(db, 10, 'w1', [[14, 1635, 875, 0.79, 1], [114, 1636, 870, 0.81, 1]])).toBe(true);
    expect(getWrTrail(db, 10)).toHaveLength(2);
    expect(db.prepare('SELECT lease_owner FROM wr_jobs WHERE wr_id=10').get()).toMatchObject({ lease_owner: null });
    expect(claimJob(db, 'w2')).toBeNull();                 // done: trail exists
  });

  it('complete accepts a legacy 4-tuple point (lap omitted)', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(completeJob(db, 10, 'w1', [[14, 1635, 875, 0.79]] as any)).toBe(true);
    expect(getWrTrail(db, 10)[0].lap).toBeNull();
  });

  it('complete by a non-owner is rejected and stores nothing', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(completeJob(db, 10, 'w2', [[14, 1635, 875, 0.79, 1]])).toBe(false);
    expect(getWrTrail(db, 10)).toEqual([]);
  });

  it('fail records the reason and clears the lease, keeping the attempt burned', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(failJob(db, 10, 'w1', 'time_mismatch')).toBe(true);
    const row = db.prepare('SELECT lease_owner, last_error, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: null, last_error: 'time_mismatch', attempts: 1 });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `heartbeatJob is not a function`.

- [ ] **Step 3: Implement the lease functions**

Append to `pi/src/db/wrJobs.ts`:

```ts
import type { Point } from './types';
import { insertWrTrail } from './wrTrails';
import type { TrailPoint } from './trailCodec';

/** Wire 4/5-tuple -> TrailPoint. Legacy 4-tuples have no lap; null is the codec's sentinel. */
const toTrailPoints = (pts: Point[]): TrailPoint[] =>
  pts.map((p) => ({ t_ms: p[0], cx: p[1], cy: p[2], score: p[3], lap: p[4] ?? null }));

/** Extend a live lease. Only the current owner of an UNEXPIRED lease may extend — once it has
 *  lapsed the job is fair game and a zombie worker must not reclaim it by heartbeat. */
export function heartbeatJob(db: DatabaseSync, wrId: number, owner: string,
                             leaseSec = DEFAULT_LEASE_SEC): boolean {
  const info = db.prepare(
    `UPDATE wr_jobs SET lease_until=datetime('now', ?), updated_at=datetime('now')
     WHERE wr_id=? AND lease_owner=? AND lease_until >= datetime('now')`
  ).run(`+${leaseSec} seconds`, wrId, owner);
  return Number(info.changes) > 0;
}

/** Voluntarily give a job back (pause mid-processing). Refunds the attempt claimJob charged —
 *  a deliberate pause must not count against the cap, whereas a crash (lease expiry) does. */
export function releaseJob(db: DatabaseSync, wrId: number, owner: string): boolean {
  const info = db.prepare(
    `UPDATE wr_jobs SET lease_owner=NULL, lease_until=NULL,
       attempts=MAX(0, attempts-1), updated_at=datetime('now')
     WHERE wr_id=? AND lease_owner=?`
  ).run(wrId, owner);
  return Number(info.changes) > 0;
}

/** Store the extracted trail and close the job. The wr_trails row is what marks it done. */
export function completeJob(db: DatabaseSync, wrId: number, owner: string, pts: Point[]): boolean {
  const owns = db.prepare('SELECT 1 FROM wr_jobs WHERE wr_id=? AND lease_owner=?').get(wrId, owner);
  if (!owns) return false;
  if (pts.length === 0) return false;
  db.exec('BEGIN IMMEDIATE');
  try {
    insertWrTrail(db, wrId, toTrailPoints(pts));
    db.prepare(`UPDATE wr_jobs SET lease_owner=NULL, lease_until=NULL, last_error=NULL,
                  updated_at=datetime('now') WHERE wr_id=?`).run(wrId);
    db.exec('COMMIT');
    return true;
  } catch (e) { db.exec('ROLLBACK'); throw e; }
}

/** Record a failure and free the lease. The attempt claimJob charged stays burned, so repeated
 *  failures walk the job to the cap and stop it. */
export function failJob(db: DatabaseSync, wrId: number, owner: string, error: string): boolean {
  const info = db.prepare(
    `UPDATE wr_jobs SET lease_owner=NULL, lease_until=NULL, last_error=?, updated_at=datetime('now')
     WHERE wr_id=? AND lease_owner=?`
  ).run(error.slice(0, 500), wrId, owner);
  return Number(info.changes) > 0;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: PASS, 21 tests.

- [ ] **Step 5: Write the failing route test**

Create `pi/src/api/wrJobs.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { mintToken } from '../db/players';
import { seedWrJobs } from '../db/wrJobs';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  db.prepare("INSERT INTO players(id, display_name) VALUES (1,'Paul')").run();
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                achieved_at, video_url, character_slug, costume_slug, kart_slug, is_current)
              VALUES (10,1,150,'JaK',62934,'1:02.934','2026-04-06T00:00:00.000Z',
                      'https://youtu.be/x','toadette','explorer','baby_blooper',1)`).run();
  seedWrJobs(db);
  const token = mintToken(db, 'Paul');
  const app = createApp(db, new EventHub());
  // Same player token on both machines — the X-Worker-Id is what separates the leases.
  const w1 = { Authorization: `Bearer ${token}`, 'X-Worker-Id': 'machine-a' };
  const w2 = { Authorization: `Bearer ${token}`, 'X-Worker-Id': 'machine-b' };
  return { db, app, token, w1, w2 };
}

describe('/v1/wr-jobs', () => {
  it('401s with no token', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: { 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });

  it('401s on a bad token', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST',
      headers: { Authorization: 'Bearer nope', 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });

  it('rejects a ?token= query param on a write (header-only)', async () => {
    const { app, token } = setup();
    const res = await app.request(`/v1/wr-jobs/claim?token=${token}`, { method: 'POST',
      headers: { 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });

  it('400s when X-Worker-Id is missing (no lease identity)', async () => {
    const { app, token } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST',
      headers: { Authorization: `Bearer ${token}` } });
    expect(res.status).toBe(400);
  });

  it('claims a job', async () => {
    const { app, w1 } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ wr_id: 10, course_slug: 'mario_circuit',
      character_slug: 'toadette', costume_slug: 'explorer', attempt: 1 });
  });

  it('204s when the queue is empty', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect(res.status).toBe(204);
  });

  it('does not hand the same job to a second machine on the same player token', async () => {
    const { app, w1, w2 } = setup();
    expect((await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 })).status).toBe(200);
    expect((await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w2 })).status).toBe(204);
  });

  it('heartbeats, then 409s for a different machine on the same token', async () => {
    const { app, w1, w2 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect((await app.request('/v1/wr-jobs/10/heartbeat', { method: 'POST', headers: w1 })).status).toBe(200);
    expect((await app.request('/v1/wr-jobs/10/heartbeat', { method: 'POST', headers: w2 })).status).toBe(409);
  });

  it('accepts a result and stores the trail', async () => {
    const { db, app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, points: [[14, 1635, 875, 0.79, 1], [114, 1636, 870, 0.81, 1]] }),
    });
    expect(res.status).toBe(200);
    expect(db.prepare('SELECT n FROM wr_trails WHERE wr_id=10').get()).toMatchObject({ n: 2 });
  });

  it('409s a result from a machine that does not hold the lease', async () => {
    const { db, app, w1, w2 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w2, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, points: [[14, 1635, 875, 0.79, 1]] }),
    });
    expect(res.status).toBe(409);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_trails').get()).toMatchObject({ n: 0 });
  });

  it('records a failure result', async () => {
    const { db, app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'time_mismatch' }),
    });
    expect(res.status).toBe(200);
    expect(db.prepare('SELECT last_error FROM wr_jobs WHERE wr_id=10').get())
      .toMatchObject({ last_error: 'time_mismatch' });
  });

  it('releases a job back to the queue', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect((await app.request('/v1/wr-jobs/10/release', { method: 'POST', headers: w1 })).status).toBe(200);
    expect((await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 })).status).toBe(200);
  });
});
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd pi && npx vitest run src/api/wrJobs.test.ts`
Expected: FAIL — 404s (routes not mounted).

- [ ] **Step 7: Implement the routes**

Create `pi/src/api/wrJobs.ts`:

```ts
import { Hono } from 'hono';
import type { Context } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { Point } from '../db/types';
import { requireToken } from './auth';
import { claimJob, heartbeatJob, releaseJob, completeJob, failJob, DEFAULT_LEASE_SEC } from '../db/wrJobs';

type ResultBody = { ok: true; points: Point[] } | { ok: false; error: string };

/** Per-MACHINE lease identity. The player token authenticates the person; one person may run the
 *  service on several PCs, so the lease owner must be the machine or one PC could complete
 *  another's job. Generated once per install and persisted by the service. */
const workerIdOf = (c: Context): string | null => {
  const v = c.req.header('x-worker-id')?.trim();
  return v && v.length > 0 && v.length <= 64 ? v : null;
};

/** WR-service worker API. Auth is the ordinary player token, header-only (a ?token= in a write
 *  URL would leak into logs) — the same double-gate POST /v1/runs uses: the app-level
 *  requireTokenAny runs first, then requireToken here narrows it to header-only. */
export function wrJobsRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();

  r.post('/v1/wr-jobs/claim', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const job = claimJob(db, worker, DEFAULT_LEASE_SEC);
    return job ? c.json(job) : c.body(null, 204);
  });

  r.post('/v1/wr-jobs/:wr_id/heartbeat', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const ok = heartbeatJob(db, Number(c.req.param('wr_id')), worker, DEFAULT_LEASE_SEC);
    return ok ? c.json({ ok: true }) : c.json({ error: 'not the lease owner, or lease expired' }, 409);
  });

  r.post('/v1/wr-jobs/:wr_id/release', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const ok = releaseJob(db, Number(c.req.param('wr_id')), worker);
    return ok ? c.json({ ok: true }) : c.json({ error: 'not the lease owner' }, 409);
  });

  r.post('/v1/wr-jobs/:wr_id/result', requireToken(db), async (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const wrId = Number(c.req.param('wr_id'));
    const body = (await c.req.json()) as ResultBody;
    if (typeof body?.ok !== 'boolean') return c.json({ error: 'bad payload' }, 400);

    if (body.ok) {
      if (!Array.isArray(body.points) || body.points.length === 0) {
        return c.json({ error: 'empty trail' }, 400);
      }
      const stored = completeJob(db, wrId, worker, body.points);
      return stored ? c.json({ ok: true, n: body.points.length })
                    : c.json({ error: 'not the lease owner' }, 409);
    }
    const recorded = failJob(db, wrId, worker, body.error ?? 'unknown');
    return recorded ? c.json({ ok: true }) : c.json({ error: 'not the lease owner' }, 409);
  });

  return r;
}
```

- [ ] **Step 8: Mount them**

In `pi/src/api/app.ts`, add the import beside the other route imports:

```ts
import { wrJobsRoutes } from './wrJobs';
```

and mount beside the other `app.route` calls:

```ts
  app.route('/', wrJobsRoutes(db));
```

**No gate change is needed.** `/v1/wr-jobs/*` is not in `OPEN`, so the app-level `requireTokenAny`
gate applies, and each route then re-gates header-only with `requireToken` — identical to how
`POST /v1/runs` is protected. Do not add these paths to `PUBLIC_READS` or `OPEN`.

- [ ] **Step 9: Run to verify it passes**

Run: `cd pi && npx vitest run src/api/wrJobs.test.ts`
Expected: PASS, 12 tests.

- [ ] **Step 10: Commit**

```bash
cd pi && npx tsc --noEmit && npx vitest run
git add pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts pi/src/api/wrJobs.ts pi/src/api/wrJobs.test.ts pi/src/api/app.ts
git commit -m "feat(wr): lease lifecycle + /v1/wr-jobs routes (player token + X-Worker-Id)"
```

---

### Task 9: Serve WR trails publicly

**Files:**
- Modify: `pi/src/api/reads.ts` (add route beside `/v1/trails` at :50-55)
- Modify: `pi/src/api/app.ts` (`PUBLIC_READS` at :37)
- Test: `pi/src/api/wrTrailsRead.test.ts` (create)

**Interfaces:**
- Consumes: `courseWrTrails(db, courseId, cc)` (Task 5); the module-local `course(c)` / `num(...)` helpers already inside `readsRoutes` (`reads.ts:13,18`).

- [ ] **Step 1: Write the failing test**

Create `pi/src/api/wrTrailsRead.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { insertWrTrail } from '../db/wrTrails';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                achieved_at, video_url, is_current)
              VALUES (10,1,150,'JaK',62934,'1:02.934','2026-04-06T00:00:00.000Z','https://youtu.be/x',1)`).run();
  insertWrTrail(db, 10, [{ t_ms: 14, cx: 1635, cy: 875, score: 0.79, lap: 1 }]);
  return createApp(db, new EventHub());
}

describe('GET /v1/wr-trails', () => {
  it('serves trails with NO token (public read)', async () => {
    const res = await setup().request('/v1/wr-trails?course=mario_circuit');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveLength(1);
    expect(body[0]).toMatchObject({ wr_id: 10, holder_name: 'JaK', record_ms: 62934, is_current: 1 });
    expect(body[0].points[0]).toEqual([14, 1635, 875, 0.79]);
  });

  it('sets permissive CORS for the cross-origin website', async () => {
    const res = await setup().request('/v1/wr-trails?course=mario_circuit');
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
  });

  it('400s on an unknown course', async () => {
    const res = await setup().request('/v1/wr-trails?course=not_a_course');
    expect(res.status).toBe(400);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/api/wrTrailsRead.test.ts`
Expected: FAIL — 401 (route missing, so the global player gate rejects it).

- [ ] **Step 3: Add the route**

In `pi/src/api/reads.ts`, extend the `../db/wrTrails` import (add it as a new import line):

```ts
import { courseWrTrails } from '../db/wrTrails';
```

and add the route directly after the `/v1/trails` route (currently ends :55):

```ts
  r.get('/v1/wr-trails', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(courseWrTrails(db, cid, num(c.req.query('cc'), 150)));
  });
```

- [ ] **Step 4: Make it public**

In `pi/src/api/app.ts`, add `'/v1/wr-trails'` to `PUBLIC_READS` (currently :37):

```ts
  const PUBLIC_READS = ['/v1/leaderboard', '/v1/world-records', '/v1/wr-trails', '/v1/roster', '/v1/territory', '/v1/territory/timeline', '/v1/version', '/v1/activity'];
```

(`PUBLIC_READS` feeds both the CORS loop and the `OPEN` set, so this one edit does both.)

- [ ] **Step 5: Run to verify it passes**

Run: `cd pi && npx vitest run src/api/wrTrailsRead.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 6: Full suite, typecheck, commit**

Run: `cd pi && npx tsc --noEmit && npx vitest run`
Expected: PASS — entire suite green.

```bash
git add pi/src/api/reads.ts pi/src/api/app.ts pi/src/api/wrTrailsRead.test.ts
git commit -m "feat(wr): serve WR trails at GET /v1/wr-trails (public read)"
```

---

## Manual verification

After Task 9, prove the loop end-to-end against a scratch DB — no worker needed.

**Do not point `MKW_DB` at the real `~/mkw-data/mkw.db`.** Use a scratch path throughout.

```bash
cd pi
export MKW_DB=/tmp/wrtest.db && rm -f /tmp/wrtest.db /tmp/wrtest.db-wal /tmp/wrtest.db-shm
npx tsx src/scripts/scrapeWr.ts                       # real mkwrs scrape -> 30 current WRs
npx tsx -e "
  import {openDb,applySchema} from './src/db/connect.ts';
  const db=openDb(process.env.MKW_DB); applySchema(db);
  console.log('jobs:', db.prepare('SELECT COUNT(*) n FROM wr_jobs').get());
  console.log('slugged:', db.prepare('SELECT COUNT(*) n FROM world_records WHERE character_slug IS NOT NULL AND is_current=1').get());
  db.prepare(\"INSERT INTO players(display_name) VALUES ('Paul')\").run();
"
npx tsx src/scripts/mintToken.ts Paul                  # copy the player token
npm run dev &                                          # server on :8787
```

Claim a job with the player token plus a machine id:

```bash
TOK=<player-token>
curl -s -XPOST -H "Authorization: Bearer $TOK" -H "X-Worker-Id: machine-a" \
  http://127.0.0.1:8787/v1/wr-jobs/claim | head -c 400
```

Expect ~30 jobs, ~30 slugged current WRs, and a claim returning a real `wr_id` + `video_url` + `character_slug` with `attempt: 1`. Then confirm the guards:

```bash
# same token, DIFFERENT machine -> 204: the lease is per-machine, not per-person
curl -s -o /dev/null -w '%{http_code}\n' -XPOST -H "Authorization: Bearer $TOK" \
  -H "X-Worker-Id: machine-b" http://127.0.0.1:8787/v1/wr-jobs/claim          # expect 204

# no machine id -> 400
curl -s -o /dev/null -w '%{http_code}\n' -XPOST -H "Authorization: Bearer $TOK" \
  http://127.0.0.1:8787/v1/wr-jobs/claim                                       # expect 400

# token in the URL instead of the header -> 401 (writes are header-only)
curl -s -o /dev/null -w '%{http_code}\n' -XPOST -H "X-Worker-Id: machine-a" \
  "http://127.0.0.1:8787/v1/wr-jobs/claim?token=$TOK"                          # expect 401

# public read, no token
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://127.0.0.1:8787/v1/wr-trails?course=mario_circuit"                    # expect 200, []
```

Also confirm the alert path by mapping a name badly on purpose: temporarily remove `'mach_rocket'` from `KARTS` in `wr/roster.ts`, re-run `scrapeWr.ts` against a fresh scratch DB, and check `npm run wr-flags` lists it. Restore `roster.ts` afterwards.

## What Plan 1 deliberately does not do

- No worker, no yt-dlp, no engine spawning — that is Plan 2.
- No client display — that is Plan 3.
- No historic backfill: `seedWrJobs` only enqueues `is_current=1` WRs. Historic trails accumulate on their own as current WRs fall (Task 8's claim has no `is_current` filter). Walking the back-catalogue is a later, separate decision (spec §2).
- `history_reconcile.ts` keeps its own inline slug resolution (see Global Constraints).
