# Pi server/bot auto-deploy + tunnel + migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the `pi/` server + Discord bot to the Raspberry Pi as auto-updating systemd services reachable off-network through a Cloudflare tunnel on `api.thekartoff.com`, with token-gated reads+writes and the legacy PB/WR + body-stat data migrated in.

**Architecture:** A pull-based updater (systemd timer) tracks GitHub release tags over a read-only deploy key and restarts services on change. Three system units run server/bot/updater on boot with `Restart=always`. One `app.use` in the server gates all HTTP reads+writes behind a token (header or `?token=`), leaving `/health` + the two WS streams open. A one-time Python import loads `hogkart.db`; body stats are read in place from `botdata.db`. A runbook (`docs/pi-deploy.md`) covers the manual, outward-facing steps.

**Tech Stack:** Node 24 + `tsx` + `node:sqlite` (no build step), Hono, systemd, bash, Cloudflare Tunnel (`cloudflared`), Python 3 stdlib (`server/importer.py`), Rust/reqwest (`src-tauri`), vitest.

**Branch:** `pi-server-deploy` (already created; the spec is committed there).

**Ordering note:** Tasks 1–5 (the server token-gate + the desktop read calls that must send the token) ship together in this branch so the monitor never sees a 401 mid-release. Tasks 6–11 (deploy artifacts + runbook) are independent of the code change.

---

## Task 1: `requireTokenAny` middleware (header-or-query auth)

**Files:**
- Modify: `pi/src/api/auth.ts`
- Test: `pi/src/api/auth.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `pi/src/api/auth.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { Hono } from 'hono';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { requireTokenAny } from './auth';
import type { Env } from './app';

function gated() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  const token = mintToken(db, 'Paul');
  const app = new Hono<Env>();
  app.use('*', requireTokenAny(db));
  app.get('/x', (c) => c.json({ ok: true, me: c.get('playerName') }));
  return { app, token };
}

