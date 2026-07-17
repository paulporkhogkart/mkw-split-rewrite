# WR Service Fix Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four real defects the 2026-07-17 adversarial review of Plans 1+2 found — course
names that miss the minimap seeds on 7 of 30 courses, crash recovery that refunds the attempt a
crash must burn, a fixed 300s engine timeout with ~50s of margin on Rainbow Road, and a fragment
trail being accepted as done-forever — plus the approved Pi follow-up: `time_mismatch` becomes
terminal (revived by a changed video link) and dead jobs are flagged to a human instead of dying
silently.

**Architecture:** Tasks 1–4 are surgical changes inside `src-tauri/src/wr/` (Rust, Plan 2's
module). Tasks 5–6 are Pi-side (`pi/src/`, Plan 1's module): a claim-predicate change + a revival
hook in the scraper's backfill, then a `wr_job_dead` event → Discord embed + `wr-flags` CLI
listing. Task 7 syncs the spec/plan docs to the new reality. Every behavioural change lands with a
test that was WATCHED TO FAIL first — this codebase's review history (7 tests that passed
regardless of the behaviour they named) is why that rule is non-negotiable here.

**Tech Stack:** Rust (Tauri v2 app; inline `#[cfg(test)] mod tests`, `cargo test` from
`src-tauri/`), Node/TS via `tsx` (Hono + `node:sqlite`, vitest colocated, `npm test` from `pi/`).

## Global Constraints

- **Branch: `wr-fix-wave` off `main`, in the MAIN checkout — do NOT use a worktree.** The fixture
  video `temp/wr_mario_circuit.mp4` and the tuning DB `mkw_tracker.db` are gitignored and exist
  only here; a worktree severs the fixture test.
- **NEVER modify `mkw_tracker/` (the Python engine).** The Sky-High Sundae seed-row rename is a
  candidate ENGINE fix that Paul has NOT approved — this wave works around it client-side
  (Task 1) and documents the pairing (Task 7).
- **No `[dev-dependencies]` in `src-tauri/Cargo.toml`.** No mock-HTTP crates. Test pure functions;
  prove process behaviour with real child processes (the existing `EnginePath::Custom` pattern).
- **`temp/` and `*.mp4` are gitignored — never commit a video.** Stage only your named files with
  explicit `git add <paths>`; NEVER `git add -A`.
- **Pi tests are colocated** (`foo.ts` → `foo.test.ts`); source AND tests must stay
  `npx tsc --noEmit`-clean (non-gating but required).
- **Every failing-test step must be RUN and observed to fail before the fix step** — and the
  failure must be the interesting one (wrong value), not a compile error, wherever the old code
  still compiles.
- Commit trailer, on its own line: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Rust suite baseline: 93 passed + 1 ignored. Pi baseline: 594 passed. Both must only grow.

## Review evidence this plan is built on (do not re-derive)

- Engine minimap seeds/ROIs key on DETECTION-derived course names — template filename stem via
  `_`→space + `.title()` (`mkw_tracker/detection/templates.py:93`); courses are deliberately NOT
  canonicalized (`mkw_tracker/detection/selection.py:76`). Live DB keys verified 2026-07-17:
  `'Dk Spaceport'`, `'Dk Pass'`, `'Mario Bros Circuit'`, `'Toads Factory'`, `'Bowsers Castle'`,
  `'Warios Galleon'`, `'Great Block Ruins'`, and — the lone exception — `'Sky-High Sundae'`
  (hyphen, written by the engine migration `mkw_tracker/database/migrations.py:74`, unreachable
  by title-casing).
- The Pi's `course_name` is the canonical display name (`server/courses.py`): `"DK Spaceport"`,
  `"Mario Bros. Circuit"`, `"Toad’s Factory"` (curly U+2019)… Sending it verbatim finds NO seed
  on 7 of 30 courses → `seed()` never runs (`mkw_tracker/lifecycle/race.py:495-509`) → 0 points →
  `no_trail` → all 5 attempts burned.
- `releaseJob` refunds the attempt and only works on an UNEXPIRED lease (`pi/src/db/wrJobs.ts:132`).
  `process_one`'s crash recovery calls `release()` (`src-tauri/src/wr/service.rs:111`), so an app
  restart within the ~600s lease refunds the attempt the spec says a crash must burn (spec §4).
- Slowest current WR: Rainbow Road `3'53"260` = 233s (mkwrs, checked 2026-07-17) vs the fixed
  300s timeout at `service.rs:158`.
- The HUD timer and the minimap trail are read by INDEPENDENT trackers, so `verify()` can see an
  exact time with a fragment trail; `no_trail` currently fires only at exactly 0 points
  (`src-tauri/src/wr/verify.rs:40`), and a stored `wr_trails` row is permanently "done".

## File Structure

| File | Change |
|---|---|
| `src-tauri/src/wr/job.rs` | + `course_display_for_engine()`; drop dead `course_name` field |
| `src-tauri/src/wr/service.rs` | use it in `selections_for`; crash recovery stops releasing; `engine_timeout_for()` |
| `src-tauri/src/wr/verify.rs` | trail-duration floor |
| `pi/src/db/wrJobs.ts` | claim predicate excludes terminal `time_mismatch`; `deadJobs()` |
| `pi/src/wr/reconcile.ts` | video-link change revives a job |
| `pi/src/db/types.ts` | `wr_job_dead` ServerEvent |
| `pi/src/api/wrJobs.ts` + `pi/src/api/app.ts` | publish `wr_job_dead` on a killing failure |
| `pi/src/bot/embeds/jobDead.ts` + `pi/src/bot/dispatch.ts` | the Discord embed |
| `pi/src/scripts/wrFlags.ts` | list dead jobs |
| spec + plan docs | Task 7 |

---

### Task 1: Course names the engine can actually find (F1)

