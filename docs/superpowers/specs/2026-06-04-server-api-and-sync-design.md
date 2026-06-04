# Server, Identity & Sync — Design

**Date:** 2026-06-04
**Status:** Approved (design); pending implementation plan
**Component:** A new TypeScript/Node server on the Pi that becomes the **canonical source of truth** for race results, plus the client **write path** under a revised architecture: the Python engine becomes a **pure detector** that emits one finalized-run event per attempt, and the **Tauri/Rust app** forwards those to the server through a resilient offline outbox. This is sub-project **B** of the client→server shift; the OBS overlays + public website that consume the data are sub-project **C**.

## 1. Goal

Make the Pi server the authoritative store for every friend's race attempts, fed by a resilient client write path, and have it push **event notifications** ("X started a run", "X PB'd — now #1", "X beat the WR") to live consumers plus a public read API. Each client identifies with a per-player token; uploads survive a flaky network via a local outbox.

The architecture shift (decided during design): durable race data leaves the engine. The engine's job is detection; the server owns race results. See §6.

## 2. Non-goals (deferred)

- **Phase 2 — client read-migration.** Moving the monitor's *own-player* reads (PB splits, history, own ghost trails) off the engine's local DB onto the server/cache, and then **removing the engine's race-data tier** (`replays` / `replay_points` / `replay_splits`). Deferred deliberately so B does not big-bang the just-merged frontend. In Phase 1 (this spec) the engine keeps writing its local race store **solely to keep today's monitor display working unchanged**.
- **Live in-progress telemetry** — streaming a remote friend's run *as it happens*. B builds the foundation (uploader + server + `WS /v1/events`); this is a clean later addition.
- **OBS overlays + public website** that consume the API/events — sub-project **C** (friends'-PB *rendering* on the local monitor also lands here / Phase 2, since it's a consumption/UI concern).
- **WR scraper** — later; the `world_records` table is append-ready.
- **cc screen-detection** — until it exists, runs are tagged with a **cc setting (default 150)**.
- **Auth beyond a per-player bearer token.**

## 3. Stack decision & rationale

**TypeScript on Node**, chosen impartially (familiarity, dev-effort, and legacy/A reuse explicitly excluded as factors):

- The interactive frontend (OBS overlays = browser sources; the public website) is **unavoidably JS/TS** and needs live WS-driven updates — a hard constraint, not a preference.
- This system's real risk is **contract/type drift between what the API/WS emits and what the live frontend renders**, not throughput/memory/concurrency (trivial at ~5 uploaders + modest public reads). A TS backend gives a **single compile-time-checked contract** from SQLite row → API → WS event → website/overlay prop, eliminating that drift natively.
- Go's strengths (static binary, low memory, concurrency) target bottlenecks this project never hits and force a two-language type boundary to the mandatory TS frontend. Python's edges here were the discarded reuse/familiarity. Rust is the heaviest build for performance that isn't needed.

**Runtime:** Node (battle-tested for an unattended 24/7 Pi service; Bun noted as a future option). **Libraries:** **Hono** (HTTP + WebSockets) and **better-sqlite3** (synchronous, ideal single-process). SvelteKit serves the website + overlays in **C**.

## 4. Repo shape (TS monorepo, pnpm workspaces, under `pi/`)

```
pi/
  package.json            pnpm workspace root
  packages/
    db/                   better-sqlite3 data layer over server/schema.sql + runtime queries (TS)
    shared/               wire-protocol + WS event types (imported by api, and by web in C)
  apps/
    api/                  Hono server: ingest + auth + public reads + WS event hub   ← sub-project B
    web/                  SvelteKit: public website + OBS overlays                    ← sub-project C
```

`pi/` keeps the Node workspace isolated from the repo-root `package.json` (the Tauri/Vite app). One Node process serves the API + WS on the Pi under systemd, behind the existing Cloudflare tunnel.

## 5. Relationship to sub-project A

- **`server/schema.sql` stays the single source of truth.** `pi/packages/db` reads/applies it (idempotent); A's Python importer reads the same file.
- **A's importer stays a one-shot Python CLI** (`python -m server.importer`), run on the Pi at cutover.
- A's Python `queries.py` is used **only by the importer**; the TS `db` re-expresses the runtime queries (ingest upsert, `is_pb` recompute, leaderboards). Mild duplication separated by time (one-shot migration vs live serving).
- **A's note about "reconciling the client's local DB" resolves here as the Phase 2 teardown:** the client's local race-data tier becomes legacy and is removed once reads move to the server.

## 6. Target architecture & the phased turn

**End state.** Three clean tiers:
- **Engine (Python) = pure detector.** Emits a rich IPC event stream including a new consolidated **`run_finalized`** event carrying the whole attempt. Keeps only *detection config* (the `config` table, minimap seeds/ROIs/thresholds, tell overrides — the tuning it needs to detect). Owns **no race data**, does **no network**.
- **Tauri app (Rust) = resilient client edge.** Forwards finalized runs to the server through a persistent outbox; holds the server URL + token; (Phase 2) reads back from the server for display with a small local cache.
- **Server (TS) = source of truth.** Stores every attempt, derives events, serves the read API + WS.

