# Phase 2b — Rust read-back + cache Implementation Plan

> Executed inline. Spec: `docs/superpowers/specs/2026-06-06-server-phase2-read-migration-design.md` §2b.

**Goal:** A `sync_course_reads(course)` Tauri command that fetches the course's PB splits + trails + friends-PBs from the server (with the token), caches the combined payload in rusqlite, and serves the last cache when offline. Course cache is cleared after a matching own upload.

**Files:** `src-tauri/src/sync.rs` (helpers + command + init + drain-loop clear; tests inline), `src-tauri/src/lib.rs` (register command). Tests: `cargo test sync::` from `src-tauri/`.

### Task 1: `course_cache` helpers + `course_slug_of` (TDD)
- Failing test (in `mod tests`): put→get roundtrip, overwrite, clear; `course_slug_of` parses+slugifies the `course` field (None when absent/blank).
- Implement `ensure_course_cache`, `course_cache_put` (upsert), `course_cache_get`, `course_cache_clear`, `course_slug_of`.
- `cargo test sync::` green.

### Task 2: `sync_course_reads` command + init + drain-loop invalidation
- `ensure_course_cache(&conn)` in `init` (after `ensure_pb_cache`).
- `EMPTY_COURSE_READS` const = `{"pb_splits":{"total_ms":null,"splits":{}},"trails":[],"friends_pbs":[]}`.
- `async fn fetch_course_reads(cfg, course) -> Result<String,String>`: GET `/v1/me/pb-splits` (bearer), `/v1/trails` (bearer, for is_me), `/v1/friends-pbs` (public), each `?course=<course>&cc=150`, parse via `text()` + `serde_json::from_str` (no reqwest json feature needed), combine `{pb_splits, trails, friends_pbs}`; Err on any non-2xx/network error.
- `#[tauri::command] pub async fn sync_course_reads(course) -> String`: empty config → cached/empty; else fetch → on Ok cache+return, on Err return cached/empty. Never holds the OUTBOX mutex across an await.
- Drain loop: after a successful upload `outbox_delete`, also `course_cache_clear` for `course_slug_of(&line)` so the next read re-fetches the new PB/trail.
- Register `sync::sync_course_reads` in `lib.rs` `invoke_handler`.
- `cargo test sync::` green; the HTTP orchestration isn't unit-tested (no mock-server infra, consistent with the existing untested fetch paths).

### Task 3: commit + ff-merge to main.