**Files:**
- Modify: `src-tauri/src/wr/job.rs` (add fn + tests; delete the `course_name` field)
- Modify: `src-tauri/src/wr/service.rs` (`selections_for` at :39-46; replace the
  `course_name_is_used_verbatim_not_derived_from_the_slug` test at :358-370)

**Interfaces:**
- Produces: `wr::job::course_display_for_engine(course_slug: &str) -> String` — used by
  `service::selections_for`. `WrJob` LOSES its `course_name` field (nothing else reads it;
  precedent: the dead `cc` field was deleted the same way in the Plan 2 fix wave).

- [ ] **Step 1: Write the failing tests**

Append to `src-tauri/src/wr/job.rs`'s `mod tests`:

```rust
    #[test]
    fn course_display_matches_the_engines_seed_keys_not_the_pi_names() {
        // The engine's minimap seeds key on DETECTION-derived names (filename stem via
        // `_`->space + title-case; courses are NOT canonicalized — selection.py:76).
        // The Pi's canonical names differ on 7 of 30 courses; each of these inputs is
        // one where sending the Pi's course_name verbatim finds NO seed (verified
        // against the real DB, 2026-07-17) and the run is a guaranteed no_trail.
        assert_eq!(course_display_for_engine("dk_spaceport"), "Dk Spaceport");
        assert_eq!(course_display_for_engine("dk_pass"), "Dk Pass");
        assert_eq!(course_display_for_engine("mario_bros_circuit"), "Mario Bros Circuit");
        assert_eq!(course_display_for_engine("toads_factory"), "Toads Factory");
        assert_eq!(course_display_for_engine("bowsers_castle"), "Bowsers Castle");
        assert_eq!(course_display_for_engine("warios_galleon"), "Warios Galleon");
        assert_eq!(course_display_for_engine("great_block_ruins"), "Great Block Ruins");
        // The 23 already-agreeing courses must keep working unchanged.
        assert_eq!(course_display_for_engine("mario_circuit"), "Mario Circuit");
        assert_eq!(course_display_for_engine("rainbow_road"), "Rainbow Road");
    }

    #[test]
    fn sky_high_sundae_is_the_one_hyphenated_seed_key_exception() {
        // Its seed row was written by the engine's own migration (migrations.py
        // _SEED_V2) as 'Sky-High Sundae' — a hyphen title-casing a filename can never
        // produce, so slug_to_display would miss it. Match the row as it exists on
        // every install.
        assert_eq!(course_display_for_engine("sky_high_sundae"), "Sky-High Sundae");
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src-tauri && cargo test wr::job`
Expected: FAIL — `cannot find function course_display_for_engine in this scope`.

- [ ] **Step 3: Implement**

Add to `src-tauri/src/wr/job.rs`, directly below `slug_to_display`:

```rust
/// slug -> the display name the ENGINE keys its minimap seeds/ROIs/thresholds on.
///
/// NOT the Pi's canonical display name. The engine's course names are detection-derived
/// (template filename stem via `_`->space + title-case, templates.py:93) and courses are
/// deliberately not canonicalized to their marketing names (selection.py:76) — so the
/// seed table keys on "Dk Spaceport" / "Mario Bros Circuit" / "Toads Factory", while the
/// Pi sends "DK Spaceport" / "Mario Bros. Circuit" / "Toad’s Factory" (server/courses.py).
/// Sending the Pi name verbatim finds no seed on 7 of 30 courses, the tracker never
/// seeds, and the run is a guaranteed no_trail (verified on the real DB, 2026-07-17).
///
/// `slug_to_display` reproduces the engine derivation for 29 of 30 courses. The single
/// exception is Sky-High Sundae: its seed row was written by the engine's own schema
/// migration (migrations.py _SEED_V2) with the hyphenated name, which title-casing a
/// filename can never produce — so it is matched literally, as the row exists on every
/// install. If the engine ever migrates that row to "Sky High Sundae" (which would also
/// fix live-detection seeding on that course — the same latent mismatch), DELETE this
/// exception in the same commit.
pub fn course_display_for_engine(course_slug: &str) -> String {
    match course_slug {
        "sky_high_sundae" => "Sky-High Sundae".into(),
        s => slug_to_display(s),
    }
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd src-tauri && cargo test wr::job`
Expected: PASS (11 tests).

- [ ] **Step 5: Write the failing service-level tests**

In `src-tauri/src/wr/service.rs`, REPLACE the whole
`course_name_is_used_verbatim_not_derived_from_the_slug` test (:358-370) — its comment argues for
exactly the broken behaviour — with:

```rust
    #[test]
    fn course_sent_to_the_engine_is_seed_key_shaped_not_the_pi_display_name() {
        // The Pi's canonical name is "DK Spaceport" (server/courses.py) but the engine's
        // seed row is 'Dk Spaceport' (detection-derived). Sending the Pi name verbatim
        // finds no seed -> guaranteed no_trail — the pre-fix behaviour this test kills.
        let j = job::parse_job(r#"{"wr_id":1,"course_slug":"dk_spaceport",
            "course_name":"DK Spaceport","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":null,"attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).course, "Dk Spaceport");
    }

    #[test]
    fn course_mapping_handles_the_sky_high_sundae_exception() {
        let j = job::parse_job(r#"{"wr_id":1,"course_slug":"sky_high_sundae",
            "course_name":"Sky-High Sundae","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":null,"attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).course, "Sky-High Sundae");
    }
```

- [ ] **Step 6: Run to verify the first fails for the RIGHT reason**

Run: `cd src-tauri && cargo test wr::service`
Expected: `course_sent_to_the_engine_is_seed_key_shaped_not_the_pi_display_name` FAILS with
`left: "DK Spaceport", right: "Dk Spaceport"` (the old code still compiles — this is a true
behavioural red). The sundae test passes coincidentally (verbatim matches the exception); that is
fine — it exists to pin the exception against the Step 7 change.

- [ ] **Step 7: Fix `selections_for` and delete the dead field**

In `src-tauri/src/wr/service.rs`, replace `selections_for` (:37-46):

