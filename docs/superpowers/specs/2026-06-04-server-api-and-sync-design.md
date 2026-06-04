# Server, Identity & Sync — Design

**Date:** 2026-06-04
**Status:** Approved (design); pending implementation plan
**Component:** A new TypeScript/Node server on the Pi (HTTP API + WebSocket event hub) over the sub-project A SQLite store, per-player token auth, and the Python engine's client→server upload path (live push + offline outbox). This is sub-project **B** of the client→server shift. The OBS overlays + public website that consume this are sub-project **C**.

## 1. Goal

Stand up the home server that receives every friend's race attempts, stores them in the A canonical store, and pushes **event notifications** ("X started a run", "X PB'd, now #1", "X beat the WR") to live consumers — plus the public read API the website and clients use. Each client identifies itself with a per-player token; uploads survive a flaky network via a local outbox.

## 2. Non-goals (deferred)

- **Live in-progress telemetry** — streaming a remote friend's run *as it happens* (live position/splits). B builds the foundation (uploader + server + `WS /v1/events`) so this is a clean later addition (a second WS topic + a continuous stream from the engine), not a rework. The local player's live data already exists locally (engine IPC + `ipc/broadcaster.py`).
- **The OBS overlays + public website** that consume the API/events — sub-project **C**.
- **The WR scraper** (server-side ingestion of external WRs) — later; the `world_records` table is append-ready.
- **cc screen-detection** — until it exists, the client tags runs with a **cc setting (default 150)**.
- **Auth beyond a per-player bearer token** — no accounts/login/OAuth.

## 3. Stack decision & rationale

**TypeScript on Node**, not the legacy Python/FastAPI. The reasoning is impartial to familiarity, dev-effort, and reuse (all explicitly out of scope as deciding factors):

- The **interactive frontend is unavoidably JS/TS** — OBS overlays are browser sources and the public website is a browser app, both needing live WS-driven updates. That is a hard constraint, not a preference.
- The system's actual risk is **contract/type drift between what the API/WS emits and what the live frontend renders** — not throughput, memory, or concurrency, which are trivial at ~5 uploaders + modest public reads. A TS backend gives a **single compile-time-checked contract** spanning SQLite row → API response → WS event → website/overlay prop, eliminating that drift natively.
- Go's genuine strengths (single static binary, low memory, concurrency) target bottlenecks this project never hits, and force a two-language type boundary to the mandatory TS frontend. Python's only edges here were the discarded reuse/familiarity. Rust is the heaviest build for performance that isn't needed.

**Runtime:** Node (most battle-tested for an unattended 24/7 Pi service). Bun is a noted future option (built-in SQLite + WS, compiles to a standalone binary) if minimal-ops becomes a priority.
**Libraries:** **Hono** (HTTP API + WebSockets — tiny, fast, first-class TS) and **better-sqlite3** (synchronous, ideal for a single-process server). SvelteKit serves the website + overlays in **C**.

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

- **`server/schema.sql` stays the single source of truth.** `pi/packages/db` reads and applies it (idempotent `CREATE IF NOT EXISTS`); A's Python importer reads the same file. No second schema.
- **A's importer stays a one-shot Python CLI** (`python -m server.importer`), run on the Pi at cutover. It never needed to match the server language.
- A's Python `queries.py` remains used **only by the importer**. The TS `db` package **re-expresses the few runtime queries** (ingest upsert, `is_pb` recompute, leaderboards) over the same schema. Mild, intentional duplication separated by time (one-shot migration vs live serving).
- **The engine stays Python**, talking JSON to the API.

## 6. Architecture & data flow

```
 friend's PC (Python engine)        Pi — Node/TS, Cloudflare tunnel (HTTPS)      consumers (C)
 ┌───────────────────────┐  POST /v1/runs   ┌──────────────────────────────┐
 │ SyncClient + outbox DB │ ───(token)─────► │ apps/api (Hono)              │  WS /v1/events
 │  (mkw_tracker)         │  per attempt     │  • auth: token → player      │ ──────────────► OBS overlays
 │                        │ ◄─GET friends────│  • ingest → packages/db → SQLite                (SvelteKit, C)
 └───────────────────────┘   leaderboard    │  • derive events on ingest   │ ──────────────► website (C)
                                             │  • event hub → WS fan-out    │ ◄── GET /v1/leaderboard
                                             └──────────────────────────────┘
```

## 7. Connectivity & security

- **Cloudflare Tunnel** (already configured on the Pi for the legacy app) exposes the API at an HTTPS hostname — no router config, TLS terminated by Cloudflare.
- **Reads are public** (the website needs them; no secrets in leaderboards/WRs/events).
- **Writes are token-gated** — a client can only upload as the player its token maps to.
- Basic abuse protection is left to Cloudflare in front; no app-level rate limiting in B (revisit if the public site sees real traffic).

## 8. Identity / auth

- Add column `players.auth_token_hash TEXT UNIQUE` to `server/schema.sql` (A reserved "auth columns added in B"); fresh server DBs created at/after cutover include it.
- A Pi-side admin script (e.g. `pnpm --filter api mint-token <player-name>`) generates a random token, stores its **sha256 hash** on the player row, and prints the **plaintext token once** for the friend to paste into the app's settings.
- Requests authenticate with `Authorization: Bearer <token>`; the server hashes it, looks up the player, and attributes writes to that `player_id`. No token → reads only.
- The client stores its token in app config (the `SyncClient(server_url, auth_token)` constructor already takes it); it is configured during first-time setup.

