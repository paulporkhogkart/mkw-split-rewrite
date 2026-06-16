# thekartoff.com Live Cards Site (v1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a public website at `thekartoff.com` that renders the pbenguin live player cards, connected to the season server, auto-updating + Pi-hosted alongside the existing services.

**Architecture:** A standalone Vite + Svelte SPA in `web/` that reuses the desktop app's `PlayerCard` component (and everything it imports) verbatim from `../src`. A small read-only WebSocket client connects to the season server's token-less `/v1/presence` stream and feeds the same shared Svelte stores the desktop uses, so the cards behave identically. The site is served by a dependency-free Node static server as a separate `mkw-web` systemd unit, behind the existing Cloudflare tunnel, and rides the existing git-tag pull-deploy.

**Tech Stack:** Svelte 4, Vite 5, Vitest 4 (matching the desktop frontend); Node 24 `node:http` for the static server; systemd + Cloudflare tunnel on the Pi.

**Key reuse facts (verified):**
- Only `src/lib/sync.js`, `discord.js`, `ipc.js` import Tauri — none are in the card render path, so `PlayerCard` and its deps import cleanly into a plain web build.
- `PlayerPanel.svelte` is **not** reused: its `.panel` grid (`repeat(N,1fr)`) stretches cards to fill width (the layout we rejected). The web provides its own `CardWall.svelte` using `PlayerCard`.
- `handlePresenceMessage(raw, now?)` + `markServerDisconnected()` are exported from `src/lib/presence.js` and are pure store-updaters — the web client reuses them.
- Player-figure PNGs (`src/assets/players/*.png`, bundled by `playerFigures.js` via `import.meta.glob`) are normal git objects (LFS only covers `captures/**` + `player_gifs`), so the Pi clone+build bundles real images with no git-lfs.
- `theme.css` sets `html,body{overflow:hidden}` + `#app{height:100vh}` for the fixed Tauri window — the web overrides these so the page scrolls.

---

## File Structure

**New (`web/`):**
- `web/package.json` — svelte/vite/vitest dev-deps; no runtime deps.
- `web/vite.config.js` — svelte plugin, dev `fs.allow:['..']` (to import `../src`), vitest config.
- `web/index.html` — `#app` mount.
- `web/src/main.js` — imports theme + web globals, sets `serverUrl`, starts the presence client, mounts `App`.
- `web/src/app.css` — web-only global overrides (scrolling page).
- `web/src/App.svelte` — header (wordmark + live `N online · M racing`) + `<CardWall>`.
- `web/src/CardWall.svelte` — the card-wall layout + shared clock, rendering reused `PlayerCard`.
- `web/src/presenceClient.js` — read-only reconnecting `/v1/presence` client.
- `web/src/presenceClient.test.js` — its unit tests.
- `web/serve.mjs` — dependency-free static server (exports `resolveFile`/`contentType`/`createStaticServer`; listens only when run directly).
- `web/serve.test.js` — unit tests for the path-resolution + content-type helpers.

**New (deploy/docs):**
- `deploy/systemd/mkw-web.service`
- `docs/website-deploy.md`

**Modified:**
- `deploy/update.sh` (build web + restart `mkw-web`)
- `deploy/install.sh` (install + enable `mkw-web`)
- `deploy/sudoers.d/mkw-updater` (allow restarting `mkw-web`)
- `docs/superpowers/specs/2026-06-17-website-live-cards-design.md` (PlayerPanel → CardWall note)

**Unchanged:** `pi/` (no server work), `src/` (imported, not edited).

---

### Task 1: Scaffold the web app + build smoke

**Files:**
- Create: `web/package.json`, `web/vite.config.js`, `web/index.html`, `web/src/app.css`, `web/src/main.js`, `web/src/App.svelte`

- [ ] **Step 1: Create `web/package.json`**

```json
{
  "name": "thekartoff-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "serve": "node serve.mjs"
  },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^3",
    "svelte": "^4",
    "vite": "^5",
    "vitest": "^4.1.8"
  }
}
```