```rust
/// The Pi sends slugs; the engine wants ITS OWN display names. For the course that means
/// the seed-key shape — job::course_display_for_engine, NOT the Pi's course_name (which
/// misses the seed table on 7 of 30 courses; see that function's doc). Characters, karts
/// and costumes are slug_to_display: consistent-by-construction with the engine's
/// filename-derived template keys.
fn selections_for(j: &job::WrJob) -> Selections {
    Selections {
        course: job::course_display_for_engine(&j.course_slug),
        character: job::slug_to_display(&j.character_slug),
        costume: j.costume_slug.as_deref().map(job::slug_to_display),
        kart: j.kart_slug.as_deref().map(job::slug_to_display),
    }
}
```

In `src-tauri/src/wr/job.rs`: delete `pub course_name: String,` from `WrJob` and the
`course_name: s("course_name")?,` line from `parse_job` (nothing else reads it — same precedent
as the deleted `cc`). The wire still carries it; we just no longer parse it. Leave the `LIVE`
test constant unchanged (unknown keys are ignored).

In `src-tauri/src/wr/service.rs`, the two remaining `selections_map_slugs_to_engine_display_names`
/ `a_base_costume_stays_none_so_set_selection_omits_it` tests keep their JSON (extra
`course_name` key is ignored) and their assertions (`mario_circuit` → "Mario Circuit" and
`choco_mountain` → "Choco Mountain" are identical under both mappings).

- [ ] **Step 8: Run the whole Rust suite**

Run: `cd src-tauri && cargo test`
Expected: PASS — 96 passed + 1 ignored (93 baseline − 1 replaced + 2 new in service + 2 new in
job… count may differ by exactly the arithmetic above; what matters is 0 failed and the two new
service tests green). `cargo build` must stay warning-free.

- [ ] **Step 9: Commit**

```bash
git add src-tauri/src/wr/job.rs src-tauri/src/wr/service.rs
git commit -m "fix(wr): send engine-derived course names so minimap seeds resolve on all 30 courses"
```

---

### Task 2: Crash recovery must burn the attempt, not refund it (F2)

**Files:**
- Modify: `src-tauri/src/wr/service.rs` (crash-recovery block at :106-115; the two orphan tests)

**Interfaces:**
- Consumes: `state::inflight` / `state::set_inflight` (unchanged).
- Produces: no API change — `process_one` simply stops calling `client.release()` on a
  crash-orphaned job.

- [ ] **Step 1: Write the failing test**

In `src-tauri/src/wr/service.rs` `mod tests`, add a connection-counting listener beside
`stalling_listener` and REPLACE the
`process_one_releases_and_clears_a_crash_orphaned_inflight_job_before_claiming` test:

```rust
    /// Accepts and immediately drops every connection, counting them. Each reqwest call
    /// = exactly one connection (a fresh connection that dies before any response is
    /// not retried by reqwest/hyper — retries only apply to reused pool connections).
    /// This is the only observation point for WHICH calls process_one makes without a
    /// mock server: 1 connection = claim only; 2 = a release snuck back in before it.
    fn counting_listener() -> (String, Arc<AtomicUsize>) {
        let l = TcpListener::bind("127.0.0.1:0").expect("bind probe listener");
        let addr = l.local_addr().unwrap();
        let total = Arc::new(AtomicUsize::new(0));
        let t = total.clone();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(stream) = stream else { break };
                t.fetch_add(1, SeqCst);
                drop(stream);
            }
        });
        (format!("http://{addr}"), total)
    }

    #[test]
    fn crash_orphan_is_cleared_locally_without_a_lease_release() {
        // A crash must BURN its attempt (spec §4: "a worker that dies without reporting
        // still burns one and a poison job can't retry forever"). release() REFUNDS the
        // attempt, and an app restart inside the ~600s lease window would make the
        // refund succeed — under autostart, a job whose video crashes the app would
        // claim -> crash -> refund -> claim forever. So crash recovery must clear the
        // LOCAL record only and let the lease lapse.
        let (url, total) = counting_listener();
        let dir = tmpdir("orphan_no_release");
        let conn = state::open(&dir).unwrap();
        state::set_inflight(&conn, Some(42));
        drop(conn);

        let cfg = ServiceCfg {
            server_url: url,
            token: "probe".into(),
            data_dir: dir.clone(),
            engine: EnginePath::Dev,
        };
        let _ = process_one(&cfg, &|| false);

        assert_eq!(total.load(SeqCst), 1,
            "expected exactly ONE request (the claim); a second one means the orphan \
             was release()d, which refunds the attempt a crash must burn");
        let conn2 = state::open(&dir).unwrap();
        assert_eq!(state::inflight(&conn2), None,
            "the stale local record must still be cleared, or every future call re-sees it");
    }
```