## 9. Wire protocol — endpoints (`/v1`)

**Writes (require Bearer token):**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/runs` | Upload one attempt. Idempotent by client `attempt_id`. Server attributes `player` (token), `season` (active), `provenance='live'`; upserts; recomputes `is_pb`; derives + emits events. Returns `{is_pb, rank, gap_to_leader_ms, gap_to_wr_ms}`. |
| `POST` | `/v1/runs/start` | Ephemeral "run started" ping (`course`, `cc`, `attempt_id`) → emits `run_started`. No DB write. |

**Reads (public):**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/leaderboard?season&course&cc` | Per-(course, cc) leaderboard (each rostered player's PB, ordered). |
| `GET` | `/v1/leaderboard/overall?season&cc` | Aggregate standings (total time, points tiebreak). |
| `GET` | `/v1/friends-pbs?season&course&cc` | Roster's PBs for a course (the client's `fetch_friends_pbs`). |
| `GET` | `/v1/players/:id/pbs?season&cc` | One player's PBs across courses. |
| `GET` | `/v1/world-records?course&cc` | Current WR (+ history). |
| `GET` | `/v1/seasons` | Season list. |
| `GET` | `/health` | Liveness. |

**Live (public, read-only):** `WS /v1/events` — subscribe-only; server fans out event notifications.

## 10. Upload payload

```json
{
  "attempt_id": "uuid",            // client-assigned; idempotency key
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
Server stamps `player_id` (token), `season_id` (active), `provenance='live'`; maps `course` via the same slugify rule as A. Missing `cc` defaults to 150.

## 11. Ingest + event derivation (server, on `POST /v1/runs`)

1. Validate token + payload; resolve `course` slug → `course_id` (fail 400 on unknown).
2. **Upsert** the run by `attempt_id` (re-sends are no-ops/updates) with `provenance='live'`, plus its `run_laps` / `run_points`.
3. If `status='finished'`: recompute `is_pb` for `(active_season, player, course, cc)`.
4. Compute deltas vs the pre-upsert state: became a PB? new course #1 (lead change)? beats the current external WR?
5. Publish the resulting event(s) to the in-process **event hub**, which fans out to all `WS /v1/events` subscribers.
6. Resets/DNFs upsert silently (no events).

## 12. WS event types (`packages/shared`, typed)

| Event | Payload (essentials) |
|---|---|
| `run_started` | player, course, cc |
| `run_finished` | player, course, cc, total_time, is_pb, rank |
| `pb_achieved` | player, course, cc, total_time, delta_vs_prev_ms, rank |
| `lead_change` | course, cc, new_leader, prev_leader, total_time |
| `wr_beaten` | player, course, cc, total_time, wr_time |

## 13. Client half (`mkw_tracker`, Python)

- **`sync/client.py`** — implement `SyncClient`: `upload_attempt(payload) -> bool`, `fetch_friends_pbs(course) -> list`, `notify_start(...)`. Stdlib `urllib.request` with timeouts (zero new dependencies).
- **Outbox** — new local table `upload_outbox(attempt_id PRIMARY KEY, payload_json, created_at, attempts, last_error, sent)` in `mkw_tracker.db`. On each attempt-end the lifecycle saves locally (existing `save_run`) **and** enqueues the payload.
- **Sync-worker thread** — a daemon thread (mirrors the IPC daemon thread) drains the outbox: POST each unsent row; on success mark `sent`; on failure increment `attempts`, record `last_error`, back off, retry; flush on reconnect. Idempotent server upsert makes re-sends safe.
- **Wiring** — `lifecycle/race.py` already fires on start/finalize: on finalize → build payload + enqueue; on start → optional `notify_start`. On app start / course change → `fetch_friends_pbs` → existing `save_friend_pb` (friends' ghosts render on the monitor). The client *may* also subscribe to `WS /v1/events` to live-refresh friends' data (optional).
- **cc** — a client setting (default 150) included in the payload until screen-detection lands.

## 14. Idempotency & provenance

- The client mints a stable `attempt_id` (UUID) per attempt; the server upserts on it, so retries from the outbox never duplicate.
- All client uploads are `provenance='live'`, so the A importer's wipe-and-reload (which only touches `legacy_import`/`carryover`) never disturbs live data — the two ingest paths coexist cleanly.

## 15. Testing

- **TS** — `packages/db`: idempotent upsert, `is_pb` recompute, leaderboard/friends queries (temp SQLite). `apps/api`: route tests via Hono's test client (auth required/anon reads), event-derivation assertions, WS fan-out. One end-to-end: real Hono on a random port ← fake client POST → assert DB row + emitted event.
- **Python** — `SyncClient` against a mock HTTP server; outbox enqueue/flush/retry/backoff; payload construction; lifecycle wiring (on finalize → outbox row).

## 16. Execution staging

Larger than A; expected to become **two implementation plans** sharing this one spec:
1. **Server** (`pi/packages/{db,shared}` + `pi/apps/api`) — buildable and testable standalone (curl + the TS tests) before any client change.
2. **Client integration** (`mkw_tracker/sync` + outbox + lifecycle wiring) — depends on the server's API.

## 17. Decided judgment calls

- TS monorepo under `pi/`; `server/schema.sql` is the single schema source the TS `db` reads.
- Token stored as a **sha256 hash**, minted by a Pi-side admin script.
- **cc = client setting (default 150)** until detection exists.
- `run_started` is an **ephemeral event** via a lightweight start ping (kept).
- Client HTTP via **stdlib `urllib`** (no new dependency).