- [ ] **Step 2: Create `web/vite.config.js`**

```js
import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Standalone public website (no Tauri). Reuses the desktop card components from ../src,
// so the dev server must be allowed to read one directory up. `vite build` (used on the
// Pi) follows imports anywhere and is unaffected. outDir defaults to dist -> web/dist.
export default defineConfig({
  plugins: [svelte()],
  server: { port: 1430, strictPort: true, fs: { allow: [".."] } },
  test: { include: ["**/*.test.js"] },
  build: { target: "chrome105" },
});
```

- [ ] **Step 3: Create `web/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>thekartoff</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

- [ ] **Step 4: Create `web/src/app.css`**

```css
/* Website overrides for the desktop globals in src/theme.css, which assume a fixed
   100vh Tauri window. The site is a normal scrolling page. */
html, body { overflow: auto; height: auto; }
#app { height: auto; min-height: 100vh; }
body { background: var(--bg); }
```

- [ ] **Step 5: Create `web/src/main.js`** (presence wiring is added in Task 3)

```js
import "../../src/theme.css";
import "./app.css";
import App from "./App.svelte";

const app = new App({ target: document.getElementById("app") });
export default app;
```

- [ ] **Step 6: Create `web/src/App.svelte`** (header only for now; `CardWall` is added in Task 3)

```svelte
<script>
  import { presence } from "../../src/lib/stores.js";
  $: vals = Object.values($presence);
  $: online = vals.filter((p) => p.online).length;
  $: racing = vals.filter((p) => p.online && p.screen === "RACING" && !p.final_time).length;
</script>

<header class="top">
  <div class="brand"><span class="a">the</span><span class="b">kartoff</span></div>
  <div class="live"><span class="dot"></span><b>{online}</b>&nbsp;online&nbsp;·&nbsp;<b>{racing}</b>&nbsp;racing</div>
</header>
<main></main>

<style>
  .top{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;
       padding:13px 22px;background:var(--panel);border-bottom:1px solid var(--bd);}
  .brand{display:flex;align-items:baseline;gap:2px;font-size:16px;font-weight:700;letter-spacing:.01em;}
  .brand .a{color:var(--tx);} .brand .b{color:var(--accent);}
  .live{display:flex;align-items:center;gap:8px;font-size:10.5px;letter-spacing:.09em;
        color:var(--tx-mut);text-transform:uppercase;}
  .live .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);position:relative;}
  .live .dot::after{content:"";position:absolute;inset:0;border-radius:50%;background:var(--ok);
                    animation:pulse 1.8s ease-out infinite;}
  @keyframes pulse{0%{transform:scale(1);opacity:.5;}100%{transform:scale(2.6);opacity:0;}}
  .live b{color:var(--tx);font-weight:600;}
</style>
```

- [ ] **Step 7: Install deps**

Run: `npm --prefix web install --no-audit --no-fund`
Expected: completes; creates `web/node_modules` + `web/package-lock.json`.

- [ ] **Step 8: Build to verify the scaffold + cross-`src` import resolve**

Run: `npm --prefix web run build`
Expected: build succeeds; `web/dist/index.html` is emitted. (`web/node_modules` + `web/dist` are already gitignored via `node_modules/` + `dist/`.)

- [ ] **Step 9: Commit**

```bash
git add web/package.json web/package-lock.json web/vite.config.js web/index.html web/src/app.css web/src/main.js web/src/App.svelte
git commit -m "feat(web): scaffold thekartoff.com SPA (header + reused theme)"
```

---

### Task 2: Read-only presence client (TDD)

**Files:**
- Create: `web/src/presenceClient.js`
- Test: `web/src/presenceClient.test.js`

- [ ] **Step 1: Write the failing test**

Create `web/src/presenceClient.test.js`:

```js
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import { presence, serverConnection, myPlayerId } from "../../src/lib/stores.js";
import { presenceWsUrl, startPresence } from "./presenceClient.js";