Keep `process_one_with_no_orphan_leaves_inflight_untouched_before_claiming` unchanged.

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::service::tests::crash_orphan_is_cleared_locally_without_a_lease_release -- --exact`
Expected: FAIL with `expected exactly ONE request (the claim)… left: 2, right: 1` — the current
code calls `release(42)` then `claim()`.

- [ ] **Step 3: Fix the crash-recovery block**

In `src-tauri/src/wr/service.rs`, replace the block at :106-115 (the comment AND the body):

```rust
    // Crash recovery: an inflight record left over means we died mid-job last time,
    // before reaching the set_inflight(None) at the bottom of this function. Clear the
    // LOCAL record only — deliberately do NOT release() the lease. release() refunds
    // the attempt (that is its job: a voluntary pause must not count against the cap),
    // but a crash is exactly what attempts-on-claim exists to count: "a worker that
    // dies without reporting still burns one and a poison job can't retry forever"
    // (spec §4). An app restart inside the ~600s lease window would make the release
    // succeed and refund — under Plan 3's autostart, a job whose video reliably
    // crashes the app would then claim -> crash -> refund -> claim forever. Letting
    // the lease lapse burns the attempt; the only cost is that one job waiting out the
    // rest of its lease (<= ~10 min) before it can be claimed again.
    if let Some(orphan_wr_id) = state::inflight(&conn) {
        log::warn!("[wr] crash-orphaned inflight wr_id={orphan_wr_id}: clearing the local \
                    record; its lease will lapse and the attempt stays burned (per spec)");
        state::set_inflight(&conn, None);
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::service`
Expected: PASS — all service tests, including the serialization test (its listener behaviour is
unchanged: one claim connection per call).

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/service.rs
git commit -m "fix(wr): crash recovery burns the attempt instead of refunding it"
```

---

### Task 3: Engine timeout derived from the record (F3)

**Files:**
- Modify: `src-tauri/src/wr/service.rs` (`run_job` at :156-158; new pure fn + tests)

**Interfaces:**
- Produces: `service::engine_timeout_for(record_ms: i64) -> std::time::Duration` (module-private).

- [ ] **Step 1: Write the failing test**

Append to `src-tauri/src/wr/service.rs` `mod tests`:

```rust
    #[test]
    fn engine_timeout_scales_with_the_record_within_the_lease() {
        // Mario Circuit (1:02.934): the 300s floor applies.
        assert_eq!(engine_timeout_for(62_934), Duration::from_secs(300));
        // Rainbow Road, the slowest board record (3'53"260 = 233s, mkwrs 2026-07-17):
        // the old fixed 300s left ~50s for intro + finish-still + startup — one
        // long-intro upload away from burning all 5 attempts on the marquee track.
        assert_eq!(engine_timeout_for(233_260), Duration::from_secs(413));
        // Never within 60s of the Pi's 600s lease: there is NO heartbeat, so the lease
        // must outlive the engine run or another machine can claim mid-processing.
        assert_eq!(engine_timeout_for(900_000), Duration::from_secs(540));
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::service`
Expected: FAIL — `cannot find function engine_timeout_for in this scope`.

- [ ] **Step 3: Implement and wire in**

Add above `run_job` in `src-tauri/src/wr/service.rs`:

```rust
/// Engine budget for one video, from the record itself. The engine is wall-clock bound
/// (~the video's duration) and a WR upload is ~the race plus a short menu intro and the
/// finish-still hold, so record + 180s covers every observed upload shape with room for
/// engine startup. Floor 300s keeps short courses generous; cap 540s stays a full minute
/// under the Pi's 600s lease — no heartbeat exists, so the lease must outlive the run or
/// another machine can claim the job while we are still processing it.
fn engine_timeout_for(record_ms: i64) -> std::time::Duration {
    std::time::Duration::from_secs(((record_ms / 1000) + 180).clamp(300, 540) as u64)
}
```

In `run_job`, replace the fixed timeout (:156-158):

```rust
    // Wall-clock bound (~the video's own length): budget from the record, not a constant
    // (Rainbow Road is 233s — a fixed 300s left it ~50s of margin, not "room to spare").
    let finalized = match engine::run_video(
        &cfg.engine, &dest, selections_for(j), engine_timeout_for(j.record_ms), cancel) {
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::service`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/service.rs
git commit -m "fix(wr): derive the engine timeout from the record instead of a fixed 300s"
```

---

### Task 4: Reject fragment trails (F5)

**Files:**
- Modify: `src-tauri/src/wr/verify.rs` (`verify` at :35-49; `fin` helper + new tests)

**Interfaces:**
- `verify()` signature unchanged; new rejection case returns `WrError::NoTrail`.

- [ ] **Step 1: Make the test fixture time-realistic**

In `src-tauri/src/wr/verify.rs` `mod tests`, replace `fin` so its points span a realistic race
duration instead of `t = 0..n-1` ms (which the new floor would rightly reject):

```rust
    /// `n` points spread across ~63s of race clock (matching the 1:02.934 the tests
    /// use). The old fixture packed all points into the first n MILLISECONDS, which a
    /// duration floor rightly rejects — a real trail spans the race.
    fn fin(total: Option<&str>, n: usize) -> Finalized {
        Finalized {
            total_time: total.map(str::to_string),
            status: Some("finished".into()),
            points: (0..n)
                .map(|i| [((i + 1) as f64) * 63_000.0 / (n as f64), 1635.0, 875.0, 0.79, 1.0])
                .collect(),
        }
    }
```

Run: `cd src-tauri && cargo test wr::verify`
Expected: PASS (7 tests) — proves the reshaped fixture changes no existing outcome BEFORE the
behaviour change lands.

- [ ] **Step 2: Write the failing test**

Append to the same `mod tests`:

```rust
    #[test]
    fn rejects_a_fragment_trail_that_stops_mid_race() {
        // The HUD timer and the minimap are read by INDEPENDENT trackers, so a run can
        // read the exact right total while the badge was lost for most of the race
        // (e.g. HDR washout, permanent LOST after lap 1). 500 points, all inside the
        // first 40% of the race, exact time match: without a duration floor this
        // uploads a stub and the wr_trails row permanently marks the job done.
        let mut f = fin(Some("1:02.934"), 500);
        for (i, p) in f.points.iter_mut().enumerate() {
            p[0] = ((i + 1) as f64) * (0.4 * 62_934.0) / 500.0;
        }
        assert_eq!(verify(&f, 62_934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn a_wrong_video_fragment_still_reports_time_mismatch_first() {
        // Mismatch is the more actionable diagnosis (wrong/mislinked video) and the Pi
        // treats it as terminal; the fragment floor must not mask it.
        let mut f = fin(Some("1:02.934"), 500);
        for (i, p) in f.points.iter_mut().enumerate() {
            p[0] = ((i + 1) as f64) * (0.4 * 62_934.0) / 500.0;
        }
        assert!(matches!(verify(&f, 62_000).unwrap_err(), WrError::TimeMismatch { .. }));
    }
```

- [ ] **Step 3: Run to verify the first fails**

Run: `cd src-tauri && cargo test wr::verify`
Expected: `rejects_a_fragment_trail_that_stops_mid_race` FAILS (verify returned Ok — the current
code accepts any non-empty trail with a matching time). The mismatch test passes already (pins
the ordering against Step 4).

- [ ] **Step 4: Implement the floor**

In `verify()` (`src-tauri/src/wr/verify.rs`), after the `detected_ms != expected_ms` check and
before `Ok(...)`:

```rust
    // Fragment floor. The timer (HUD digits) and the trail (minimap) come from
    // independent trackers, so an exact time does NOT prove the trail covers the race —
    // the badge can be lost mid-race and never re-acquired. A wr_trails row is
    // permanently "done", so a stub accepted here is a stub forever. Deliberately AFTER
    // the mismatch check: a fragment of the WRONG video is primarily wrong (terminal on
    // the Pi), only secondarily short.
    let last_t = f.points.last().map(|p| p[0]).unwrap_or(0.0);
    if last_t < 0.8 * expected_ms as f64 {
        return Err(WrError::NoTrail);
    }
```

- [ ] **Step 5: Run to verify everything passes**

Run: `cd src-tauri && cargo test wr::verify`
Expected: PASS (9 tests).

- [ ] **Step 6: Re-run the fixture test — the floor must not reject a REAL trail**

Run: `cd src-tauri && cargo test wr::engine::tests::fixture -- --ignored --nocapture`
Expected: PASS in ~77s, `total_time == "1:02.934"`, >1500 points (the real trail's last point is
at ~63129ms ≥ 0.8 × 62934 — comfortably above the floor). Do NOT relax the fixture assertions.

- [ ] **Step 7: Commit**

```bash
git add src-tauri/src/wr/verify.rs
git commit -m "fix(wr): reject fragment trails that stop mid-race instead of storing them forever"
```

---

### Task 5: `time_mismatch` is terminal on the Pi; a changed video link revives it

**Files:**
- Modify: `pi/src/db/wrJobs.ts` (claim predicate), `pi/src/db/wrJobs.test.ts`
- Modify: `pi/src/wr/reconcile.ts` (`backfill` at :40), `pi/src/wr/reconcile.test.ts`

**Interfaces:**
- No signature changes. Claim behaviour: a job whose `last_error` starts with `time_mismatch`
  is not offered, regardless of attempts. `backfill()` on a video-link change resets that job
  (`last_error=NULL, attempts=0`).

- [ ] **Step 1: Write the failing claim tests**

Append inside `describe('lease lifecycle', ...)` in `pi/src/db/wrJobs.test.ts`:

```ts
  it('a time_mismatch failure makes the job unclaimable — terminal, not retryable', () => {
    // Re-downloading the same wrong/mislinked video cannot change the verdict; without
    // terminality it burns all 5 attempts (~10 min + ~275MB on Rainbow Road) to reach
    // the same dead end. Spec §6.4's known gap, closed here.
    const db = queued(); claimJob(db, 'w1');
    expect(failJob(db, 10, 'w1', 'time_mismatch detected=62934 expected=62000')).toBe(true);
    expect(claimJob(db, 'w2')).toBeNull();
  });

  it('other failures stay retryable up to the attempts cap', () => {
    const db = queued(); claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'download_failed: HTTP 403');
    expect(claimJob(db, 'w2')).not.toBeNull();
  });
```

- [ ] **Step 2: Run to verify the first fails**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `expected { wr_id: 10, … } to be null` (the job is re-offered today).

- [ ] **Step 3: Extend the claim predicate**

In `pi/src/db/wrJobs.ts` `claimJob`, add one line to the WHERE (after the `attempts` filter):

```sql
         AND j.attempts < ?
         -- time_mismatch is TERMINAL for claiming: the video itself is wrong for this
         -- record, and re-downloading it cannot change that verdict. It needs a human —
         -- or a new link: reconcile's backfill() clears it when video_url changes.
         AND (j.last_error IS NULL OR j.last_error NOT LIKE 'time_mismatch%')
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: PASS (28 tests).

- [ ] **Step 5: Write the failing revival test**

Append to `pi/src/wr/reconcile.test.ts` (inside `describe('reconcile', ...)`), and add
`import { claimJob, failJob } from '../db/wrJobs';` to its imports:

```ts
  it('a changed video link revives a time_mismatch-dead job with fresh attempts', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/wrong' })]);
    const id = (db.prepare('SELECT id FROM world_records WHERE is_current=1').get() as any).id;
    expect(claimJob(db, 'w1')).toMatchObject({ wr_id: id });
    failJob(db, id, 'w1', 'time_mismatch detected=1 expected=2');
    expect(claimJob(db, 'w1')).toBeNull();                       // dead
    // Same record + holder -> Case 1 backfill; only the link changed.
    reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/corrected' })]);
    expect(db.prepare('SELECT attempts, last_error FROM wr_jobs WHERE wr_id=?').get(id))
      .toMatchObject({ attempts: 0, last_error: null });
    expect(claimJob(db, 'w1')).toMatchObject({ wr_id: id,
      video_url: 'https://youtu.be/corrected', attempt: 1 });    // alive again, fresh
  });
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd pi && npx vitest run src/wr/reconcile.test.ts`
Expected: FAIL — `expected null to deeply match { wr_id: … }` on the final claim (nothing clears
`last_error` today).

- [ ] **Step 7: Revive on link change in `backfill`**

In `pi/src/wr/reconcile.ts`, replace the `videoUrl` branch of `backfill` (:40):

```ts
  if (s.videoUrl && s.videoUrl !== row.video_url) {
    sets.push('video_url=?'); vals.push(s.videoUrl);
    // A changed link voids any prior processing verdict for this record — above all a
    // TERMINAL time_mismatch (wrong video linked): that terminality means "needs a human
    // or a new link", and this IS the new link. Attempts reset too: they were spent on
    // the old video. No-op when the WR has no job row.
    db.prepare(`UPDATE wr_jobs SET last_error=NULL, attempts=0, updated_at=datetime('now')
                WHERE wr_id=?`).run(row.id);
  }
```

- [ ] **Step 8: Run the wr + db suites, typecheck, commit**

Run: `cd pi && npx vitest run src/wr/ src/db/ && npx tsc --noEmit`
Expected: PASS, tsc clean.

```bash
git add pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts pi/src/wr/reconcile.ts pi/src/wr/reconcile.test.ts
git commit -m "feat(wr): time_mismatch is terminal; a corrected video link revives the job"
```

---

### Task 6: Dead jobs get flagged to a human (F4)

**Files:**
- Modify: `pi/src/db/types.ts` (ServerEvent union), `pi/src/db/wrJobs.ts` (+`deadJobs`),
  `pi/src/db/wrJobs.test.ts`, `pi/src/api/wrJobs.ts` (+hub param + publish),
  `pi/src/api/app.ts` (:61), `pi/src/api/wrJobs.test.ts`, `pi/src/bot/dispatch.ts`,
  `pi/src/bot/dispatch.test.ts`, `pi/src/scripts/wrFlags.ts`
- Create: `pi/src/bot/embeds/jobDead.ts`

**Interfaces:**
- Produces: `deadJobs(db) → DeadJob[]` where
  `DeadJob = { wr_id: number; course: string; holder_name: string | null; record_str: string; attempts: number; last_error: string | null }`;
  ServerEvent member
  `{ type: 'wr_job_dead'; wr_id: number; course: string; holder: string | null; record_str: string; reason: string; attempts: number }`;
  `wrJobsRoutes(db, hub)` (was `(db)`); `buildJobDeadEmbed(d)`.

- [ ] **Step 1: Write the failing `deadJobs` tests**

Append a new describe to `pi/src/db/wrJobs.test.ts`, adding `deadJobs` to the wrJobs import and
`import { insertWrTrail } from './wrTrails';` if not already present (it is — Task 6 of Plan 1):

```ts
describe('deadJobs', () => {
  const dead = () => { const db = setup(); addWr(db, 10); seedWrJobs(db); return db; };

  it('lists a job at the attempts cap', () => {
    const db = dead();
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    expect(deadJobs(db)).toMatchObject([{ wr_id: 10, course: 'Mario Circuit', attempts: 5 }]);
  });

  it('lists a terminal time_mismatch even below the cap', () => {
    const db = dead(); claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'time_mismatch detected=1 expected=2');
    expect(deadJobs(db)).toMatchObject([{ wr_id: 10, attempts: 1 }]);
  });

  it('does not list healthy or already-trailed jobs', () => {
    const db = dead();
    expect(deadJobs(db)).toEqual([]);                            // healthy
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    insertWrTrail(db, 10, [{ t_ms: 1, cx: 1, cy: 1, score: 0.9, lap: 1 }]);
    expect(deadJobs(db)).toEqual([]);                            // done is not dead
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `deadJobs is not a function`.

- [ ] **Step 3: Implement `deadJobs`**

Append to `pi/src/db/wrJobs.ts`:

```ts
export type DeadJob = { wr_id: number; course: string; holder_name: string | null;
  record_str: string; attempts: number; last_error: string | null };

/** Jobs that will never be claimed again without a human: at the attempts cap, or
 *  terminally time_mismatched — and still trail-less. Spec §6.4's "cap reached; flag for
 *  Paul": this is what `npm run wr-flags` prints and what the wr_job_dead alert announces. */
export function deadJobs(db: DatabaseSync): DeadJob[] {
  return db.prepare(
    `SELECT j.wr_id, c.display_name AS course, w.holder_name, w.record_str,
            j.attempts, j.last_error
     FROM wr_jobs j
     JOIN world_records w ON w.id = j.wr_id
     JOIN courses c ON c.id = w.course_id
     WHERE NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = j.wr_id)
       AND w.removed_at IS NULL
       AND (j.attempts >= ? OR j.last_error LIKE 'time_mismatch%')
     ORDER BY j.updated_at DESC`
  ).all(MAX_ATTEMPTS) as DeadJob[];
}
```

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts` → PASS (31 tests).

- [ ] **Step 4: Add the event type**

In `pi/src/db/types.ts`, append to the `ServerEvent` union after the `wr_name_flag` member:

```ts
  | { type: 'wr_job_dead'; wr_id: number; course: string; holder: string | null;
      record_str: string; reason: string; attempts: number };
```

- [ ] **Step 5: Write the failing route test**

In `pi/src/api/wrJobs.test.ts`, rework `setup()` to capture events and return them (mirror
`reconcile.test.ts`): construct `const hub = new EventHub(); const events: ServerEvent[] = [];
hub.subscribe((e) => events.push(e)); const app = createApp(db, hub);` and add `events` to the
returned object (`import type { ServerEvent } from '../db/types';`). Then append:

```ts
  it('announces wr_job_dead when a failure kills the job (cap reached)', async () => {
    const { db, app, w1, events } = setup();
    db.prepare('UPDATE wr_jobs SET attempts=4 WHERE wr_id=10').run();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });   // attempts -> 5
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'timeout' }),
    });
    expect(events.filter((e) => e.type === 'wr_job_dead')).toMatchObject([
      { wr_id: 10, course: 'Mario Circuit', holder: 'JaK', reason: 'timeout', attempts: 5 },
    ]);
  });

  it('announces wr_job_dead immediately on a terminal time_mismatch', async () => {
    const { app, w1, events } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'time_mismatch detected=1 expected=2' }),
    });
    expect(events.filter((e) => e.type === 'wr_job_dead')).toHaveLength(1);
  });

  it('does not announce for a survivable failure', async () => {
    const { app, w1, events } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'download_failed: 403' }),
    });
    expect(events.filter((e) => e.type === 'wr_job_dead')).toEqual([]);
  });
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd pi && npx vitest run src/api/wrJobs.test.ts`
Expected: the two announce tests FAIL (`expected [] to …`); the survivable one passes vacuously —
it exists to pin the negative against Step 7.

- [ ] **Step 7: Publish from the route**

In `pi/src/api/wrJobs.ts`:
- imports: add `import type { EventHub } from './events';` and `deadJobs` to the wrJobs import.
- signature: `export function wrJobsRoutes(db: DatabaseSync, hub: EventHub): Hono<Env> {`.
- in the `/result` failure branch, replace the last two lines:

```ts
    const recorded = failJob(db, wrId, worker, body.error ?? 'unknown');
    if (recorded) {
      // Did that failure kill the job (attempts cap, or terminal time_mismatch)? Then a
      // human is the only thing that can move it — spec §6.4 "cap reached; flag for
      // Paul". Same predicate `npm run wr-flags` prints, so alert and listing agree.
      const dead = deadJobs(db).find((d) => d.wr_id === wrId);
      if (dead) {
        hub.publish({ type: 'wr_job_dead', wr_id: dead.wr_id, course: dead.course,
          holder: dead.holder_name, record_str: dead.record_str,
          reason: dead.last_error ?? 'unknown', attempts: dead.attempts });
      }
      return c.json({ ok: true });
    }
    return c.json({ error: 'not the lease owner' }, 409);
```

In `pi/src/api/app.ts` (:61): `app.route('/', wrJobsRoutes(db, hub));`

Run: `cd pi && npx vitest run src/api/wrJobs.test.ts` → PASS (15 tests).

- [ ] **Step 8: The Discord embed + dispatch**

Create `pi/src/bot/embeds/jobDead.ts`:

```ts
import { EmbedBuilder } from 'discord.js';

export type JobDeadData = { course: string; holder: string | null; record_str: string;
  reason: string; attempts: number };

/** Red alert: a WR trail job exhausted its attempts (or hit a terminal time_mismatch)
 *  and will never retry on its own. Needs a human — or, for a mislinked video, a
 *  corrected link on mkwrs (reconcile revives the job automatically when the link
 *  changes). */
export function buildJobDeadEmbed(d: JobDeadData): EmbedBuilder {
  return new EmbedBuilder()
    .setColor(0xef4444)
    .setTitle('WR TRAIL JOB DEAD')
    .setDescription(`Trail extraction for **${d.course}** (${d.record_str}${d.holder ? ` by ${d.holder}` : ''}) gave up and will not retry on its own.`)
    .addFields(
      { name: 'Last error', value: `\`${d.reason.slice(0, 200)}\``, inline: false },
      { name: 'Attempts', value: String(d.attempts), inline: true },
    )
    .setFooter({ text: d.reason.startsWith('time_mismatch')
      ? 'Likely a wrong/mislinked video — revives automatically if mkwrs corrects the link. npm run wr-flags lists it.'
      : 'npm run wr-flags lists dead jobs.' });
}
```

In `pi/src/bot/dispatch.ts`: add `import { buildJobDeadEmbed } from './embeds/jobDead';` and a
branch after the `wr_name_flag` one:

```ts
    } else if (ev.type === 'wr_job_dead') {
      send(buildJobDeadEmbed({ course: ev.course, holder: ev.holder,
        record_str: ev.record_str, reason: ev.reason, attempts: ev.attempts }));
    }