describe('requireTokenAny', () => {
  it('401s without a token', async () => {
    expect((await gated().app.request('/x')).status).toBe(401);
  });
  it('accepts a Bearer header and sets the player', async () => {
    const { app, token } = gated();
    const res = await app.request('/x', { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    expect((await res.json()).me).toBe('Paul');
  });
  it('accepts a ?token= query param', async () => {
    const { app, token } = gated();
    expect((await app.request(`/x?token=${token}`)).status).toBe(200);
  });
  it('401s on a bad token', async () => {
    expect((await gated().app.request('/x?token=nope')).status).toBe(401);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run (from `pi/`): `npm test -- src/api/auth.test.ts`
Expected: FAIL — `requireTokenAny` is not exported from `./auth`.

- [ ] **Step 3: Implement the middleware**

Replace the entire contents of `pi/src/api/auth.ts` with:

```ts
import type { Context, Next } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { playerByToken } from '../db/players';

/** Token from the Authorization: Bearer header only. */
function bearerToken(c: Context): string | null {
  const m = /^Bearer (.+)$/.exec(c.req.header('authorization') ?? '');
  return m ? m[1] : null;
}

/** Token from the Bearer header, else a ?token= query param (browsers / WebSocket clients
 *  can't set an Authorization header). */
export function tokenFromRequest(c: Context): string | null {
  return bearerToken(c) ?? c.req.query('token') ?? null;
}

function gate(db: DatabaseSync, extract: (c: Context) => string | null) {
  return async (c: Context, next: Next) => {
    const tok = extract(c);
    const player = tok ? playerByToken(db, tok) : null;
    if (!player) return c.json({ error: 'unauthorized' }, 401);
    c.set('playerId', player.id);
    c.set('playerName', player.display_name);
    await next();
  };
}

/** Header-only auth — for writes (a ?token= in a write URL would leak in logs). */
export function requireToken(db: DatabaseSync) { return gate(db, bearerToken); }

/** Header-or-query auth — for reads (lets a browser / WS client pass ?token=). */
export function requireTokenAny(db: DatabaseSync) { return gate(db, tokenFromRequest); }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run (from `pi/`): `npm test -- src/api/auth.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Confirm the existing write tests still pass (requireToken behaviour unchanged)**

Run (from `pi/`): `npm test -- src/api/app.test.ts src/api/screen.test.ts`
Expected: PASS (the refactor keeps `requireToken` header-only).

- [ ] **Step 6: Commit**

```bash
git add pi/src/api/auth.ts pi/src/api/auth.test.ts
git commit -m "feat(server): requireTokenAny (header-or-query) auth middleware"
```

---

## Task 2: Gate all HTTP reads/writes behind a token

Adds one `app.use` in `createApp` that requires a token for every HTTP route except `/health` and the two WS streams. This makes the existing read tests fail (reads were open); we update them in the same task so the suite stays green per commit.

**Files:**
- Modify: `pi/src/api/app.ts:19-32`
- Modify: `pi/src/api/app.test.ts`
- Modify: `pi/src/api/reads.test.ts`
- Modify: `pi/src/api/screen.test.ts`

- [ ] **Step 1: Update `app.test.ts` to assert the new gated behaviour**

In `pi/src/api/app.test.ts`, add this `describe` block after the existing `describe('app skeleton', …)` block (the file's `appWith()` already returns `{ app, token }`):

```ts
describe('reads need a token', () => {
  it('a read 401s with no token, 200s with a header or a ?token= query', async () => {
    const { app, token } = appWith();
    expect((await app.request('/v1/seasons')).status).toBe(401);
    expect((await app.request('/v1/seasons', { headers: { authorization: `Bearer ${token}` } })).status).toBe(200);
    expect((await app.request(`/v1/seasons?token=${token}`)).status).toBe(200);
  });
  it('GET /health stays public', async () => {
    expect((await appWith().app.request('/health')).status).toBe(200);
  });
});
```

- [ ] **Step 2: Rewrite `reads.test.ts` for gated reads**

Replace the entire contents of `pi/src/api/reads.test.ts` with:

```ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { mintToken } from '../db/players';

function appWith() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1)");
  return { app: createApp(db, new EventHub()), token: mintToken(db, 'Paul') };
}

const auth = (token: string) => ({ headers: { authorization: `Bearer ${token}` } });

describe('reads require a token', () => {
  it('GET /v1/leaderboard 401s without one, returns rows with one', async () => {
    const { app, token } = appWith();
    expect((await app.request('/v1/leaderboard?course=Rainbow%20Road&cc=150')).status).toBe(401);
    const res = await app.request('/v1/leaderboard?course=Rainbow%20Road&cc=150', auth(token));
    expect(res.status).toBe(200);
    expect((await res.json())[0].display_name).toBe('Paul');
  });
  it('GET /v1/seasons accepts a ?token= query param', async () => {
    const { app, token } = appWith();
    expect((await app.request('/v1/seasons')).status).toBe(401);
    const res = await app.request(`/v1/seasons?token=${token}`);
    expect(res.status).toBe(200);
    expect((await res.json()).length).toBe(1);
  });
});

describe('GET /v1/me/pbs (token)', () => {
  it('401s without a token, returns the caller\'s PBs with one', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',108000,1)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    expect((await app.request('/v1/me/pbs')).status).toBe(401);
    const res = await app.request('/v1/me/pbs', auth(token));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([{ course_slug: 'rainbow_road', cc: 150, total_time_ms: 108000 }]);
  });
});

function trailsDb() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1),(20,1,2,1,150,'finished','live',112000,1)");
  db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (10,1,36000),(10,2,72000),(10,3,108000)");
  db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,100,200,0.9),(20,0,300,400,0.8)");
  return db;
}

describe('GET /v1/me/pb-splits (token)', () => {
  it('401s without a token; returns total + splits with one; 400 on unknown course', async () => {
    const db = trailsDb();
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    expect((await app.request('/v1/me/pb-splits?course=Rainbow%20Road')).status).toBe(401);
    const res = await app.request('/v1/me/pb-splits?course=Rainbow%20Road', auth(token));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ total_ms: 108000, splits: { 1: 36000, 2: 72000, 3: 108000 } });
    expect((await app.request('/v1/me/pb-splits?course=nope', auth(token))).status).toBe(400);
  });
});

describe('GET /v1/trails (token; is_me for the owner)', () => {
  it('401 without a token; returns roster trails with is_me; 400 on unknown course', async () => {
    const db = trailsDb();
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    expect((await app.request('/v1/trails?course=Rainbow%20Road')).status).toBe(401);
    const mine = await (await app.request('/v1/trails?course=Rainbow%20Road', auth(token))).json();
    expect(mine.map((t: any) => t.player)).toEqual(['Paul', 'Luke']);
    expect(mine.find((t: any) => t.player === 'Paul').is_me).toBe(true);
    expect(mine.find((t: any) => t.player === 'Luke').is_me).toBe(false);
    expect((await app.request('/v1/trails?course=nope', auth(token))).status).toBe(400);
  });
});

describe('GET /v1/roster (token)', () => {
  it('401 without a token; lists the season roster; is_me flags the holder', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
    db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    expect((await app.request('/v1/roster')).status).toBe(401);
    const mine = await (await app.request('/v1/roster', auth(token))).json();
    expect(mine.find((r: any) => r.display_name === 'Paul').is_me).toBe(true);
    expect(mine.find((r: any) => r.display_name === 'Luke').is_me).toBe(false);
  });
});

describe('GET /v1/players/:id/trails (token)', () => {
  it('401 without a token; returns the player trails by mode; 400 on unknown course', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,started_at,ended_at,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,'a','a1',1),(20,1,1,1,150,'finished','live',110000,'b','b1',0)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,1,1,0.9),(20,0,2,2,0.9)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    expect((await app.request('/v1/players/1/trails?course=Rainbow%20Road&mode=pbs')).status).toBe(401);
    const last = await (await app.request('/v1/players/1/trails?course=Rainbow%20Road&mode=last&n=5', auth(token))).json();
    expect(last.map((r: any) => r.run_id)).toEqual([20, 10]);
    const pbs = await (await app.request('/v1/players/1/trails?course=Rainbow%20Road&mode=pbs', auth(token))).json();
    expect(pbs.map((r: any) => r.run_id)).toEqual([10]);
    expect((await app.request('/v1/players/1/trails?course=nope&mode=pbs', auth(token))).status).toBe(400);
  });
});
```

- [ ] **Step 3: Update `screen.test.ts` — token on the stats reads + explorer**

In `pi/src/api/screen.test.ts`, the first test already mints `token` (header on the POST). Add `&token=${token}` to its three stats reads:

```ts
    const v = await app.request(`/v1/stats/value?metric=screen_time&screen=MAIN_MENU&period=all_time&token=${token}`);
    expect((await v.json()).value).toBe(3000);

    const bd = await app.request(`/v1/stats/breakdown?metric=screen_time&group_by=screen&period=all_time&token=${token}`);
    const rows = (await bd.json()).rows as { key: string; value: number }[];
    expect(Object.fromEntries(rows.map((r) => [r.key, r.value]))).toEqual({ MAIN_MENU: 3000, RACING: 5000 });

    const cat = await (await app.request(`/v1/stats/metrics?token=${token}`)).json();
```

Then update the `explorer` describe to pass a token:

```ts
describe('explorer', () => {
  it('serves the stat-explorer page at /explorer with a token', async () => {
    const d = db();
    const token = mintToken(d, 'Luke');
    const app = createApp(d, new EventHub());
    expect((await app.request('/explorer')).status).toBe(401);
    const res = await app.request(`/explorer?token=${token}`);
    expect(res.status).toBe(200);
    expect(await res.text()).toContain('MKW Broadcast Stats');
  });
});
```

- [ ] **Step 4: Run the read/screen/app tests to confirm they now FAIL (gate not added yet)**

Run (from `pi/`): `npm test -- src/api/reads.test.ts src/api/screen.test.ts src/api/app.test.ts`
Expected: FAIL — the "401 without a token" assertions fail because reads are still open (return 200).

- [ ] **Step 5: Add the gate in `createApp`**

In `pi/src/api/app.ts`, add the import near the other route imports:

```ts
import { requireTokenAny } from './auth';
```

Then in `createApp`, insert the gate immediately after the `/health` route and before the first `app.route(...)`:

```ts
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  // Every HTTP route except /health and the two WebSocket streams needs a token (read or write).
  // /v1/events stays open: the on-Pi bot subscribes to it over localhost with no token, and it
  // only carries PB/WR events that are already announced publicly. /v1/presence keeps its own
  // optional-token (receive-only) model.
  const OPEN = new Set(['/health', '/v1/events', '/v1/presence']);
  app.use('*', (c, next) => (OPEN.has(c.req.path) ? next() : requireTokenAny(db)(c, next)));
  app.route('/', runsRoutes(db, hub, invalidateModel));
```

(The rest of `createApp` is unchanged.)

- [ ] **Step 6: Run the full server test suite to confirm green**

Run (from `pi/`): `npm test`
Expected: PASS — all suites, including `ws.test.ts` (the `/v1/events` socket is exempt) and `stats.test.ts` (builds `createStatsApp` directly, bypassing the gate).

- [ ] **Step 7: Commit**

```bash
git add pi/src/api/app.ts pi/src/api/app.test.ts pi/src/api/reads.test.ts pi/src/api/screen.test.ts
git commit -m "feat(server): require a token for all HTTP reads + writes (events/presence WS stay open)"
```

---

## Task 3: Desktop — send the token on friends-PBs and player-trails reads

Gating reads would 401 the monitor's friends/trails reads, which currently send no token. Add `bearer_auth`.

**Files:**
- Modify: `src-tauri/src/sync.rs:604` and `src-tauri/src/sync.rs:609-613`

- [ ] **Step 1: Add the token to the friends-pbs read**

In `src-tauri/src/sync.rs`, in `fetch_course_reads`, change the `friends-pbs` line:

```rust
    let fp = get_json(client.get(format!("{base}/v1/friends-pbs")).query(&q).bearer_auth(&cfg.token), "friends-pbs").await?;
```

- [ ] **Step 2: Add the token to the player-trails read**

In the same function, change the player-trails `get_json` call to add `.bearer_auth(&cfg.token)`:

```rust
        let runs = get_json(
            client.get(format!("{base}/v1/players/{}/trails", p.player_id))
                .query(&[("course", course), ("cc", "150"), ("mode", p.mode.as_str()), ("n", n.as_str())])
                .bearer_auth(&cfg.token),
            "player-trails",
        ).await?;
```

- [ ] **Step 3: Confirm it compiles**

Run (from `src-tauri/`): `cargo check`
Expected: finishes with no errors (warnings ok). (`bearer_auth` is already used elsewhere in this file, so the API is in scope.)

- [ ] **Step 4: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "fix(sync): send the auth token on friends-pbs + player-trails reads"
```

> Manual check (later, on the dev box after deploy): with the token-gated server running, the monitor still renders friends' PBs and ghost trails (they fall back to cache on a 401, so a blank trails panel would signal a missing token).

---

## Task 4: Explorer page — carry the token from `?token=`

**Files:**
- Modify: `pi/stat-explorer.html` (the `load()` fetch, the `run()` fetch, and the notes line)

- [ ] **Step 1: Add a token-aware fetch helper**

In `pi/stat-explorer.html`, insert before the `// ── catalogue load + builder ──` comment:

```js
// ── auth: same-origin reads carry the token from ?token= in the page URL ──
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const authFetch = (u, o = {}) => fetch(u, TOKEN ? { ...o, headers: { ...(o.headers || {}), authorization: 'Bearer ' + TOKEN } } : o);

```

- [ ] **Step 2: Use it for the two gated reads**

Change the metrics fetch in `load()`:

```js
      authFetch('/v1/stats/metrics').then(r => r.json()),
```

Change the query fetch in `run()`:

```js
    const r = await authFetch(buildUrl());
```

(Leave the `/health` fetch as `fetch` — `/health` is open.)

- [ ] **Step 3: Update the notes line**

Change the notes paragraph text from `reads need no login` to:

```html
  <p><b>Notes</b> — reads need a token; open <code>/explorer?token=YOUR_TOKEN</code>. Body &amp; correlation need <code>porker.db</code>. Time-per-screen reads 0 until the desktop app forwards intervals. Live registry: <code>/v1/stats/metrics</code>.</p>
```

- [ ] **Step 4: Verify the edits landed**

Run: `grep -n "authFetch\|YOUR_TOKEN" pi/stat-explorer.html`
Expected: the helper definition + two `authFetch(` call sites + the notes line.

- [ ] **Step 5: Commit**

```bash
git add pi/stat-explorer.html
git commit -m "feat(explorer): pass ?token= through to the gated stats reads"
```

---

## Task 5: Env file template (`deploy/mkw.env.example`)

**Files:**
- Create: `deploy/mkw.env.example`

- [ ] **Step 1: Create the file**

```ini
# Copy to /etc/mkw/mkw.env on the Pi and fill in the secrets. Absolute paths so the
# systemd WorkingDirectory is irrelevant. Data + state live OUTSIDE the git clone.
PORT=8787
MKW_DB=/home/pi/mkw-data/mkw.db
BOT_STATE=/home/pi/mkw-data/bot-state.json
STATS_PORKER_DB=/home/pi/porker-data/databases/botdata.db

# Discord bot (from the Discord developer portal). Required for the bot to start.
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
DISCORD_GUILD_ID=

# Optional overrides:
# BOT_WS_URL=ws://127.0.0.1:8787/v1/events
# MKWRS_URL=
# MKWRS_MIN_INTERVAL_SEC=900
# MKWRS_MAX_INTERVAL_SEC=1800
```

- [ ] **Step 2: Commit**

```bash
git add deploy/mkw.env.example
git commit -m "chore(deploy): env file template"
```

---

## Task 6: systemd units (`deploy/systemd/`)

**Files:**
- Create: `deploy/systemd/mkw-server.service`
- Create: `deploy/systemd/mkw-bot.service`
- Create: `deploy/systemd/mkw-updater.service`
- Create: `deploy/systemd/mkw-updater.timer`

- [ ] **Step 1: Create `mkw-server.service`**

```ini
[Unit]
Description=MKW server (Hono API + WebSocket + WR scraper)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/mkw/pi
EnvironmentFile=/etc/mkw/mkw.env
ExecStart=/usr/bin/node --no-warnings --import tsx src/server.ts
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create `mkw-bot.service`**

```ini
[Unit]
Description=MKW Discord bot (PB/WR announcements + slash commands)
After=mkw-server.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/mkw/pi
EnvironmentFile=/etc/mkw/mkw.env
ExecStart=/usr/bin/node --no-warnings --import tsx src/bot/index.ts
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create `mkw-updater.service`**

```ini
[Unit]
Description=MKW pull-deploy updater (checks GitHub for a newer release tag)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=pi
ExecStart=/home/pi/mkw/deploy/update.sh
```

- [ ] **Step 4: Create `mkw-updater.timer`**

```ini
[Unit]
Description=Run the MKW updater periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit=mkw-updater.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Sanity-check the unit syntax (key=value lines, [Section] headers)**

Run: `grep -L "\[Install\]" deploy/systemd/mkw-server.service deploy/systemd/mkw-bot.service`
Expected: empty output (both have an `[Install]` section). (`systemd-analyze verify` runs on the Pi — noted in the runbook.)

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd/
git commit -m "chore(deploy): systemd units for server, bot, and the updater timer"
```

---

## Task 7: Updater sudoers grant (`deploy/sudoers.d/mkw-updater`)

**Files:**
- Create: `deploy/sudoers.d/mkw-updater`

- [ ] **Step 1: Create the drop-in**

```sudoers
# Let the unprivileged deploy user restart only the MKW units, no password.
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart mkw-server mkw-bot, /usr/bin/systemctl restart mkw-server, /usr/bin/systemctl restart mkw-bot, /usr/bin/systemctl start mkw-server, /usr/bin/systemctl start mkw-bot
```

(Ensure the file ends with a trailing newline. It is validated with `visudo -cf` by `install.sh` before being placed in `/etc/sudoers.d/`.)

- [ ] **Step 2: Commit**

```bash
git add deploy/sudoers.d/mkw-updater
git commit -m "chore(deploy): least-privilege sudoers grant for the updater"
```

---

## Task 8: Pull-deploy updater (`deploy/update.sh`)

**Files:**
- Create: `deploy/update.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
# Pull-deploy: if GitHub has a newer release tag than we've deployed, check it out,
# install deps, and restart the services. Outbound-only (CGNAT-safe). Idempotent +
# fail-safe: on any error it aborts before writing the marker, so the running version
# stays up and the next timer tick retries.
set -euo pipefail

REPO="${MKW_REPO:-/home/pi/mkw}"
DATA="${MKW_DATA:-/home/pi/mkw-data}"
MARKER="$DATA/.deployed-tag"
KEY="${MKW_DEPLOY_KEY:-/home/pi/.ssh/mkw_deploy}"

mkdir -p "$DATA"
# Serialize: if a previous run is still going, skip this tick.
exec 9>"$DATA/.update.lock"
flock -n 9 || { echo "another update is in progress; skipping"; exit 0; }

cd "$REPO"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git fetch --tags --prune --quiet origin

latest="$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -n1)"
if [ -z "$latest" ]; then echo "no release tags yet; nothing to deploy"; exit 0; fi

current="$(cat "$MARKER" 2>/dev/null || true)"
if [ "$latest" = "$current" ]; then echo "already up to date ($current)"; exit 0; fi

echo "deploying $latest (was ${current:-none})"
git checkout -q --force "tags/$latest"
npm --prefix "$REPO/pi" install --no-audit --no-fund
sudo systemctl restart mkw-server mkw-bot
echo "$latest" > "$MARKER"
echo "deployed $latest"
```

- [ ] **Step 2: Syntax-check the script**

Run: `bash -n deploy/update.sh`
Expected: no output, exit 0 (valid bash).

- [ ] **Step 3: Mark it executable (recorded in git)**

```bash
git add deploy/update.sh
git update-index --chmod=+x deploy/update.sh
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(deploy): tag-tracking pull updater"
```

---

## Task 9: One-time bootstrap (`deploy/install.sh`)

**Files:**
- Create: `deploy/install.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
# One-time (idempotent) bootstrap of the MKW services on the Pi. Run with sudo, AFTER the
# repo is cloned, `npm --prefix pi install` has run, /etc/mkw/mkw.env is filled in, and the
# data has been migrated (see docs/pi-deploy.md). Starting the bot last keeps its announce
# watermark seeded over the already-imported PBs.
set -euo pipefail

REPO="${MKW_REPO:-/home/pi/mkw}"
DATA=/home/pi/mkw-data
ENVDIR=/etc/mkw

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

install -d -o pi -g pi "$DATA"
install -d "$ENVDIR"
if [ ! -f "$ENVDIR/mkw.env" ]; then
  install -m 0640 -o root -g pi "$REPO/deploy/mkw.env.example" "$ENVDIR/mkw.env"
  echo "seeded $ENVDIR/mkw.env from the example - fill in DISCORD_* before the bot will start"
fi

# sudoers drop-in (validate, then install read-only).
visudo -cf "$REPO/deploy/sudoers.d/mkw-updater"
install -m 0440 -o root -g root "$REPO/deploy/sudoers.d/mkw-updater" /etc/sudoers.d/mkw-updater

# systemd units.
install -m 0644 \
  "$REPO/deploy/systemd/mkw-server.service" \
  "$REPO/deploy/systemd/mkw-bot.service" \
  "$REPO/deploy/systemd/mkw-updater.service" \
  "$REPO/deploy/systemd/mkw-updater.timer" \
  /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now mkw-server.service mkw-bot.service mkw-updater.timer
echo "installed + started."
echo "check: systemctl status mkw-server mkw-bot; systemctl list-timers mkw-updater.timer"
```

- [ ] **Step 2: Syntax-check**

Run: `bash -n deploy/install.sh`
Expected: no output, exit 0.

- [ ] **Step 3: Mark executable + commit**

```bash
git add deploy/install.sh
git update-index --chmod=+x deploy/install.sh
git commit -m "feat(deploy): one-time service bootstrap script"
```

---

## Task 10: Setup + migration runbook (`docs/pi-deploy.md`)

**Files:**
- Create: `docs/pi-deploy.md`

- [ ] **Step 1: Create the runbook with this exact content**

````markdown
# Pi deployment + migration runbook

The new `pi/` server + Discord bot, running on `pi@192.168.1.21` as auto-updating systemd
services, reachable off-network at `https://api.thekartoff.com` through the existing Cloudflare
tunnel. Steady-state updates: you `git tag` + push; the Pi self-updates within ~2 minutes.

Layout it creates:

| Path | What |
|---|---|
| `/home/pi/mkw` | the git clone (checked out at a release tag) |
| `/home/pi/mkw-data` | `mkw.db`, `bot-state.json`, `.deployed-tag` (outside the clone) |
| `/etc/mkw/mkw.env` | env + secrets (outside the clone) |
| `/home/pi/.ssh/mkw_deploy` | read-only GitHub deploy key |

## 0. Prerequisites

- A **64-bit** Pi OS: `uname -m` → `aarch64`. (32-bit `armv7l` has no Node 24 build.)
- Cloudflare account with the existing tunnel (`~/.cloudflared/config.yml` + credentials).
- A Discord **bot token** + the target channel id (developer portal → your application → Bot).
- `thekartoff.com` registered (nameservers not yet pointed at Cloudflare).

## 1. Push the repo + a release tag to GitHub (on the dev box)

The updater tracks GitHub tags, so the code must be on `origin` first.

```bash
git push origin main
git tag v0.3.0          # pick your next version
git push origin v0.3.0
```

## 2. Install Node 24 on the Pi

```bash
ssh pi@192.168.1.21
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs git python3
node -v                                   # v24.x
node -e "require('node:sqlite')" && echo "node:sqlite OK"   # must print OK, no flag needed
```

## 3. Read-only deploy key + clone

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mkw_deploy -N "" -C "mkw-pi-deploy"
cat ~/.ssh/mkw_deploy.pub
```

On GitHub: repo → **Settings → Deploy keys → Add deploy key** → paste the `.pub`, leave
**Allow write access** unchecked.

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes" \
  git clone git@github.com:<you>/<repo>.git /home/pi/mkw
cd /home/pi/mkw
GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes" git checkout v0.3.0
npm --prefix pi install --no-audit --no-fund
```

## 4. Configure env + secrets

```bash
sudo install -d /etc/mkw
sudo install -m 0640 -o root -g pi deploy/mkw.env.example /etc/mkw/mkw.env
sudo nano /etc/mkw/mkw.env      # fill DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, (DISCORD_GUILD_ID)
```

Confirm `STATS_PORKER_DB=/home/pi/porker-data/databases/botdata.db` and that the body tables
exist:

```bash
sqlite3 /home/pi/porker-data/databases/botdata.db ".tables"   # expect Measurements, AddymerMeasurements, ...
```

## 5. Migrate the legacy data (BEFORE first bot start)

The bot seeds its announce watermark to the newest run on first launch, so the historical PBs
must already be imported — otherwise the bot would announce hundreds of them. Stop the old bot
first so it stops writing + double-posting.

```bash
sudo systemctl stop mkpb && sudo systemctl disable mkpb

# Import PBs/WRs from a snapshot of the legacy DB into the server DB.
mkdir -p /home/pi/mkw-data
cp ~/mkwpb2/kart-off/data/hogkart.db /home/pi/hogkart-snapshot.db
cd /home/pi/mkw
python3 -m server.importer \
  --legacy-db /home/pi/hogkart-snapshot.db \
  --out /home/pi/mkw-data/mkw.db
# prints: players / courses / S0 runs / world_records / carryover seeds
```

(Body stats need no import — the server reads `botdata.db` in place, read-only, alongside the
pork bot's writer.)

## 6. Install + start the services

```bash
sudo MKW_REPO=/home/pi/mkw bash deploy/install.sh
systemctl status mkw-server mkw-bot --no-pager
curl -s http://localhost:8787/health        # {"status":"ok"}
```

## 7. Cloudflare domain + tunnel route

1. Cloudflare dashboard → **Add a site** → `thekartoff.com` → Free → copy the two nameservers.
2. At your registrar, set the domain's nameservers to those two. Wait for the site to go
   **Active** (minutes–hours).
3. On the Pi, route the subdomain through the existing tunnel and add the ingress rule:

```bash
cloudflared tunnel list                                  # note the tunnel name/UUID
cloudflared tunnel route dns <TUNNEL> api.thekartoff.com
nano ~/.cloudflared/config.yml
```

Add, **above** the catch-all `- service: http_status:404`:

```yaml
ingress:
  - hostname: api.thekartoff.com
    service: http://localhost:8787
  # ...existing rules...
  - service: http_status:404
```

```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
curl -s https://api.thekartoff.com/health    # {"status":"ok"} through the tunnel
```

## 8. Mint tokens + repoint the desktop apps

Reads + writes need a token. Mint one per player (players exist after the import):

```bash
cd /home/pi/mkw/pi
MKW_DB=/home/pi/mkw-data/mkw.db npm run mint-token -- Paul     # prints the token once
# repeat for Gub / Alex / Aliias / Luke
```

In each person's desktop app **Settings → Server**: set the server URL to
`https://api.thekartoff.com` and paste that person's token. The personal `/explorer` page opens
at `https://api.thekartoff.com/explorer?token=YOUR_TOKEN`.

## 9. Verification

- `curl -s https://api.thekartoff.com/health` → ok.
- `curl -s -o /dev/null -w "%{http_code}" https://api.thekartoff.com/v1/seasons` → `401`.
- `curl -s "https://api.thekartoff.com/v1/seasons?token=<paul>"` → JSON.
- Set a quick PB on the dev box → it announces in Discord within ~1s, and the old bot is silent.
- `sudo systemctl start mkw-updater.service && journalctl -u mkw-updater -n 5 --no-pager`
  → "already up to date".
- `sudo reboot`; after it comes back, `systemctl is-active mkw-server mkw-bot cloudflared` →
  `active`, and `systemctl list-timers mkw-updater.timer` shows the next run.

## 10. Steady-state updating

```bash
# on the dev box, after merging work to main:
git push origin main
git tag v0.3.1 && git push origin v0.3.1
```

Within ~2 minutes the Pi fetches the tag, checks it out, `npm install`s, and restarts. Watch:

```bash
journalctl -u mkw-updater -f
```

## 11. Rollback

The updater only ever moves to the highest tag. To roll back, delete the bad tag on GitHub and
push a higher tag containing the fix (or, on the Pi, `git checkout <good-tag>` + `npm --prefix
pi install` + `sudo systemctl restart mkw-server mkw-bot` and write that tag into
`/home/pi/mkw-data/.deployed-tag`).

## 12. Troubleshooting

- **Bot won't start** — `journalctl -u mkw-bot -n 50`. Usually a missing `DISCORD_BOT_TOKEN` /
  `DISCORD_CHANNEL_ID` in `/etc/mkw/mkw.env` (then `sudo systemctl restart mkw-bot`).
- **Updater not deploying** — `journalctl -u mkw-updater -n 30`. Check the deploy key works:
  `GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes" git -C /home/pi/mkw ls-remote --tags origin`.
- **502 through the tunnel** — the server isn't up (`systemctl status mkw-server`) or the
  ingress hostname/port is wrong (`cloudflared tunnel ingress validate`).
- **Monitor shows no friends' trails after switching to the tunnel** — the desktop token is
  missing/wrong in Settings → Server (reads now require it).
- **`node:sqlite` error on boot** — Node is too old; reinstall Node 24 (Step 2).
- **Live presence cards freeze after a quiet spell through the tunnel** — Cloudflare drops idle
  WebSockets after ~100s; the desktop reconnects on its own (the presence client carries an idle
  heartbeat, see `pi/src/presence/hub.ts`). If a friend's cards don't recover, that's a client
  heartbeat gap to chase, not a server one — `/v1/presence` is unauthenticated and unchanged here.
````

- [ ] **Step 2: Commit**

```bash
git add docs/pi-deploy.md
git commit -m "docs: Pi setup + migration runbook"
```

---

## Task 11: Final verification + branch summary

- [ ] **Step 1: Full server test suite**

Run (from `pi/`): `npm test`
Expected: PASS (the new `auth.test.ts` + the updated read tests; `ws`/`stats` unchanged).

- [ ] **Step 2: Rust compiles**

Run (from `src-tauri/`): `cargo check`
Expected: no errors.

- [ ] **Step 3: Script syntax**

Run: `bash -n deploy/update.sh && bash -n deploy/install.sh && echo OK`
Expected: `OK`.

- [ ] **Step 4: Confirm the deploy tree is complete**

Run: `git ls-files deploy docs/pi-deploy.md`
Expected: `deploy/install.sh`, `deploy/mkw.env.example`, `deploy/sudoers.d/mkw-updater`,
`deploy/systemd/mkw-{server,bot,updater}.service`, `deploy/systemd/mkw-updater.timer`,
`deploy/update.sh`, `docs/pi-deploy.md`.

- [ ] **Step 5: Confirm `update.sh` + `install.sh` are executable in git**

Run: `git ls-files -s deploy/update.sh deploy/install.sh`
Expected: mode `100755` for both.

---

## Self-review (done while writing)

- **Spec coverage:** updater (T8) + units (T6) + sudoers (T7) + env (T5) + install (T9) + token
  gate (T1–T2) + desktop token reads (T3) + explorer token (T4) + runbook covering Node/clone/
  env/migration/tunnel/repoint/verify/rollback (T10). All Components 1–9 + prerequisites map to a
  task.
- **WS exemption** matches the updated spec (events open for the on-Pi bot; presence keeps its
  optional-token model).
- **Type/name consistency:** `requireTokenAny`/`tokenFromRequest`/`bearerToken` are defined in T1
  and used in T2; `OPEN` set, `MKW_REPO`/`MKW_DATA` env names, and `/home/pi/mkw-data/.deployed-tag`
  are consistent across T8/T9/T10.
- **Not automated here (verified on the Pi, per the runbook):** `systemd-analyze verify`,
  `visudo -cf` (run by `install.sh`), the real tunnel, and the live PB→Discord path.