// Minimal fake WebSocket: captures listeners, lets the test drive open/message/close.
class FakeWS {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; FakeWS.last = this; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  emit(type, ev) { (this.listeners[type] || []).forEach((fn) => fn(ev)); }
  close() { this.closed = true; }
}

beforeEach(() => {
  presence.set({}); myPlayerId.set(null); serverConnection.set({ connected: false, syncedAt: null });
  FakeWS.last = null;
});

describe("presenceWsUrl", () => {
  it("derives the token-less ws(s) URL and strips a trailing slash", () => {
    expect(presenceWsUrl("https://api.thekartoff.com/")).toBe("wss://api.thekartoff.com/v1/presence");
    expect(presenceWsUrl("http://localhost:8787")).toBe("ws://localhost:8787/v1/presence");
  });
});

describe("startPresence", () => {
  it("opens a receive-only socket and applies a snapshot to the shared stores", () => {
    startPresence("http://localhost:8787", { WebSocketImpl: FakeWS });
    expect(FakeWS.last.url).toBe("ws://localhost:8787/v1/presence");
    FakeWS.last.emit("open");
    FakeWS.last.emit("message", { data: JSON.stringify({
      type: "presence_snapshot", you: null,
      players: [{ player_id: 1, name: "Paul", online: true }, { player_id: 2, name: "Luke", online: false }],
    }) });
    expect(Object.keys(get(presence)).sort()).toEqual(["1", "2"]);
    expect(get(serverConnection).connected).toBe(true);
  });

  it("marks disconnected and schedules a reconnect on close", () => {
    let scheduled = null;
    const setTimeoutImpl = (fn, ms) => { scheduled = { fn, ms }; return 1; };
    startPresence("http://x", { WebSocketImpl: FakeWS, setTimeoutImpl });
    const first = FakeWS.last;
    first.emit("message", { data: JSON.stringify({ type: "presence_snapshot", players: [] }) });
    expect(get(serverConnection).connected).toBe(true);
    first.emit("close");
    expect(get(serverConnection).connected).toBe(false);
    expect(scheduled.ms).toBe(1000);
    scheduled.fn();                          // run the scheduled reconnect
    expect(FakeWS.last).not.toBe(first);     // a fresh socket was opened
  });

  it("stop() prevents reconnects after close", () => {
    let scheduled = null;
    const setTimeoutImpl = (fn, ms) => { scheduled = { fn, ms }; return 1; };
    const stop = startPresence("http://x", { WebSocketImpl: FakeWS, setTimeoutImpl });
    stop();
    FakeWS.last.emit("close");
    expect(scheduled).toBeNull();            // closed first -> never schedules
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix web test`
Expected: FAIL — cannot resolve `./presenceClient.js`.

- [ ] **Step 3: Implement `web/src/presenceClient.js`**

```js
// Read-only live-presence client for the public website. Connects to the season
// server's /v1/presence WebSocket WITHOUT a token (a token-less socket is
// receive-only) and feeds the shared presence stores via the desktop app's
// handlePresenceMessage. It only receives - no outbound frames, no local-self echo
// (those are desktop-only concerns). Mirrors the reconnect/backoff in src/lib/presence.js.
import { handlePresenceMessage, markServerDisconnected } from "../../src/lib/presence.js";

const MAX_BACKOFF_MS = 30000;

/** ws(s)://<origin>/v1/presence - the public, receive-only stream (no token). */
export function presenceWsUrl(apiBase) {
  const base = (apiBase || "").trim().replace(/\/+$/, "");
  return `${base.replace(/^http/, "ws")}/v1/presence`;
}

/** Open a reconnecting read-only presence socket. Returns stop(). WebSocket + timers
 *  are injectable for tests. */
export function startPresence(apiBase, {
  WebSocketImpl = WebSocket, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
} = {}) {
  const url = presenceWsUrl(apiBase);
  let ws = null, closed = false, backoff = 1000, timer = 0;

  function connect() {
    if (closed) return;
    ws = new WebSocketImpl(url);
    ws.addEventListener("open", () => { backoff = 1000; });
    ws.addEventListener("message", (e) => handlePresenceMessage(e.data));
    ws.addEventListener("close", () => {
      markServerDisconnected();
      if (closed) return;
      timer = setTimeoutImpl(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
  }
  connect();

  return function stop() {
    closed = true;
    if (timer) clearTimeoutImpl(timer);
    try { ws?.close(); } catch { /* ignore */ }
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix web test`
Expected: PASS (all presenceClient tests green).

- [ ] **Step 5: Commit**

```bash
git add web/src/presenceClient.js web/src/presenceClient.test.js
git commit -m "feat(web): read-only /v1/presence client feeding the shared stores"
```

---

### Task 3: Card wall + wire live data

**Files:**
- Create: `web/src/CardWall.svelte`
- Modify: `web/src/App.svelte` (add `<CardWall>`), `web/src/main.js` (set `serverUrl` + start presence)

- [ ] **Step 1: Create `web/src/CardWall.svelte`**

Reuses `PlayerCard` verbatim; mirrors `PlayerPanel`'s shared clock but uses the approved wall layout instead of the desktop stretch grid.

```svelte
<script>
  import { onDestroy } from "svelte";
  import { presence, serverConnection } from "../../src/lib/stores.js";
  import PlayerCard from "../../src/components/PlayerCard.svelte";

  // Render in stable ascending player_id order; the server seeds all roster players
  // (offline), so the wall is populated as soon as the first snapshot lands.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: connected = $serverConnection.connected;
  // No live link -> every card renders stale/offline (the website has no local-self echo).
  $: anyRacing = connected && players.some((p) => p.online && p.screen === "RACING" && !p.final_time);

  // One shared clock for all cards: ~30fps while someone races (so the ms timer ticks),
  // else a cheap 1s tick. Avoids a per-card animation loop. (Mirrors PlayerPanel.svelte.)
  let now = Date.now();
  let fast = 0, slow = 0, clockRacing = null;
  function setClock(racing) {
    if (racing === clockRacing) return;
    clockRacing = racing;
    clearInterval(fast); clearInterval(slow); fast = 0; slow = 0;
    now = Date.now();
    if (racing) fast = setInterval(() => (now = Date.now()), 33);
    else slow = setInterval(() => (now = Date.now()), 1000);
  }
  $: setClock(anyRacing);
  onDestroy(() => { clearInterval(fast); clearInterval(slow); });
</script>

{#if players.length}
  <div class="wall">
    {#each players as p (p.player_id)}
      <div class="cell"><PlayerCard entry={p} {now} stale={!connected} /></div>
    {/each}
  </div>
{:else}
  <div class="empty">Connecting to the season server…</div>
{/if}

<style>
  /* One centered row of native ~189px cards; shrink to 170px to hold the row, then
     wrap-and-center; one full-width column on a phone. */
  .wall { max-width: 1200px; margin: 16px auto 0; padding: 0 18px; display: flex; flex-wrap: wrap;
          justify-content: center; gap: 8px; }
  .cell { flex: 0 1 189px; min-width: 170px; height: 172px; }
  .cell > :global(.tt) { width: 100%; }
  @media (max-width: 430px) { .cell { flex-basis: 100%; min-width: 0; } }
  .empty { text-align: center; color: var(--tx-mut); font-size: .8rem; padding: 48px 0; }
</style>
```

- [ ] **Step 2: Modify `web/src/App.svelte`** — replace the empty `<main></main>` with the wall

Replace the entire file with:

```svelte
<script>
  import { presence } from "../../src/lib/stores.js";
  import CardWall from "./CardWall.svelte";
  $: vals = Object.values($presence);
  $: online = vals.filter((p) => p.online).length;
  $: racing = vals.filter((p) => p.online && p.screen === "RACING" && !p.final_time).length;
</script>

<header class="top">
  <div class="brand"><span class="a">the</span><span class="b">kartoff</span></div>
  <div class="live"><span class="dot"></span><b>{online}</b>&nbsp;online&nbsp;·&nbsp;<b>{racing}</b>&nbsp;racing</div>
</header>
<main><CardWall /></main>

<style>
  .top{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;
       padding:13px 22px;background:var(--panel);border-bottom:1px solid var(--bd);}
  .brand{display:flex;align-items:baseline;gap:2px;font-size:16px;font-weight:700;letter-spacing:.01em;}
  .brand .a{color:var(--tx);} .brand .b{color:var(--accent);}
  .live{display:flex;align-items:center;gap:8px;font-size:10.5px;letter-spacing:.09em;
        color:var(--tx-mut);text-transform:uppercase;}
  .live .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);position:relative;}
  .live .dot::after{content:"";position:absolute;inset:0;border-radius:50%;background:var(--ok);
                    animation:pulse 1.8s ease-out infinite;}
  @keyframes pulse{0%{transform:scale(1);opacity:.5;}100%{transform:scale(2.6);opacity:0;}}
  .live b{color:var(--tx);font-weight:600;}
</style>
```

- [ ] **Step 3: Modify `web/src/main.js`** — point at the API + start the presence client

Replace the entire file with:

```js
import "../../src/theme.css";
import "./app.css";
import { serverUrl } from "../../src/lib/syncSettings.js";
import { startPresence } from "./presenceClient.js";
import App from "./App.svelte";

// The season server origin. Override for local dev via VITE_API_BASE (e.g. http://localhost:8787).
const API_BASE = import.meta.env.VITE_API_BASE || "https://api.thekartoff.com";
serverUrl.set(API_BASE);          // PlayerPanel/empty-state copy reads this as "configured"
startPresence(API_BASE);          // read-only presence socket -> shared stores

const app = new App({ target: document.getElementById("app") });
export default app;
```

- [ ] **Step 4: Build to verify the full app compiles (incl. reused PlayerCard + Fire + figures)**

Run: `npm --prefix web run build`
Expected: build succeeds; `web/dist/index.html` + hashed JS/CSS assets emitted, and player-figure PNGs bundled (no resolve errors from `playerFigures.js`).

- [ ] **Step 5: Commit**

```bash
git add web/src/CardWall.svelte web/src/App.svelte web/src/main.js
git commit -m "feat(web): live card wall reusing PlayerCard, wired to presence"
```

---

### Task 4: Dependency-free static server (TDD)

**Files:**
- Create: `web/serve.mjs`
- Test: `web/serve.test.js`

- [ ] **Step 1: Write the failing test**

Create `web/serve.test.js`:

```js
import { describe, it, expect } from "vitest";
import { join } from "node:path";
import { resolveFile, contentType } from "./serve.mjs";

const DIST = join(process.cwd(), "dist");

describe("contentType", () => {
  it("maps known extensions and defaults to octet-stream", () => {
    expect(contentType("/x/app.js")).toBe("text/javascript; charset=utf-8");
    expect(contentType("/x/index.html")).toBe("text/html; charset=utf-8");
    expect(contentType("/x/logo.svg")).toBe("image/svg+xml");
    expect(contentType("/x/blob.bin")).toBe("application/octet-stream");
  });
});

describe("resolveFile", () => {
  it("maps '/' to index.html under dist", () => {
    expect(resolveFile("/", DIST)).toBe(join(DIST, "index.html"));
  });
  it("maps an asset path under dist", () => {
    expect(resolveFile("/assets/app.js", DIST)).toBe(join(DIST, "assets", "app.js"));
  });
  it("strips a query string", () => {
    expect(resolveFile("/assets/app.js?v=2", DIST)).toBe(join(DIST, "assets", "app.js"));
  });
  it("never escapes dist via traversal", () => {
    const r = resolveFile("/../../etc/passwd", DIST);
    expect(r.startsWith(DIST)).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix web test`
Expected: FAIL — cannot resolve `./serve.mjs`.

- [ ] **Step 3: Implement `web/serve.mjs`**

```js
// Dependency-free static server for the built website (web/dist). Serves files, with
// an SPA fallback to index.html for extension-less paths. No deps so the systemd unit
// just runs `node serve.mjs`. PORT defaults to 8788. Helpers are exported for tests;
// the server only listens when this file is run directly.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, normalize, extname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
  ".map": "application/json", ".txt": "text/plain; charset=utf-8",
};

export function contentType(p) { return TYPES[extname(p).toLowerCase()] || "application/octet-stream"; }

/** Map a request path to an absolute file under `distDir`, guarding against traversal.
 *  A trailing slash maps to index.html; leading `..`/slashes are stripped so the result
 *  always stays within distDir (a non-existent file is the caller's 404/SPA decision). */
export function resolveFile(urlPath, distDir) {
  let p = decodeURIComponent((urlPath || "/").split("?")[0]);
  if (p.endsWith("/")) p += "index.html";
  const rel = normalize(p).replace(/^(\.\.[/\\])+/, "").replace(/^[/\\]+/, "");
  return join(distDir, rel);
}

export function createStaticServer(distDir) {
  const indexHtml = join(distDir, "index.html");
  return createServer(async (req, res) => {
    try {
      let file = resolveFile(req.url, distDir);
      const exists = await stat(file).then((s) => s.isFile()).catch(() => false);
      if (!exists) {
        if (extname(file)) { res.writeHead(404); res.end("not found"); return; }
        file = indexHtml;   // extension-less path -> SPA shell
      }
      const body = await readFile(file);
      res.writeHead(200, {
        "content-type": contentType(file),
        "cache-control": file === indexHtml ? "no-cache" : "public, max-age=3600",
      });
      res.end(body);
    } catch {
      res.writeHead(500); res.end("server error");
    }
  });
}

// Listen only when run directly (not when imported by tests).
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const distDir = fileURLToPath(new URL("./dist", import.meta.url));
  const port = Number(process.env.PORT ?? 8788);
  createStaticServer(distDir).listen(port, () => console.log(`[web] serving ${distDir} on http://127.0.0.1:${port}`));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix web test`
Expected: PASS (presenceClient + serve suites all green).

- [ ] **Step 5: Manual smoke (optional but recommended)**

Run: `npm --prefix web run build && PORT=8788 node web/serve.mjs` then in another shell `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8788` → `200`. Ctrl-C to stop.

- [ ] **Step 6: Commit**

```bash
git add web/serve.mjs web/serve.test.js
git commit -m "feat(web): dependency-free static server with SPA fallback"
```

---

### Task 5: Pi service + auto-update wiring

**Files:**
- Create: `deploy/systemd/mkw-web.service`
- Modify: `deploy/update.sh`, `deploy/install.sh`, `deploy/sudoers.d/mkw-updater`

- [ ] **Step 1: Create `deploy/systemd/mkw-web.service`**

```ini
[Unit]
Description=MKW website (static SPA on :8788)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/mkw/web
Environment=PORT=8788
ExecStart=/usr/bin/node serve.mjs
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Modify `deploy/update.sh`** — build the web app + restart `mkw-web`

Find:

```bash
npm --prefix "$REPO/pi" install --no-audit --no-fund
sudo systemctl restart mkw-server mkw-bot
echo "$latest" > "$MARKER"
```

Replace with:

```bash
npm --prefix "$REPO/pi" install --no-audit --no-fund
npm --prefix "$REPO/web" install --no-audit --no-fund
npm --prefix "$REPO/web" run build
sudo systemctl restart mkw-server mkw-bot mkw-web
echo "$latest" > "$MARKER"
```

- [ ] **Step 3: Modify `deploy/install.sh`** — install + enable the unit

Find:

```bash
install -m 0644 \
  "$REPO/deploy/systemd/mkw-server.service" \
  "$REPO/deploy/systemd/mkw-bot.service" \
  "$REPO/deploy/systemd/mkw-updater.service" \
  "$REPO/deploy/systemd/mkw-updater.timer" \
  /etc/systemd/system/
```

Replace with:

```bash
install -m 0644 \
  "$REPO/deploy/systemd/mkw-server.service" \
  "$REPO/deploy/systemd/mkw-bot.service" \
  "$REPO/deploy/systemd/mkw-web.service" \
  "$REPO/deploy/systemd/mkw-updater.service" \
  "$REPO/deploy/systemd/mkw-updater.timer" \
  /etc/systemd/system/
```

Then find:

```bash
systemctl enable --now mkw-server.service mkw-bot.service mkw-updater.timer
```

Replace with:

```bash
systemctl enable --now mkw-server.service mkw-bot.service mkw-web.service mkw-updater.timer
```

- [ ] **Step 4: Modify `deploy/sudoers.d/mkw-updater`** — allow restarting `mkw-web`

Replace the whole file with (the combined `restart mkw-server mkw-bot mkw-web` must be an exact allowed command, since `update.sh` invokes it verbatim):

```
# Let the unprivileged deploy user restart only the MKW units, no password.
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart mkw-server mkw-bot mkw-web, /usr/bin/systemctl restart mkw-server mkw-bot, /usr/bin/systemctl restart mkw-server, /usr/bin/systemctl restart mkw-bot, /usr/bin/systemctl restart mkw-web, /usr/bin/systemctl start mkw-server, /usr/bin/systemctl start mkw-bot, /usr/bin/systemctl start mkw-web
```

- [ ] **Step 5: Commit**

```bash
git add deploy/systemd/mkw-web.service deploy/update.sh deploy/install.sh deploy/sudoers.d/mkw-updater
git commit -m "feat(deploy): mkw-web service + auto-build/restart in the pull-deploy"
```

---

### Task 6: Deploy runbook

**Files:**
- Create: `docs/website-deploy.md`

- [ ] **Step 1: Create `docs/website-deploy.md`**

````markdown
# Website (thekartoff.com) deploy runbook

The public live-cards site (`web/`), served by the `mkw-web` systemd service on the Pi
alongside `mkw-server`/`mkw-bot`, reachable at `https://thekartoff.com` (and `www.`) through
the existing Cloudflare tunnel. Steady-state it auto-updates with the others: you `git tag` +
push, the Pi rebuilds + restarts within ~2 minutes.

## 0. Prerequisites
- The Pi server + bot + tunnel are already up (see `docs/pi-deploy.md`).
- `thekartoff.com` is an active Cloudflare zone (it is — registered through Cloudflare).
- Node 24 on the Pi (already installed for the server).

## 1. Push the repo + a release tag (dev box)
```bash
git push origin main
git tag v0.4.0           # pick your next version
git push origin v0.4.0
```

## 2. Build the site + (re)install the services (Pi)
```bash
ssh pi@192.168.1.21
cd /home/pi/mkw
export GIT_SSH_COMMAND="ssh -i ~/.ssh/mkw_deploy -o IdentitiesOnly=yes"
git fetch --tags origin && git checkout v0.4.0
npm --prefix web install --no-audit --no-fund
npm --prefix web run build                           # -> web/dist
sudo MKW_REPO=/home/pi/mkw bash deploy/install.sh    # now also installs + enables mkw-web
systemctl status mkw-web --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8788    # 200
```

## 3. Cloudflare tunnel route (Pi)
Route the apex + www through the existing tunnel:
```bash
cloudflared tunnel route dns <TUNNEL> thekartoff.com
cloudflared tunnel route dns <TUNNEL> www.thekartoff.com
nano ~/.cloudflared/config.yml
```
Add, **above** the catch-all `- service: http_status:404`:
```yaml
  - hostname: thekartoff.com
    service: http://localhost:8788
  - hostname: www.thekartoff.com
    service: http://localhost:8788
```
(If `route dns` errors about the management cert, use the dashboard proxied-CNAME method from
`docs/pi-deploy.md` §7 — point `thekartoff.com` + `www` at `<TUNNEL-UUID>.cfargotunnel.com`.)
```bash
cloudflared tunnel ingress validate
sudo systemctl restart cloudflared
curl -s -o /dev/null -w "%{http_code}\n" https://thekartoff.com   # 200
```

## 4. Verify
- `https://thekartoff.com` loads the five cards (offline until someone opens the app).
- Open the desktop app + start a race → that card goes live within ~1s (timer + bar tick).
- `https://www.thekartoff.com` loads the same.
- `sudo reboot`; after it returns, `systemctl is-active mkw-web` → `active`.

## 5. Steady-state updating
Same as the server: `git tag vX.Y.Z && git push origin vX.Y.Z`. Within ~2 min the Pi checks
out the tag, runs `npm --prefix web install` + `npm --prefix web run build`, and restarts
`mkw-web` (see `deploy/update.sh`). Watch: `journalctl -u mkw-updater -f`.

## 6. Troubleshooting
- **502 on thekartoff.com** — `mkw-web` is down (`systemctl status mkw-web`) or `web/dist` is
  missing (rebuild). `journalctl -u mkw-web -n 50`.
- **Blank page / module MIME errors** — the build didn't run or `web/dist` is stale; rebuild.
- **Cards never go live** — the browser can't reach `wss://api.thekartoff.com/v1/presence`;
  confirm the `api` hostname is still routed (it's separate from the web host).
````

- [ ] **Step 2: Commit**

```bash
git add docs/website-deploy.md
git commit -m "docs(web): thekartoff.com deploy runbook"
```

---

### Task 7: Spec sync + full verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-17-website-live-cards-design.md`

- [ ] **Step 1: Sync the spec to the built reality (PlayerPanel → CardWall)**

In `docs/superpowers/specs/2026-06-17-website-live-cards-design.md`, under **Section 1 → "Reused from the desktop app"**, find:

```
- Components: `PlayerPanel.svelte`, `PlayerCard.svelte`, `Fire.svelte`
```

Replace with:

```
- Components: `PlayerCard.svelte`, `Fire.svelte` (PlayerCard's transitive deps). NOT `PlayerPanel.svelte` — its grid stretches cards to fill width (desktop behavior); the web uses its own `web/src/CardWall.svelte` (the approved wall layout) which renders `PlayerCard`.
```

- [ ] **Step 2: Run the full web test suite**

Run: `npm --prefix web test`
Expected: PASS — presenceClient + serve suites all green.

- [ ] **Step 3: Production build smoke**

Run: `npm --prefix web run build`
Expected: succeeds; `web/dist/index.html` present.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-17-website-live-cards-design.md
git commit -m "docs(web): sync spec to CardWall (PlayerPanel not reused)"
```

---

## Self-Review

**Spec coverage:**
- v1 = live card wall as the site → Tasks 1, 3 (App + CardWall). ✓
- Public access (token-less presence) → Task 2 (`presenceWsUrl` has no token). ✓
- Native ~189px card, row→wrap→1-col → Task 3 (`CardWall` flex `0 1 189px`, min 170, mobile 430). ✓
- Reuse the card stack from `../src` → Tasks 1, 3 (imports `PlayerCard`, `stores`, `presence`, `syncSettings`, `theme.css`); `PlayerPanel` exception captured (Task 7). ✓
- Read-only presence client feeding shared stores → Task 2. ✓
- No server changes → no `pi/` task. ✓
- Separate `mkw-web` service, port 8788 → Tasks 4, 5. ✓
- Auto-update via update.sh + sudoers → Task 5. ✓
- Setup guide → Task 6. ✓
- Testing (presenceClient + build smoke) → Tasks 2, 4, 7. ✓

**Placeholder scan:** No TBD/TODO; every code/edit step shows complete content; commands have expected output. ✓

**Type/name consistency:** `startPresence`/`presenceWsUrl` (Task 2 ↔ Task 3 main.js), `resolveFile`/`contentType`/`createStaticServer` (Task 4 ↔ serve.test), `CardWall` (Tasks 3, 7), stores `presence`/`serverConnection` and `handlePresenceMessage`/`markServerDisconnected` (match `src/lib` exports), service name `mkw-web` (Tasks 4, 5, 6), port `8788` (service + serve.mjs + docs). ✓