```

Append to `pi/src/bot/dispatch.test.ts`:

```ts
  it('announces a dead WR trail job', () => {
    const db = openDb(':memory:'); applySchema(db);
    const sent: any[] = [];
    dispatch(db, { type: 'wr_job_dead', wr_id: 10, course: 'Mario Circuit', holder: 'JaK',
                   record_str: '1:02.934', reason: 'time_mismatch detected=1 expected=2',
                   attempts: 1 }, (e) => sent.push(e));
    expect(sent).toHaveLength(1);
    expect(sent[0].data.title).toBe('WR TRAIL JOB DEAD');
    expect(sent[0].data.footer.text).toContain('mkwrs corrects the link');
  });
```

Run: `cd pi && npx vitest run src/bot/dispatch.test.ts` → PASS.

- [ ] **Step 9: List dead jobs in the wr-flags CLI**

Replace `pi/src/scripts/wrFlags.ts` entirely:

```ts
import { openDb, applySchema } from '../db/connect';
import { resolveFlags, reportFlags } from '../wr/flags';
import { backfillSlugs } from '../wr/backfillSlugs';
import { deadJobs } from '../db/wrJobs';

const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const resolved = resolveFlags(db);
const filled = backfillSlugs(db);
if (resolved) console.log(`resolved ${resolved} flag(s)`);
if (filled) console.log(`backfilled slugs on ${filled} world_records row(s)`);
console.log(reportFlags(db));

