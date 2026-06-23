# /version Diagnostic Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an unlisted `#/version` page on thekartoff.com that reports latest-available, deployed, and per-player installed pbenguin-app versions.

**Architecture:** A new public-but-unlisted `GET /v1/version` on the pi server returns latest-available versions (highest git tag for server/bot/site via the GitHub tags API; last release for the app via the updater `latest.json`), deployed versions (server in-process, bot self-reported to a `service_status` DB row), and the roster's last-ran app versions (persisted to `players.app_version` from the presence frame). A new `web/` Svelte page renders it, reading the deployed site version from a build-time `__SITE_VERSION__` define.

**Tech Stack:** TypeScript + Hono + `node:sqlite` (pi server), discord.js (bot), Svelte 4 + Vite (web + desktop frontend), vitest.

## Global Constraints

- **One shared version.** No per-component independent versioning. Source of truth is the root `package.json` version (tag `v{x.y.z}`).
- **"Latest available" semantics:** server/bot/site = the highest git **tag** (GitHub tags API); pbenguin app = the last **release** (the updater `latest.json` `.version`).
- **Public-but-unlisted:** `/v1/version` joins `PUBLIC_READS` (permissive GET CORS, skips the token gate), exactly like `/v1/territory/timeline` that `#/heat` uses. No navbar tab.
- **Semver comparison:** strip a leading `v`; missing parts coerce to `0`; compare numerically.
- **The endpoint never throws:** always HTTP 200 with whatever data it has; failed external lookups yield `null` + a string in `latest.errors[]`. Latest lookups cached ~10 min (`MKW_VERSION_CACHE_MS`, default 600000); each external fetch has a 5s `AbortController` timeout.
- **pi has no whole-project tsc gate** — tests run via tsx. The bar per task is: the named vitest file(s) green, and feature source type-clean. Don't chase pre-existing tsc debt elsewhere.
- **Commit messages** end with the repo convention trailers:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01QPhTPtfNrgcu9nQUKVhect`.
- **Run commands** (from repo root): pi tests `npm --prefix pi test -- <pattern>`; web tests `npm --prefix web test -- <pattern>`; desktop-frontend tests `npm run test:js -- <pattern>`; web type-check `npm --prefix web run check`; web build `npm --prefix web run build`.

## File Structure

**New (pi):**
- `pi/src/version/repoVersion.ts` — read the root `package.json` version once (the deployed server/bot version).
- `pi/src/version/serviceStatus.ts` — `reportService` / `readService` over a `service_status` table (the bot self-reports; the route reads).
- `pi/src/version/latest.ts` — semver helpers, release-source resolution, and a cached GitHub-tags + updater-manifest fetcher.
- `pi/src/api/version.ts` — the `GET /v1/version` route.
- Tests: `pi/src/version/repoVersion.test.ts`, `pi/src/version/serviceStatus.test.ts`, `pi/src/version/latest.test.ts`, `pi/src/api/version.test.ts`.

**New (web):**
- `web/src/lib/version.js` — pure, tested display helpers + the `SITE_VERSION` build constant.
- `web/src/VersionPage.svelte` — the page (presentation only).
- Test: `web/src/lib/version.test.js`.

**Modified:**
- `server/schema.sql`, `pi/src/db/connect.ts` — `players.app_version` column + `service_status` table.
- `pi/src/presence/hub.ts` — accept + persist `app_version`.
- `pi/src/bot/index.ts` — self-report the bot version on boot.
- `pi/src/api/app.ts` — add `/v1/version` to `PUBLIC_READS`; mount `versionRoutes`.
- `web/vite.config.js` — `__SITE_VERSION__` define from the root `package.json`.
- `web/src/lib/view.js`, `web/src/App.svelte`, `web/src/lib/api.js` — route + dispatch + URL helper.
- `src/lib/stores.js`, `src/App.svelte`, `src/lib/presence.js` — `appVersion` store, set it, send it.
- `src/lib/presence.test.js` — update the strict `frame()` assertion.

---

### Task 1: DB migration — `players.app_version` + `service_status`

**Files:**
- Modify: `server/schema.sql`
- Modify: `pi/src/db/connect.ts:16-81` (the `applySchema` migration block)
- Test: `pi/src/db/connect.test.ts`

**Interfaces:**
- Produces: a `players.app_version TEXT` column (nullable) and a `service_status (service TEXT PRIMARY KEY, version TEXT, booted_at INTEGER)` table, present on both fresh and migrated DBs after `applySchema(db)`.

- [ ] **Step 1: Write the failing test**

Add to the end of `pi/src/db/connect.test.ts`:

```ts
describe('applySchema app_version + service_status migration', () => {
  it('adds app_version to a legacy players table (idempotent) and creates service_status', () => {
    const db = new DatabaseSync(':memory:');
    // Legacy players shape that predates app_version (has last_seen_at already).
    db.exec(`CREATE TABLE players(
      id INTEGER PRIMARY KEY, display_name TEXT NOT NULL UNIQUE,
      auth_token_hash TEXT UNIQUE, color TEXT, last_seen_at INTEGER,
      created_at TEXT NOT NULL DEFAULT (datetime('now')));
      INSERT INTO players(id,display_name) VALUES (1,'Paul');`);
    applySchema(db);                                   // additive ALTER adds app_version
    db.prepare('UPDATE players SET app_version=? WHERE id=?').run('2.1.0', 1);
    expect((db.prepare('SELECT app_version FROM players WHERE id=1').get() as { app_version: string }).app_version)
      .toBe('2.1.0');
    // service_status exists and upserts.
    db.prepare(`INSERT INTO service_status(service,version,booted_at) VALUES('bot','2.1.0',5)
                ON CONFLICT(service) DO UPDATE SET version=excluded.version`).run();
    expect((db.prepare("SELECT version FROM service_status WHERE service='bot'").get() as { version: string }).version)
      .toBe('2.1.0');
    applySchema(db);                                   // idempotent second boot
    expect((db.prepare('SELECT app_version FROM players WHERE id=1').get() as { app_version: string }).app_version)
      .toBe('2.1.0');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix pi test -- connect`
Expected: FAIL — `no such column: app_version` (and/or `no such table: service_status`).

- [ ] **Step 3: Add the column to the canonical schema**

In `server/schema.sql`, add `app_version` to the `players` table (after `last_seen_at`):

```sql
CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    display_name    TEXT NOT NULL UNIQUE,
    auth_token_hash TEXT UNIQUE,
    color           TEXT,
    last_seen_at    INTEGER,
    app_version     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

And add the table after the `wr_meta` line (`CREATE TABLE IF NOT EXISTS wr_meta (key TEXT PRIMARY KEY, value TEXT);`):

```sql
CREATE TABLE IF NOT EXISTS service_status (
    service    TEXT PRIMARY KEY,
    version    TEXT,
    booted_at  INTEGER
);
```

- [ ] **Step 4: Add the additive migrations**

In `pi/src/db/connect.ts`, inside `applySchema`, immediately after the `last_seen_at` migration block (line ~22, after the `try { db.exec('ALTER TABLE players ADD COLUMN last_seen_at INTEGER'); } catch { ... }` line), add:

```ts
  // Additive: last-ran pbenguin-app version per player (reported through the presence frame).
  // Nullable -> never reported. Read by /v1/version.
  try { db.exec('ALTER TABLE players ADD COLUMN app_version TEXT'); } catch { /* already present */ }
  // Service self-report: a separate process (the bot) upserts its deployed version + boot time
  // here on boot so /v1/version can show it. Created here for migrated DBs; also in schema.sql.
  db.exec('CREATE TABLE IF NOT EXISTS service_status (service TEXT PRIMARY KEY, version TEXT, booted_at INTEGER)');
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm --prefix pi test -- connect`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/schema.sql pi/src/db/connect.ts pi/src/db/connect.test.ts
git commit -m "feat(version): add players.app_version + service_status schema"
```

---

### Task 2: Presence hub — accept + persist `app_version`

**Files:**
- Modify: `pi/src/presence/hub.ts:11-25` (the `PresenceFrame` interface), `:142-181` (`update`), and add a private helper near `writeLastSeen` (`:250-253`)
- Test: `pi/src/presence/hub.test.ts`

**Interfaces:**
- Consumes: `players.app_version` column (Task 1).
- Produces: when a frame carries `app_version`, the hub writes it to `players.app_version` (only when the value changes), and never clobbers a stored value when the field is absent.

- [ ] **Step 1: Write the failing test**

Add to the end of the `describe('PresenceHub', ...)` block in `pi/src/presence/hub.test.ts`:

```ts
  it('persists app_version to players on change; absent never clobbers', () => {
    const d = db();
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 1000);
    hub.addSink(() => {});
    const ver = () => (d.prepare('SELECT app_version FROM players WHERE id=1').get() as { app_version: string | null }).app_version;
    hub.update(1, { screen: 'MAIN_MENU', app_version: '2.1.0' });
    expect(ver()).toBe('2.1.0');
    hub.update(1, { screen: 'MAIN_MENU', app_version: '2.2.0' });   // changed -> rewritten
    expect(ver()).toBe('2.2.0');
    hub.update(1, { screen: 'RACING' });                            // absent -> unchanged
    expect(ver()).toBe('2.2.0');
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix pi test -- hub`
Expected: FAIL — `app_version` is not written, so `ver()` is `null` after the first update.

- [ ] **Step 3: Add the field to `PresenceFrame`**

In `pi/src/presence/hub.ts`, add to the `PresenceFrame` interface (after the `invalid_reason?: string | null;` line, before the closing `}`):

```ts
  app_version?: string | null;  // the player's running pbenguin-app version (persisted as last-ran)
```

- [ ] **Step 4: Add the persistence guard field + helper**

In the `PresenceHub` class, add a field next to `private map = ...` (line ~76):

```ts
  // Last app_version persisted per player, so the ~4Hz frame only writes the DB on change.
  private appVersionSeen = new Map<number, string>();
```

And add a private method next to `writeLastSeen` (after the `writeLastSeen` method, ~line 253):

```ts
  /** Persist the player's last-ran app version, but only when it changes (the frame carries it
   *  every tick). A DB hiccup must never break presence, so failures are swallowed. */
  private writeAppVersion(playerId: number, version: string): void {
    if (this.appVersionSeen.get(playerId) === version) return;
    try {
      this.db.prepare('UPDATE players SET app_version=? WHERE id=?').run(version, playerId);
      this.appVersionSeen.set(playerId, version);
    } catch { /* non-fatal */ }
  }
```

- [ ] **Step 5: Call it from `update`**

In `update`, after the `if (!wasOnline) this.writeLastSeen(playerId, now);` line (line ~179), add:

```ts
    if (frame.app_version) this.writeAppVersion(playerId, frame.app_version);
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm --prefix pi test -- hub`
Expected: PASS (all existing hub tests still pass too).

- [ ] **Step 7: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(version): persist last-ran app_version from the presence frame"
```

---

### Task 3: `serviceStatus.ts` — bot self-report read/write

**Files:**
- Create: `pi/src/version/serviceStatus.ts`
- Test: `pi/src/version/serviceStatus.test.ts`

**Interfaces:**
- Produces:
  - `reportService(db: DatabaseSync, service: string, version: string, bootedAt: number): void` — defensively creates `service_status` then upserts the row.
  - `readService(db: DatabaseSync, service: string): { version: string; booted_at: number } | null` — returns the row, or `null` if absent or the table doesn't exist.

- [ ] **Step 1: Write the failing test**

Create `pi/src/version/serviceStatus.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { reportService, readService } from './serviceStatus';

describe('serviceStatus', () => {
  it('reads null before any write (even with no table), then upserts + reads back', () => {
    const db = new DatabaseSync(':memory:');
    expect(readService(db, 'bot')).toBeNull();                  // table absent -> null, no throw
    reportService(db, 'bot', '2.1.0', 1750000000000);
    expect(readService(db, 'bot')).toEqual({ version: '2.1.0', booted_at: 1750000000000 });
    reportService(db, 'bot', '2.2.0', 1750000001000);          // upsert (PK conflict)
    expect(readService(db, 'bot')).toEqual({ version: '2.2.0', booted_at: 1750000001000 });
    expect(readService(db, 'server')).toBeNull();               // unknown service
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix pi test -- serviceStatus`
Expected: FAIL — `Cannot find module './serviceStatus'`.

- [ ] **Step 3: Write the implementation**

Create `pi/src/version/serviceStatus.ts`:

```ts
import type { DatabaseSync } from 'node:sqlite';

/** Upsert a service's deployed version + boot time. The table is created defensively because
 *  the bot is a separate process that never runs applySchema. */
export function reportService(db: DatabaseSync, service: string, version: string, bootedAt: number): void {
  db.exec('CREATE TABLE IF NOT EXISTS service_status (service TEXT PRIMARY KEY, version TEXT, booted_at INTEGER)');
  db.prepare(`INSERT INTO service_status(service,version,booted_at) VALUES(?,?,?)
              ON CONFLICT(service) DO UPDATE SET version=excluded.version, booted_at=excluded.booted_at`)
    .run(service, version, bootedAt);
}

/** The last-reported version + boot time for a service, or null if absent / table missing. */
export function readService(db: DatabaseSync, service: string): { version: string; booted_at: number } | null {
  try {
    return (db.prepare('SELECT version, booted_at FROM service_status WHERE service=?').get(service) as
      { version: string; booted_at: number } | undefined) ?? null;
  } catch { return null; }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix pi test -- serviceStatus`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/version/serviceStatus.ts pi/src/version/serviceStatus.test.ts
git commit -m "feat(version): service_status report/read helpers"
```

---

### Task 4: `repoVersion.ts` — deployed server/bot version

**Files:**
- Create: `pi/src/version/repoVersion.ts`
- Test: `pi/src/version/repoVersion.test.ts`

**Interfaces:**
- Produces: `repoVersion(): string` — the root `package.json` version (read + cached once), or `'unknown'` on failure.

- [ ] **Step 1: Write the failing test**

Create `pi/src/version/repoVersion.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { repoVersion } from './repoVersion';

describe('repoVersion', () => {
  it('reads a semver string from the root package.json', () => {
    expect(repoVersion()).toMatch(/^\d+\.\d+\.\d+/);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix pi test -- repoVersion`
Expected: FAIL — `Cannot find module './repoVersion'`.

- [ ] **Step 3: Write the implementation**

Create `pi/src/version/repoVersion.ts`:

```ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// pi/src/version/repoVersion.ts -> repo root is three levels up.
let cached: string | null = null;

/** The repo's single source-of-truth version (root package.json), read + cached once.
 *  On the Pi the clone is checked out at the deployed tag, so this is the deployed build. */
export function repoVersion(): string {
  if (cached !== null) return cached;
  try {
    const p = fileURLToPath(new URL('../../../package.json', import.meta.url));
    cached = (JSON.parse(readFileSync(p, 'utf8')).version as string) ?? 'unknown';
  } catch { cached = 'unknown'; }
  return cached;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix pi test -- repoVersion`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/version/repoVersion.ts pi/src/version/repoVersion.test.ts
git commit -m "feat(version): repoVersion() reads the root package.json version"
```

---

### Task 5: `latest.ts` — semver helpers + cached latest-version fetcher

**Files:**
- Create: `pi/src/version/latest.ts`
- Test: `pi/src/version/latest.test.ts`

**Interfaces:**
- Produces:
  - `parseSemver(v: string): [number,number,number] | null`
  - `compareSemver(a: string, b: string): number` (-1/0/1; `0` if either is unparseable)
  - `pickLatestTag(names: string[]): string | null` (highest semver, leading `v` stripped)
  - `resolveRelease(): { repo: string | null; manifest: string | null }`
  - `interface LatestVersions { tag: string | null; app: string | null; fetched_at: number; errors: string[] }`
  - `type LatestFn = (force?: boolean) => Promise<LatestVersions>`
  - `makeLatestFetcher(opts?): LatestFn` — caches per `ttlMs`; injectable `fetchImpl`, `now`, `repo`, `manifest`.

- [ ] **Step 1: Write the failing test**

Create `pi/src/version/latest.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { parseSemver, compareSemver, pickLatestTag, resolveRelease, makeLatestFetcher } from './latest';

describe('semver helpers', () => {
  it('parses + compares, tolerating a leading v and missing parts', () => {
    expect(parseSemver('v2.10.3')).toEqual([2, 10, 3]);
    expect(parseSemver('nope')).toBeNull();
    expect(compareSemver('2.2.0', '2.10.0')).toBe(-1);   // numeric, not lexical
    expect(compareSemver('v2.1.0', '2.1.0')).toBe(0);
    expect(compareSemver('bad', '2.1.0')).toBe(0);       // unparseable -> 0
  });
  it('pickLatestTag returns the highest semver, v-stripped', () => {
    expect(pickLatestTag(['v2.1.0', 'v2.10.0', 'v2.2.0', 'garbage'])).toBe('2.10.0');
    expect(pickLatestTag([])).toBeNull();
  });
});

describe('resolveRelease', () => {
  it('parses owner/repo from an updater manifest env override', () => {
    const savedM = process.env.MKW_UPDATER_MANIFEST, savedR = process.env.MKW_RELEASE_REPO;
    process.env.MKW_UPDATER_MANIFEST = 'https://github.com/foo/bar/releases/latest/download/latest.json';
    delete process.env.MKW_RELEASE_REPO;
    const r = resolveRelease();
    expect(r.repo).toBe('foo/bar');
    expect(r.manifest).toContain('latest.json');
    if (savedM === undefined) delete process.env.MKW_UPDATER_MANIFEST; else process.env.MKW_UPDATER_MANIFEST = savedM;
    if (savedR === undefined) delete process.env.MKW_RELEASE_REPO; else process.env.MKW_RELEASE_REPO = savedR;
  });
});

describe('makeLatestFetcher', () => {
  it('picks the tag + app version and caches within the TTL', async () => {
    let calls = 0;
    const fetchImpl = (async (url: string) => {
      calls++;
      if (url.includes('/tags'))
        return { ok: true, json: async () => [{ name: 'v2.1.0' }, { name: 'v2.10.0' }, { name: 'v2.2.0' }] };
      return { ok: true, json: async () => ({ version: '2.1.0' }) };
    }) as unknown as typeof fetch;
    let t = 1000;
    const getLatest = makeLatestFetcher({ fetchImpl, now: () => t, repo: 'o/r', manifest: 'https://x/latest.json', ttlMs: 500 });
    expect(await getLatest()).toMatchObject({ tag: '2.10.0', app: '2.1.0', errors: [] });
    const after = calls;
    await getLatest();                       // within TTL -> served from cache
    expect(calls).toBe(after);
    t = 2000;                                // past TTL -> refetch
    await getLatest();
    expect(calls).toBeGreaterThan(after);
  });

  it('degrades to the last-good value and records an error when a source fails', async () => {
    const fetchImpl = (async (url: string) => {
      if (url.includes('/tags')) return { ok: false, status: 503 };
      return { ok: true, json: async () => ({ version: '2.1.0' }) };
    }) as unknown as typeof fetch;
    const getLatest = makeLatestFetcher({ fetchImpl, now: () => 1, repo: 'o/r', manifest: 'https://x/latest.json' });
    const r = await getLatest();
    expect(r.app).toBe('2.1.0');
    expect(r.tag).toBeNull();
    expect(r.errors.some((e) => e.startsWith('tags:'))).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix pi test -- latest`
Expected: FAIL — `Cannot find module './latest'`.

- [ ] **Step 3: Write the implementation**

Create `pi/src/version/latest.ts`:

```ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export interface LatestVersions { tag: string | null; app: string | null; fetched_at: number; errors: string[]; }
export type LatestFn = (force?: boolean) => Promise<LatestVersions>;

export function parseSemver(v: string): [number, number, number] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec((v ?? '').trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** -1/0/1; 0 when either side is unparseable (callers treat that as "can't tell"). */
export function compareSemver(a: string, b: string): number {
  const pa = parseSemver(a), pb = parseSemver(b);
  if (!pa || !pb) return 0;
  for (let i = 0; i < 3; i++) if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  return 0;
}

/** Highest semver among the tag names (leading v stripped). GitHub's tag order isn't
 *  guaranteed, so we sort ourselves. Assumes < 100 tags (current reality). */
export function pickLatestTag(names: string[]): string | null {
  let best: string | null = null;
  for (const n of names) {
    const p = parseSemver(n);
    if (!p) continue;
    const norm = `${p[0]}.${p[1]}.${p[2]}`;
    if (best === null || compareSemver(norm, best) > 0) best = norm;
  }
  return best;
}

/** Resolve the GitHub repo slug + updater manifest URL. Env overrides win; otherwise both are
 *  derived from tauri.conf.json's updater endpoint. */
export function resolveRelease(): { repo: string | null; manifest: string | null } {
  const envRepo = process.env.MKW_RELEASE_REPO || null;
  const envManifest = process.env.MKW_UPDATER_MANIFEST || null;
  let endpoint: string | null = envManifest;
  if (!endpoint || !envRepo) {
    try {
      const p = fileURLToPath(new URL('../../../src-tauri/tauri.conf.json', import.meta.url));
      const conf = JSON.parse(readFileSync(p, 'utf8'));
      endpoint = endpoint || conf?.plugins?.updater?.endpoints?.[0] || null;
    } catch { /* fall through to whatever we have */ }
  }
  let repo = envRepo;
  if (!repo && endpoint) {
    const m = /github\.com\/([^/]+\/[^/]+?)(?:\.git)?\//.exec(endpoint);
    repo = m ? m[1] : null;
  }
  return { repo, manifest: envManifest || endpoint };
}

async function fetchJson(url: string, headers: Record<string, string>, fetchImpl: typeof fetch): Promise<any> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  try {
    const res = await fetchImpl(url, { headers, signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally { clearTimeout(timer); }
}

/** A cached latest-version fetcher. The two lookups degrade independently to the last-good
 *  value; failures are recorded in `errors` and never throw. */
export function makeLatestFetcher(opts: {
  ttlMs?: number; now?: () => number; fetchImpl?: typeof fetch; repo?: string | null; manifest?: string | null;
} = {}): LatestFn {
  const ttl = opts.ttlMs ?? Number(process.env.MKW_VERSION_CACHE_MS ?? 600000);
  const now = opts.now ?? Date.now;
  const fetchImpl = opts.fetchImpl ?? fetch;
  const resolved = (opts.repo !== undefined || opts.manifest !== undefined)
    ? { repo: opts.repo ?? null, manifest: opts.manifest ?? null }
    : resolveRelease();
  let cache: LatestVersions | null = null;

  return async function getLatest(force = false): Promise<LatestVersions> {
    if (!force && cache && now() - cache.fetched_at < ttl) return cache;
    const errors: string[] = [];
    let tag = cache?.tag ?? null;
    let app = cache?.app ?? null;
    if (resolved.repo) {
      try {
        const headers: Record<string, string> = { Accept: 'application/vnd.github+json', 'User-Agent': 'mkw-version' };
        if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
        const tags = await fetchJson(`https://api.github.com/repos/${resolved.repo}/tags?per_page=100`, headers, fetchImpl);
        const picked = pickLatestTag((tags as { name: string }[]).map((t) => t.name));
        if (picked) tag = picked;
      } catch (e) { errors.push(`tags: ${(e as Error).message}`); }
    } else errors.push('tags: no repo configured');
    if (resolved.manifest) {
      try {
        const mf = await fetchJson(resolved.manifest, { 'User-Agent': 'mkw-version' }, fetchImpl);
        if (mf?.version) app = String(mf.version);
      } catch (e) { errors.push(`app: ${(e as Error).message}`); }
    } else errors.push('app: no manifest configured');
    cache = { tag, app, fetched_at: now(), errors };
    return cache;
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix pi test -- latest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/version/latest.ts pi/src/version/latest.test.ts
git commit -m "feat(version): cached latest tag + release fetcher with semver helpers"
```

---

### Task 6: `GET /v1/version` route + wire into the app

**Files:**
- Create: `pi/src/api/version.ts`
- Modify: `pi/src/api/app.ts:8` (imports), `:21-22` (signature), `:32` (`PUBLIC_READS`), `:39` (mount)
- Test: `pi/src/api/version.test.ts`

**Interfaces:**
- Consumes: `readService` (Task 3), `repoVersion` (Task 4), `makeLatestFetcher`/`LatestFn` (Task 5), `activeSeasonId` (`../db/seasons`), `players.app_version` (Task 1), `PresenceHub` writes (Task 2).
- Produces: `versionRoutes(db, opts?: { latest?: LatestFn; serverVersion?: string; bootedAt?: number }): Hono<Env>` serving `GET /v1/version`; and `createApp(db, hub, invalidateModel?, opts?: { latest?: LatestFn })` now mounts it. Response shape:
  `{ latest: { tag, app, fetched_at, errors }, deployed: { server: { version, booted_at }, bot: { version, booted_at } | null }, players: [{ player_id, name, color, app_version, last_seen_at }] }`.

- [ ] **Step 1: Write the failing test**

Create `pi/src/api/version.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import type { LatestFn } from '../version/latest';

const fakeLatest: LatestFn = async () => ({ tag: '2.1.5', app: '2.1.0', fetched_at: 1750000000000, errors: [] });

function appWith() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec(`INSERT INTO players(id,display_name,color,app_version,last_seen_at) VALUES
    (1,'Paul','#a78bfa','2.1.0',1750000000000),(2,'Gub','#38bdf8','2.0.0',1749000000000),(3,'Aliias',NULL,NULL,NULL)`);
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2),(1,3)");
  db.exec("INSERT INTO service_status(service,version,booted_at) VALUES ('bot','2.1.5',1750000000000)");
  return createApp(db, new EventHub(), undefined, { latest: fakeLatest });
}

describe('GET /v1/version', () => {
  it('is public (no token) and reports latest, deployed, and per-player app versions', async () => {
    const res = await appWith().request('/v1/version');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.latest).toMatchObject({ tag: '2.1.5', app: '2.1.0', errors: [] });
    expect(body.deployed.bot).toMatchObject({ version: '2.1.5' });
    expect(typeof body.deployed.server.version).toBe('string');
    expect(body.players.map((p: any) => p.name)).toEqual(['Aliias', 'Gub', 'Paul']);   // by display_name
    expect(body.players.find((p: any) => p.name === 'Paul').app_version).toBe('2.1.0');
    expect(body.players.find((p: any) => p.name === 'Aliias').app_version).toBeNull();
  });

  it('reports bot:null when no service_status row exists', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    const app = createApp(db, new EventHub(), undefined, { latest: fakeLatest });
    const body = await (await app.request('/v1/version')).json();
    expect(body.deployed.bot).toBeNull();
    expect(body.players).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix pi test -- version`
Expected: FAIL — `createApp` has no 4th arg / `/v1/version` 404s (or the import of `./version` fails).

- [ ] **Step 3: Write the route**

Create `pi/src/api/version.ts`:

```ts
import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { activeSeasonId } from '../db/seasons';
import { repoVersion } from '../version/repoVersion';
import { readService } from '../version/serviceStatus';
import { makeLatestFetcher, type LatestFn } from '../version/latest';

const BOOTED_AT = Date.now();   // module load ~ server boot

interface PlayerVersionRow { player_id: number; name: string; color: string | null; app_version: string | null; last_seen_at: number | null; }

/** GET /v1/version — public-but-unlisted diagnostic. Never throws: external lookups degrade to
 *  null + errors[], local DB reads always succeed. */
export function versionRoutes(db: DatabaseSync,
                              opts: { latest?: LatestFn; serverVersion?: string; bootedAt?: number } = {}): Hono<Env> {
  const serverVersion = opts.serverVersion ?? repoVersion();
  const latest = opts.latest ?? makeLatestFetcher();
  const bootedAt = opts.bootedAt ?? BOOTED_AT;
  const r = new Hono<Env>();
  r.get('/v1/version', async (c) => {
    const lv = await latest(c.req.query('fresh') === '1');
    const bot = readService(db, 'bot');
    const players = db.prepare(
      `SELECT p.id AS player_id, p.display_name AS name, p.color, p.app_version, p.last_seen_at
       FROM season_rosters sr JOIN players p ON p.id = sr.player_id
       WHERE sr.season_id = ?
       ORDER BY p.display_name`
    ).all(activeSeasonId(db)) as PlayerVersionRow[];
    return c.json({
      latest: { tag: lv.tag, app: lv.app, fetched_at: lv.fetched_at, errors: lv.errors },
      deployed: {
        server: { version: serverVersion, booted_at: bootedAt },
        bot: bot ? { version: bot.version, booted_at: bot.booted_at } : null,
      },
      players,
    });
  });
  return r;
}
```

- [ ] **Step 4: Wire it into `createApp`**

In `pi/src/api/app.ts`:

Add after the `import { readsRoutes } from './reads';` line (line 8):

```ts
import { versionRoutes } from './version';
import type { LatestFn } from '../version/latest';
```

Change the `createApp` signature (lines 21-22) to add the optional `opts`:

```ts
export function createApp(db: DatabaseSync, hub: EventHub,
                          invalidateModel?: (courseId: number) => void,
                          opts?: { latest?: LatestFn }): Hono<Env> {
```

Add `'/v1/version'` to the `PUBLIC_READS` array (line 32):

```ts
  const PUBLIC_READS = ['/v1/leaderboard', '/v1/world-records', '/v1/roster', '/v1/territory', '/v1/territory/timeline', '/v1/version'];
```

Add the mount after `app.route('/', readsRoutes(db));` (line 39):

```ts
  app.route('/', versionRoutes(db, { latest: opts?.latest }));
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm --prefix pi test -- version`
Expected: PASS.

- [ ] **Step 6: Run the broader api suite to confirm nothing regressed**

Run: `npm --prefix pi test -- api`
Expected: PASS (reads/app/auth/etc. unaffected).

- [ ] **Step 7: Commit**

```bash
git add pi/src/api/version.ts pi/src/api/app.ts pi/src/api/version.test.ts
git commit -m "feat(version): public GET /v1/version endpoint"
```

---

### Task 7: Bot self-reports its version on boot

**Files:**
- Modify: `pi/src/bot/index.ts:11-13` (after the `openDb` line)

**Interfaces:**
- Consumes: `reportService` (Task 3), `repoVersion` (Task 4).
- Produces: a `service_status` row with `service='bot'` written on every bot start.

> No new unit test: `bot/index.ts` performs Discord login + command install on import, so it can't be imported in a test. The written logic is already covered by Task 3 (`reportService`) and Task 6 (the route reads it). Verification is the existing bot suite staying green + the one-line wiring below.

- [ ] **Step 1: Add the import**

In `pi/src/bot/index.ts`, add after the existing imports (after `import { announceMissedPbs } from './catchup';`, line 9):

```ts
import { reportService } from '../version/serviceStatus';
import { repoVersion } from '../version/repoVersion';
```

- [ ] **Step 2: Write the self-report**

In `pi/src/bot/index.ts`, immediately after the `const db = openDb(cfg.dbPath);` line (line 12), add:

```ts
// Self-report our deployed version so /v1/version can show the bot's running build (a separate
// process the server can't otherwise see). This is the bot's one allowed write to the shared DB.
try { reportService(db, 'bot', repoVersion(), Date.now()); }
catch (err) { console.error('[bot] version self-report failed', err); }
```

- [ ] **Step 3: Verify the bot suite still passes**

Run: `npm --prefix pi test -- bot`
Expected: PASS (no behavioral change to the tested units).

- [ ] **Step 4: Commit**

```bash
git add pi/src/bot/index.ts
git commit -m "feat(version): bot self-reports its deployed version on boot"
```

---

### Task 8: Desktop app sends its `app_version` in the presence frame

**Files:**
- Modify: `src/lib/stores.js:32-33` (add the store)
- Modify: `src/App.svelte:1348` (set the store)
- Modify: `src/lib/presence.js:5` (import), `:150-159` (`frame()` return)
- Test: `src/lib/presence.test.js:2` (import), `:17-22` (update assertion), + new case

**Interfaces:**
- Consumes: Tauri `getVersion()` (already read in `App.svelte`).
- Produces: `frame()` includes `app_version: <string> | null`, fed to the server's `PresenceFrame` (Task 2).

- [ ] **Step 1: Update the failing test**

In `src/lib/presence.test.js`, add `appVersion` to the stores import (line 2):

```js
import { screen, selection, race, minimap, presence, serverConnection, myPlayerId, pbSplits, pbTotalMs, appVersion } from "./stores.js";
```

Update the first test's expected object (lines 17-22) to include `app_version: null`:

```js
    expect(frame()).toEqual({
      screen: "RACING", course: "Bowsers Castle", character: "Mario", kart: "Std", costume: "Base",
      cur_lap: 2, tot_lap: 3, coins: 7, mushrooms: 1, pos: [12, 34], final_time: null, resets: 4,
      track_state: "tracking", elapsed_ms: 5000, splits_ms: null, dnf: false,
      invalidated: false, invalid_reason: null, app_version: null,
    });
```

Add a new test right after that first `it(...)` block (after line 23):

```js
  it("carries the app version from the appVersion store", () => {
    appVersion.set("2.1.0");
    expect(frame().app_version).toBe("2.1.0");
    appVersion.set("");
    expect(frame().app_version).toBeNull();
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test:js -- presence`
Expected: FAIL — `appVersion` is not exported / `frame()` has no `app_version`.

- [ ] **Step 3: Add the store**

In `src/lib/stores.js`, add after the `serverConnection` export (end of file):

```js
export const appVersion    = writable("");   // this desktop build's version (Tauri getVersion); sent in the presence frame
```

- [ ] **Step 4: Send it from `frame()`**

In `src/lib/presence.js`, add `appVersion` to the stores import (line 5):

```js
import { screen, selection, race, minimap, presence, myPlayerId, serverConnection, pbSplits, pbTotalMs, appVersion } from "./stores.js";
```

Add the field to the object returned by `frame()` (after the `invalidated: ..., invalid_reason: ...` line, ~line 157):

```js
    app_version: get(appVersion) || null,
```

- [ ] **Step 5: Set the store from the Tauri version in `App.svelte`**

In `src/App.svelte`, add a dedicated import after the `import { t } from "./translations.js";` line (line 9). A separate import from `./lib/stores.js` is valid ESM even if other stores are imported elsewhere in the file:

```js
  import { appVersion } from "./lib/stores.js";
```

Then in `onMount`, immediately after the `version=await getVersion();` line (line 1348), add:

```js
    appVersion.set(version);   // expose to the presence frame so the server records our last-ran build
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm run test:js -- presence`
Expected: PASS.

- [ ] **Step 7: Type-check the desktop frontend**

Run: `npm run check`
Expected: 0 errors (the `appVersion` import resolves).

- [ ] **Step 8: Commit**

```bash
git add src/lib/stores.js src/lib/presence.js src/App.svelte src/lib/presence.test.js
git commit -m "feat(version): desktop app reports its version via presence"
```

---

### Task 9: `web/src/lib/version.js` — pure display helpers

**Files:**
- Create: `web/src/lib/version.js`
- Test: `web/src/lib/version.test.js`

**Interfaces:**
- Produces:
  - `SITE_VERSION: string` (the build-time `__SITE_VERSION__`, or `"dev"`).
  - `parseSemver(v)`, `compareSemver(a,b)` (-1/0/1 or `null` when either is unparseable).
  - `status(deployed, latest): "current" | "behind" | "ahead" | "unknown"`.
  - `formatLastSeen(lastSeenAt, now): string`.
  - `componentRows(payload, siteVersion): [{ key, label, latest, deployed, summary?, status }]` (rows: app, server, bot, site).
  - `playerRows(payload, now): [{ player_id, name, color, app_version, last_seen, status }]`.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/version.test.js`:

```js
import { describe, it, expect } from "vitest";
import { compareSemver, status, formatLastSeen, componentRows, playerRows } from "./version.js";

describe("compareSemver / status", () => {
  it("compares numerically and tolerates a leading v; null when unparseable", () => {
    expect(compareSemver("2.2.0", "2.10.0")).toBe(-1);
    expect(compareSemver("v2.1.0", "2.1.0")).toBe(0);
    expect(compareSemver("dev", "2.1.0")).toBeNull();
  });
  it("maps a comparison to a status word", () => {
    expect(status("2.1.0", "2.1.0")).toBe("current");
    expect(status("2.0.0", "2.1.0")).toBe("behind");
    expect(status("2.2.0", "2.1.0")).toBe("ahead");
    expect(status(null, "2.1.0")).toBe("unknown");
    expect(status("2.1.0", null)).toBe("unknown");
  });
});

describe("formatLastSeen", () => {
  it("renders online / relative / never", () => {
    const now = 1_000_000_000;
    expect(formatLastSeen(now - 5_000, now)).toBe("online");      // < 60s
    expect(formatLastSeen(now - 5 * 60_000, now)).toBe("5m ago");
    expect(formatLastSeen(now - 3 * 3_600_000, now)).toBe("3h ago");
    expect(formatLastSeen(now - 2 * 86_400_000, now)).toBe("2d ago");
    expect(formatLastSeen(null, now)).toBe("never");
  });
});

const payload = {
  latest: { tag: "2.1.5", app: "2.1.0", fetched_at: 0, errors: [] },
  deployed: { server: { version: "2.1.5", booted_at: 0 }, bot: { version: "2.1.4", booted_at: 0 } },
  players: [
    { player_id: 1, name: "Paul", color: "#a78bfa", app_version: "2.1.0", last_seen_at: 1_000_000_000 },
    { player_id: 2, name: "Gub", color: "#38bdf8", app_version: "2.0.0", last_seen_at: 900_000_000 },
    { player_id: 3, name: "Aliias", color: null, app_version: null, last_seen_at: null },
  ],
};

describe("componentRows", () => {
  it("builds app/server/bot/site rows with statuses; site uses the passed bundle version", () => {
    const rows = componentRows(payload, "2.1.5");
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by.server.status).toBe("current");
    expect(by.bot.status).toBe("behind");           // 2.1.4 < 2.1.5
    expect(by.site).toMatchObject({ deployed: "2.1.5", status: "current" });
    expect(by.app).toMatchObject({ latest: "2.1.0", summary: "1/2 on latest" });  // Paul on, Gub off, Aliias unreported
  });
});

describe("playerRows", () => {
  it("maps each player to installed version, last-seen, and status", () => {
    const rows = playerRows(payload, 1_000_000_000);
    expect(rows.find((r) => r.name === "Paul")).toMatchObject({ app_version: "2.1.0", last_seen: "online", status: "current" });
    expect(rows.find((r) => r.name === "Gub").status).toBe("behind");
    expect(rows.find((r) => r.name === "Aliias")).toMatchObject({ app_version: null, last_seen: "never", status: "unknown" });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix web test -- version`
Expected: FAIL — `Cannot find module './version.js'`.

- [ ] **Step 3: Write the implementation**

Create `web/src/lib/version.js`:

```js
// Pure helpers for the unlisted #/version page. SITE_VERSION is the deployed site build, baked
// in by vite (web/vite.config.js define); the typeof guard keeps vitest (no define) from throwing.
export const SITE_VERSION = typeof __SITE_VERSION__ !== "undefined" ? __SITE_VERSION__ : "dev";

export function parseSemver(v) {
  if (typeof v !== "string") return null;
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(v.trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** -1/0/1, or null when either side is unparseable (caller renders "unknown"). */
export function compareSemver(a, b) {
  const pa = parseSemver(a), pb = parseSemver(b);
  if (!pa || !pb) return null;
  for (let i = 0; i < 3; i++) if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  return 0;
}

export function status(deployed, latest) {
  const cmp = compareSemver(deployed, latest);
  if (cmp === null) return "unknown";
  if (cmp < 0) return "behind";
  if (cmp > 0) return "ahead";
  return "current";
}

export function formatLastSeen(lastSeenAt, now) {
  if (!lastSeenAt) return "never";
  const d = now - lastSeenAt;
  if (d < 60_000) return "online";
  if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
  return `${Math.floor(d / 86_400_000)}d ago`;
}

/** Rows for the components table. The app row carries an "N/M on latest" summary instead of a
 *  single deployed version (it's per-player). server/bot/site compare deployed vs the latest tag. */
export function componentRows(payload, siteVersion) {
  const tag = payload?.latest?.tag ?? null;
  const app = payload?.latest?.app ?? null;
  const server = payload?.deployed?.server?.version ?? null;
  const bot = payload?.deployed?.bot?.version ?? null;
  const players = payload?.players ?? [];
  const reported = players.filter((p) => p.app_version);
  const onLatest = reported.filter((p) => compareSemver(p.app_version, app) === 0);
  return [
    { key: "app", label: "pbenguin app", latest: app, deployed: null,
      summary: app ? `${onLatest.length}/${reported.length} on latest` : "—", status: "na" },
    { key: "server", label: "server", latest: tag, deployed: server, status: status(server, tag) },
    { key: "bot", label: "bot", latest: tag, deployed: bot, status: status(bot, tag) },
    { key: "site", label: "site", latest: tag, deployed: siteVersion, status: status(siteVersion, tag) },
  ];
}

export function playerRows(payload, now) {
  const app = payload?.latest?.app ?? null;
  return (payload?.players ?? []).map((p) => ({
    player_id: p.player_id, name: p.name, color: p.color ?? null,
    app_version: p.app_version ?? null,
    last_seen: formatLastSeen(p.last_seen_at, now),
    status: p.app_version ? status(p.app_version, app) : "unknown",
  }));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix web test -- version`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/version.js web/src/lib/version.test.js
git commit -m "feat(version): web version display helpers"
```

---

### Task 10: web routing + API URL + site-version define

**Files:**
- Modify: `web/src/lib/view.js`
- Modify: `web/src/lib/api.js`
- Modify: `web/vite.config.js`
- Test: `web/src/lib/view.test.js`

**Interfaces:**
- Produces: `viewFromHash("#/version") === "version"`; `versionUrl()` → `${API_BASE}/v1/version`; a `__SITE_VERSION__` global defined at build/test time.

- [ ] **Step 1: Write the failing test**

Add to `web/src/lib/view.test.js`, inside the `describe("viewFromHash", ...)` block:

```js
  it("returns version for the unlisted version hash", () => {
    expect(viewFromHash("#/version")).toBe("version");
    expect(viewFromHash("#version")).toBe("version");
  });
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix web test -- view`
Expected: FAIL — `viewFromHash("#/version")` returns `"live"`.

- [ ] **Step 3: Add the route**

In `web/src/lib/view.js`, add after the `if (h === "heat") return "heat";` line:

```js
  if (h === "version") return "version";   // unlisted, URL-only (no navbar tab)
```

- [ ] **Step 4: Add the API URL helper**

In `web/src/lib/api.js`, add after the `territoryTimelineUrl` export:

```js
export const versionUrl = () => `${API_BASE}/v1/version`;
```

- [ ] **Step 5: Define `__SITE_VERSION__` at build time**

Replace the contents of `web/vite.config.js` with (adds the import + `define`, keeps the existing config):

```js
import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The deployed site version = the root package.json version (the Pi builds web/ from the same
// tagged clone). Baked in so the #/version page can report its own bundle's build.
const pkg = JSON.parse(readFileSync(fileURLToPath(new URL("../package.json", import.meta.url)), "utf8"));

// Standalone public website (no Tauri). Reuses the desktop card components from ../src,
// so the dev server must be allowed to read one directory up. `vite build` (used on the
// Pi) follows imports anywhere and is unaffected. outDir defaults to dist -> web/dist.
export default defineConfig({
  plugins: [svelte()],
  define: { __SITE_VERSION__: JSON.stringify(pkg.version) },
  server: { port: 1430, strictPort: true, fs: { allow: [".."] } },
  test: { include: ["**/*.test.js"] },
  build: { target: "chrome105" },
});
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm --prefix web test -- view`
Expected: PASS.

Run: `npm --prefix web test`
Expected: PASS (full web suite, incl. version.js from Task 9, unaffected by the `define`).

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/view.js web/src/lib/api.js web/vite.config.js web/src/lib/view.test.js
git commit -m "feat(version): web #/version route, API url, and site-version define"
```

---

### Task 11: `VersionPage.svelte` + dispatch

**Files:**
- Create: `web/src/VersionPage.svelte`
- Modify: `web/src/App.svelte:9` (import), `:58-62` (the view dispatch)

**Interfaces:**
- Consumes: `versionUrl` (Task 10), `SITE_VERSION` / `componentRows` / `playerRows` (Task 9).
- Produces: the rendered `#/version` page. Presentation only — no new unit test (logic is tested in Task 9); verification is `svelte-check` + `vite build`.

- [ ] **Step 1: Create the page**

Create `web/src/VersionPage.svelte`:

```svelte
<script>
  import { onMount } from "svelte";
  import { versionUrl } from "./lib/api.js";
  import { SITE_VERSION, componentRows, playerRows } from "./lib/version.js";

  let payload = null, loaded = false, error = false;
  let comps = [], players = [];

  onMount(async () => {
    try {
      const res = await fetch(versionUrl(), { cache: "no-store" });
      if (!res.ok) throw new Error(`version ${res.status}`);
      payload = await res.json();
      comps = componentRows(payload, SITE_VERSION);
      players = playerRows(payload, Date.now());
      loaded = true;
    } catch (e) {
      console.error("version load failed", e);
      error = true;
    }
  });

  const dot = (s) => (s === "current" ? "✓" : s === "behind" ? "⚠" : s === "ahead" ? "dev" : "?");
</script>

<section class="ver">
  <h2>versions</h2>
  {#if error}
    <p class="msg">Couldn't load version data.</p>
  {:else if !loaded}
    <p class="msg">Loading…</p>
  {:else}
    <table>
      <thead><tr><th>Component</th><th>Latest</th><th>Deployed</th><th class="sth"></th></tr></thead>
      <tbody>
        {#each comps as c}
          <tr>
            <td>{c.label}</td>
            <td class="mono">{c.latest ?? "?"}</td>
            <td class="mono">{c.key === "app" ? c.summary : (c.deployed ?? "?")}</td>
            <td class="st {c.status}">{c.key === "app" ? "" : dot(c.status)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    <h3>players — installed app (last ran)</h3>
    <table>
      <thead><tr><th>Player</th><th>Installed</th><th>Last seen</th><th class="sth"></th></tr></thead>
      <tbody>
        {#each players as p}
          <tr>
            <td><span class="swatch" style="background:{p.color || '#555'}"></span>{p.name}</td>
            <td class="mono">{p.app_version ?? "—"}</td>
            <td class="seen">{p.last_seen}</td>
            <td class="st {p.status}">{dot(p.status)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if payload?.latest?.errors?.length}
      <p class="errs">latest-version lookup issues: {payload.latest.errors.join("; ")}</p>
    {/if}
  {/if}
</section>

<style>
  .ver { max-width: 760px; margin: 0 auto; padding: 22px 24px; color: #c7ccd2;
         font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif; }
  h2 { color: #e8eaed; font-size: 18px; margin: 0 0 12px; }
  h3 { color: #cfd3d8; font-size: 13px; margin: 22px 0 6px; font-weight: 600; }
  .msg { color: #8a8f98; font-size: 13px; padding: 24px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #7a818b; font-weight: 600; font-size: 11px; text-transform: uppercase;
       letter-spacing: .08em; padding: 4px 10px; border-bottom: 1px solid #23262b; }
  th.sth { width: 34px; }
  td { padding: 6px 10px; border-bottom: 1px solid #181b1f; }
  .mono { font-family: ui-monospace, Menlo, monospace; font-variant-numeric: tabular-nums; color: #e8eaed; }
  .seen { color: #8a8f98; }
  .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; vertical-align: middle; }
  .st { text-align: center; font-weight: 700; }
  .st.current { color: #4ade80; }
  .st.behind { color: #fbbf24; }
  .st.ahead { color: #7a818b; font-weight: 600; font-size: 11px; }
  .st.unknown { color: #6f7782; }
  .errs { margin-top: 14px; font-size: 11px; color: #7a818b; }
</style>
```

- [ ] **Step 2: Wire it into the router**

In `web/src/App.svelte`, add the import after `import HeatGraph from "./HeatGraph.svelte";` (line 9):

```js
  import VersionPage from "./VersionPage.svelte";
```

Add the dispatch branch in `<main>` (after the `{:else if view === "heat"}<HeatGraph />` line, line 60):

```svelte
  {:else if view === "version"}<VersionPage />
```

- [ ] **Step 3: Type-check + build the web app**

Run: `npm --prefix web run check`
Expected: 0 errors / 0 warnings.

Run: `npm --prefix web run build`
Expected: build succeeds (the `__SITE_VERSION__` define resolves; no unresolved-global error).

- [ ] **Step 4: Commit**

```bash
git add web/src/VersionPage.svelte web/src/App.svelte
git commit -m "feat(version): add the unlisted #/version page"
```

---

## Final verification

- [ ] **Run every affected suite:**
  - `npm --prefix pi test` (expect all green; new files: connect, hub, serviceStatus, repoVersion, latest, version)
  - `npm --prefix web test` (expect all green; new: version, updated: view)
  - `npm run test:js -- presence` (expect green; updated frame() assertions)
  - `npm run check` and `npm --prefix web run check` (expect 0 errors)
  - `npm --prefix web run build` (expect success)

- [ ] **Manual smoke (after the Pi has the change + a restart, and at least one app reports in):**
  visit `https://thekartoff.com/#/version` — components table shows latest vs deployed for app/server/bot/site, and the players table shows each roster member's last-ran app version + last-seen.

## Deploy notes (not code tasks)

- The server makes outbound calls to `api.github.com` (tags) and the `latest.json` release asset; the Pi already reaches the internet (WR scraper), so no firewall change is expected. Optionally set `GITHUB_TOKEN` in `/etc/mkw/mkw.env` to raise the API rate limit.
- The repo slug + manifest auto-resolve from `src-tauri/tauri.conf.json`; override with `MKW_RELEASE_REPO` / `MKW_UPDATER_MANIFEST` if needed.
- Players only start reporting `app_version` once they run a build that includes Task 8, so existing roster members show `—` until their next app launch with the new version.