**Invariant — the app is the engine's only runtime peer.** The engine communicates solely over its IPC stream (stdin/stdout) with the Tauri/Rust app; it does no network and is never contacted directly by the server or any consumer (overlays/website talk to the *server*, never the engine). The engine's optional `--ws-port` broadcaster — used today by local overlays + the dev autotemplate tool — becomes redundant for production once overlays consume the server in C; retiring it or fencing it to the dev-only autotemplate workflow is a small cleanup item (tracked, not in B's write-path scope).

**Phase 1 = this spec (B):** stand up the server, and the **write path** (engine emits `run_finalized` → Rust outbox → server). The engine *temporarily* keeps writing its existing local race store so the current monitor display is untouched.

**Phase 2 = deferred:** repoint the monitor's own-player reads at the server/app-cache, render friends from the server, then delete the engine's race-data tables. The engine becomes fully pure.

## 7. Data flow (Phase 1)

```
 Python engine (pure detector)        Tauri app (Rust)               Pi — Node/TS (Cloudflare tunnel)
 ┌───────────────────────┐  run_finalized   ┌──────────────────┐  POST /v1/runs   ┌──────────────────┐
 │ detect → emit events  │ ───(IPC stdout)─►│ sync.rs:         │ ──(token)──────► │ apps/api (Hono)  │
 │ (mints attempt_id)    │                  │  • rusqlite      │                  │  ingest → SQLite │
 │ [keeps local store    │                  │    outbox        │ ◄── 200 {is_pb,  │  derive events   │
 │  for own display only]│                  │  • POST + retry  │      rank,gaps}  │  WS fan-out ─────┼─► C
 └───────────────────────┘                  │  • holds url+token│                 └──────────────────┘
                                            └──────────────────┘
```

The engine builds the full payload once; **Rust forwards it opaquely** (stores + POSTs the JSON, keyed by `attempt_id`) — so the payload's *shape* is a single engine(Python)→server(TS) contract, with Rust as a dumb resilient pipe (no payload re-modeling).

## 8. Connectivity & security

- **Cloudflare Tunnel** (already on the Pi) exposes the API over HTTPS — no router config, TLS terminated by Cloudflare.
- **Reads public** (the website needs them; no secrets). **Writes token-gated** (a client uploads only as its own player). Abuse protection left to Cloudflare; no app-level rate limiting in B.

## 9. Identity / auth

- Add column `players.auth_token_hash TEXT UNIQUE` to `server/schema.sql` (A reserved this); fresh server DBs created at/after cutover include it.
- A Pi-side admin script (e.g. `pnpm --filter api mint-token <player-name>`) generates a random token, stores its **sha256 hash**, and prints the **plaintext once** for the friend.
- Requests send `Authorization: Bearer <token>`; the server hashes → looks up the player → attributes writes to that `player_id`. No token → reads only.
- **The token + server URL live in the Tauri app's settings** (a small "Sync" settings tab, mirroring the Discord tab) — not the engine. The engine never sees them.

## 10. Wire protocol — endpoints (`/v1`)

**Writes (Bearer token):**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/runs` | Upload one attempt. Idempotent by `attempt_id`. Server attributes `player` (token) + `season` (active) + `provenance='live'`; upserts; recomputes `is_pb`; derives events. Returns `{is_pb, rank, gap_to_leader_ms, gap_to_wr_ms}`. |
| `POST` | `/v1/runs/start` | Ephemeral "run started" ping → emits `run_started`. No DB write. |

**Reads (public):**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/leaderboard?season&course&cc` | Per-(course, cc) leaderboard |
| `GET` | `/v1/leaderboard/overall?season&cc` | Aggregate standings |
| `GET` | `/v1/friends-pbs?season&course&cc` | Roster's PBs for a course |
| `GET` | `/v1/players/:id/pbs?season&cc` | One player's PBs across courses |
| `GET` | `/v1/world-records?course&cc` | Current WR (+ history) |
| `GET` | `/v1/seasons`, `GET /health` | — |

**Live (public, read-only):** `WS /v1/events` — subscribe-only fan-out.

## 11. Upload payload (built by the engine, forwarded by Rust)

```json
{
  "attempt_id": "uuid",            // engine-minted; idempotency key
  "course": "Wario's Galleon",     // display name; slugified server-side → courses.slug
  "cc": 150,
  "status": "finished",            // "finished" | "reset" | "dnf"
  "character": "Mario", "kart": "Standard Kart", "costume": "Base",
  "started_at": "ISO8601", "ended_at": "ISO8601",
  "total_time": "1:48.000",        // null when status != finished
  "laps":   [ { "lap": 1, "time_ms": 36000, "coins": 5, "shrooms": 1 } ],
  "points": [ [t_ms, cx, cy, score] ]
}
```
Server stamps `player_id` (token), `season_id` (active), `provenance='live'`; maps `course` via A's slugify; missing `cc` defaults to 150.

## 12. Ingest + event derivation (server, on `POST /v1/runs`)

1. Validate token + payload; resolve `course` slug → `course_id` (400 on unknown).
2. **Upsert** by `attempt_id` (re-sends are no-ops/updates), `provenance='live'`, plus `run_laps` / `run_points`.
3. If `status='finished'`: recompute `is_pb` for `(active_season, player, course, cc)`.
4. Compute vs pre-upsert state: became a PB? new course #1? beats the WR?
5. Publish event(s) to the in-process **event hub** → fan out to all `WS /v1/events` subscribers.
6. Resets/DNFs upsert silently (no events).

## 13. WS event types (`packages/shared`, typed)

| Event | Payload (essentials) |
|---|---|
| `run_started` | player, course, cc |
| `run_finished` | player, course, cc, total_time, is_pb, rank |
| `pb_achieved` | player, course, cc, total_time, delta_vs_prev_ms, rank |
| `lead_change` | course, cc, new_leader, prev_leader, total_time |
| `wr_beaten` | player, course, cc, total_time, wr_time |

## 14. Client write path (the three pieces)

1. **Engine (`mkw_tracker`, Python)** — add a consolidated **`run_finalized`** IPC emit (the §11 payload) fired from `lifecycle/race.py` on every attempt-finalize; the engine **mints the `attempt_id`**. No network, no new deps. (It nearly does this already via `pb_export`, which emits the full mkwreplay for PBs — extend to every attempt with the richer fields.) In Phase 1 it also keeps its existing local `save_run` so the monitor is unaffected.
2. **Tauri app (`src-tauri/src/sync.rs`, Rust)** — a decoupled module (mirrors `discord.rs`): the existing sidecar-stdout reader routes `run_finalized` lines into it → persist to a **rusqlite outbox** (`outbox(attempt_id PK, payload TEXT, attempts INT, last_error TEXT, created_at)`) → a background task POSTs each unsent row with the bearer token → delete on `2xx`, else increment `attempts` + record `last_error` + back off + retry; flush on reconnect. Idempotent server upsert makes re-sends safe.
3. **App settings** — a "Sync" tab storing `server_url` + `auth_token` (Tauri settings), plus a connection indicator. Removing the one `sync::init()` line disables the feature with no other effect (same isolation property as the Discord plugin).

`POST /v1/runs/start` (the `run_started` ping) is fired by `sync.rs` when the engine emits its RACING-entered event.

## 15. Idempotency & provenance

- The **engine** mints a stable `attempt_id` (UUID) per attempt; the Rust outbox is keyed by it; the server upserts on it — so retries never duplicate.
- All client uploads are `provenance='live'`, so A's importer wipe-and-reload (only `legacy_import`/`carryover`) never disturbs live data — the two ingest paths coexist.

## 16. What stays vs moves (phasing)

| | Phase 1 (this spec / B) | Phase 2 (deferred) |
|---|---|---|
| **Engine** | adds `run_finalized` emit; keeps local race store for own-display | local race store (`replays`/`replay_points`/`replay_splits`) **removed**; pure detector |
| **Tauri app** | new `sync.rs` outbox + upload; Sync settings | adds read-back + local cache; renders own + friends' data |
| **Server** | source of truth (writes + reads + events) | unchanged (already authoritative) |
| **Monitor reads** | own data still from engine DB (unchanged) | repointed to server/app-cache |

## 17. Testing

- **TS** — `packages/db`: idempotent upsert, `is_pb` recompute, leaderboard/friends queries (temp SQLite). `apps/api`: route tests (Hono test client; auth required / anon reads), event-derivation, WS fan-out. One end-to-end: real Hono on a random port ← fake POST → assert DB row + emitted event.
- **Engine (Python)** — `run_finalized` emitted on finalize with the correct payload (every attempt, incl. reset); `attempt_id` stable.
- **Rust (`sync.rs`)** — outbox persist/flush/retry/backoff against a mock HTTP server; graceful no-op when `server_url`/token unset; survives restart (outbox is durable).

## 18. Execution staging

Two implementation plans share this spec:
1. **Server** (`pi/packages/{db,shared}` + `pi/apps/api`) — buildable/testable standalone (curl + TS tests) before any client change.
2. **Client write path** (engine `run_finalized` emit + `src-tauri/src/sync.rs` outbox/upload + Sync settings).

## 19. Decided judgment calls

- TS monorepo under `pi/`; `server/schema.sql` is the single schema source the TS `db` reads.
- **Server is the source of truth; the engine is a pure detector; the Tauri/Rust app does the pushing** (resilient outbox in **rusqlite**).
- Engine emits one consolidated **`run_finalized`** per attempt and mints the `attempt_id`; Rust forwards the payload opaquely.
- Token + server URL live in **app settings**; token stored server-side as a **sha256 hash**, minted by a Pi-side script.
- **cc = client setting (default 150)** until detection exists.
- `run_started` is an **ephemeral** event via a lightweight start ping.
- **Phase 1 keeps the engine's local race store** for monitor display; its removal + read-migration is **Phase 2**, out of scope here.