const dead = deadJobs(db);
if (dead.length) {
  console.log(`\n${dead.length} dead WR trail job(s) — will not retry without a human (or a corrected mkwrs link):`);
  for (const d of dead) {
    console.log(`  wr_id=${d.wr_id} ${d.course} ${d.record_str}${d.holder_name ? ` by ${d.holder_name}` : ''} — attempts=${d.attempts} last_error=${d.last_error ?? '-'}`);
  }
}
```

Run: `cd pi && MKW_DB=":memory:" npx tsx src/scripts/wrFlags.ts`
Expected: prints the flags report and exits 0 (no dead-jobs block on an empty DB).

- [ ] **Step 10: Full Pi suite, typecheck, commit**

Run: `cd pi && npx vitest run && npx tsc --noEmit`
Expected: PASS (601+ tests), tsc clean.

```bash
git add pi/src/db/types.ts pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts pi/src/api/wrJobs.ts pi/src/api/app.ts pi/src/api/wrJobs.test.ts pi/src/bot/embeds/jobDead.ts pi/src/bot/dispatch.ts pi/src/bot/dispatch.test.ts pi/src/scripts/wrFlags.ts
git commit -m "feat(wr): flag dead trail jobs — Discord alert + wr-flags listing"
```

---

### Task 7: Documentation sync

**Files:**
- Modify: `docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md`
- Modify: `docs/superpowers/plans/2026-07-15-wr-service-core.md` (banner only)

No tests; correctness is textual. One commit.

- [ ] **Step 1: Spec §6.4 — retry table + the closed gap**

Replace the attempts table row `| 5 | cap reached; flag for Paul |` with
`| 5 | cap reached; job is dead — announced via the wr_job_dead Discord alert and listed by npm run wr-flags |`.

Replace the entire `**KNOWN GAP (server-side, unfixed):** …` paragraph with:

```markdown
**CLOSED 2026-07-17:** `time_mismatch` is now TERMINAL on the Pi — the claim predicate skips any
job whose `last_error` starts with `time_mismatch` (`db/wrJobs.ts`), so a wrong/mislinked video
no longer burns its remaining attempts re-downloading. The job revives automatically (attempts
reset) when the scraper sees the video link change (`reconcile.ts backfill()`), and a killing
failure — cap reached or terminal mismatch — fires a `wr_job_dead` Discord alert plus a
`npm run wr-flags` listing. `tier_for` remains blind to WHY a prior attempt failed (the claim
payload still carries no `last_error`); with a single tier that is moot, and any future
escalation tier must revisit it.
```

- [ ] **Step 2: Spec §1.3 — the course-name rule**

Append a bullet to §1.3 ("Other engine facts the service must respect"):

```markdown
- **set_selection course names must be the ENGINE's detection-derived names, not the Pi's
  canonical display names.** Seeds/ROIs key on filename-derived names (`_`→space + `.title()`;
  courses are deliberately not canonicalized — `selection.py:76`): `Dk Spaceport`,
  `Mario Bros Circuit`, `Toads Factory`… The one exception is `Sky-High Sundae`, whose seed row
  was migration-written with the hyphen. `job.rs::course_display_for_engine` owns this mapping;
  sending the Pi's `course_name` verbatim finds no seed on 7 of 30 courses (found 2026-07-17).
  Related latent ENGINE issue (not fixed here): live detection says "Sky High Sundae", so that
  course's seed row is unreachable in live tracking too; if the engine ever migrates the row,
  delete the client-side exception in the same commit.
```

- [ ] **Step 3: Spec §6.5 + §6.6 + §6.3 honesty fixes**

- §6.5 pause table, Mid-download row: replace with
  `| Mid-download | The in-flight yt-dlp run completes (~10s), then the lease is release()d before processing starts — a pause is honoured at the next boundary, not by suspending the transfer |`
- §6.6, after the splits sentence, append: *(v1 verifies the exact total only; `lap_splits_ms`
  arrives in the claim payload but is not yet checked.)*
- §6.3 autostart bullet: append: **Caveat found 2026-07-17: `App.svelte`'s `onMount`
  unconditionally invokes `start_tracker` (src/App.svelte:1436), so ANY window creation — hidden
  included — currently spawns the live engine and opens the camera. Plan 3 must gate that call;
  "a hidden start touches no camera" is a requirement on Plan 3, not a property the app already
  has.**
- §6.4 work-loop step 3: replace `hard timeout at ~3× video duration` with
  `hard timeout derived from the record: clamp(record + 180s, 300s, 540s) — must stay under the
  600s lease, which is never heartbeated`.
- §6.6 failure-reason list: note `no_trail` also covers a fragment trail (last point < 80% of the
  record) — an exact time with a mid-race badge loss must not store a stub forever.

- [ ] **Step 4: Plan 2 doc errata banner**

Insert directly under the H1 of `docs/superpowers/plans/2026-07-15-wr-service-core.md`:

```markdown
> **ERRATA (2026-07-17) — this plan's code samples predate the final-review fix waves and the
> fix-wave plan `2026-07-17-wr-fix-wave.md`. The SHIPPED code is the truth, not these samples.**
> Known-stale in the samples below: the 4K `Downscaled4k` tier (DELETED — spec §6.4); Task 6's
> `run_video` showing timeout/cancel checks INSIDE the read loop and `stderr(Stdio::null())`
> (both forbidden by its own prose — shipped code uses a watchdog thread + a drained, bounded
> stderr pipe); Task 7's `course_name`-verbatim selections (misses the minimap seeds on 7 of 30
> courses — shipped code maps `course_display_for_engine`). Rebuilding from these samples
> reintroduces three known-fatal bugs.
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md docs/superpowers/plans/2026-07-15-wr-service-core.md
git commit -m "docs(wr): sync spec to the fix wave; errata banner on the stale Plan 2 samples"
```

---

## Final verification (controller, after all tasks)

1. `cd src-tauri && cargo test` → 0 failed, 1 ignored; `cargo build` and `cargo build --release`
   → zero warnings.
2. `cd src-tauri && cargo test wr::engine::tests::fixture -- --ignored --nocapture` → PASS,
   `1:02.934`, >1500 points, unrelaxed.
3. `cd pi && npx vitest run && npx tsc --noEmit` → 0 failed, clean.
4. Mutation spot-checks (break → observe the named test fail → restore, tree byte-identical):
   `course_display_for_engine` exception removed → sundae tests fail; crash-recovery release
   re-added → `crash_orphan_is_cleared_locally_without_a_lease_release` fails with `left: 2`;
   claim predicate's `time_mismatch` line removed → terminal test fails; `deadJobs` predicate
   `>=` flipped → cap test fails.
5. Then `superpowers:finishing-a-development-branch` (merge to `main`, no push, no tag).

## What this wave deliberately does not do

- **No engine (`mkw_tracker/`) changes.** The Sky-High Sundae seed-row rename (which would fix
  live-detection seeding on that course AND let the client drop its exception) is a separate
  decision for Paul.
- **No heartbeat wiring** — with the 540s timeout cap the 600s lease still outlives every run.
- **No yt-dlp cancel/timeout** (review F6) — Plan 3's loop owns responsiveness around the
  download step.
- **No Plan 3 / Plan 4 work.**
