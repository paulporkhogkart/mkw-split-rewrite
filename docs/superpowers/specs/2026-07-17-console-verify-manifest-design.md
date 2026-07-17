# Console "Verify manifest" Button — Design

**Date:** 2026-07-17
**Status:** Approved (user, same-day follow-up to the char nameplate merge `dd43969`)
**Branch:** `console-verify-manifest`
**Builds on:** the 2026-07-17 share-bookkeeping audit: 259 kart combos + 5 standalones had matte
frames on the share but no entry in ANY manifest (ship-per-clip vs publish-at-run-end drift), and
`process_all` computes its pending set from the PRIMARY manifest only, so entries recorded only in
another box's `manifest.<machine>.json` would be pointlessly re-matted.

## Policy (user)

**Everything ends up in the one primary manifest** (`<out>/manifest.json` of the machine you run
on). Foreign per-machine manifests are publications to be absorbed, not co-equal sources.

## Design

### `tools/asset_matte/manifest_verify.py` — pure module (build python, no GPU imports)

- `audit(clips_dir, out_dir, primary_path) -> dict` — cross-checks every `*.mkv` clip name against
  the union of `<out_dir>/manifest*.json` (skipping any file with `.bak` in its name) and the
  matte dirs on disk (`has_core_frames` = both `<name>__idle_frames` and `<name>__flourish_frames`
  exist under `<out_dir>/matte`). Returns lists (names, sorted):
  - `unrecorded_with_frames` — frames on share, entry in NO manifest (the drift class)
  - `unrecorded_no_frames` — in no manifest, no frames (genuinely never processed)
  - `foreign_only` — status-done entry only in a non-primary manifest
  - `missing_frames` — union-status done but core frame dirs absent
  - `missing_idle_resume` — KART entries (≥3 name parts) union-status done without the key
  - `status_not_done` — recorded anywhere with status ≠ done
  - `pending` — clips the PRIMARY manifest does not mark done (what Process would run)
- `merge_foreign(out_dir, primary_path) -> (added: int, backup_path: str | None)` — additive-only:
  every status-done entry present in a foreign manifest and absent from the primary is copied in;
  the primary always wins on conflicts; nothing is ever removed. Timestamped backup of the primary
  is written first; the write is atomic (`.tmp` + `os.replace`). Returns `(0, None)` when there is
  nothing to merge (no backup written).
- `format_report(audit_result) -> list[str]` — stable human lines shared by console + CLI, ending
  with `pending for next Process run: N (S standalones + K karts)`.
- `run_for_console(clips_dir, out_dir, primary_path, processing_active) -> list[str]` — orchestrator:
  audit → report; if `foreign_only` is non-empty: merge when `processing_active` is False (then
  re-audit so the report reflects the merged state), else emit a warning that the merge was
  skipped (`process_all` holds the manifest in memory and rewrites the whole file after every
  clip — a mid-run merge would be clobbered by its next save).
- CLI `main()` — `--clips/--out/--manifest` (defaults mirror `process_all`), `--no-merge` for
  audit-only; prints the same report lines. For headless / cron use.

### Console button (`tools/sweep_console/app.py`)

"Verify manifest" beside "Build viewer", same pattern: always-clickable, `_verifying` re-entry
guard, worker thread (never the Tk thread), each report line appended to the `process` log pane
via `_on_line` (already thread-safe). `processing_active = self.pstate.state != ps.IDLE`
(conservative: PAUSED also blocks the merge — resume relaunches `process_all`, which reloads the
manifest and then picks the merged entries up anyway). `asset_matte` is already on `app.py`'s
`sys.path`. One button serves both machines: paths come from `procconfig` per machine.

### Explicitly report-only

`missing_idle_resume` and `missing_frames` are REPORTED, not healed (healing = re-matte; today's
audit found zero of either among recorded entries). No frame-dir deletion anywhere in this feature.

## What does not change

`process_all`, claims, `make_viewer` (it keeps unioning `manifest*.json` — after a merge the union
is simply dominated by the primary), the rig/sweep halves of the console.

## Testing

`tests/test_manifest_verify.py` with synthetic tmp-dir manifests + matte dirs: every audit
classification; merge semantics (foreign-done added, non-done skipped, primary precedence,
backup + atomicity, `.bak` files ignored, `(0, None)` no-op); `run_for_console` merge-vs-skip on
`processing_active`; the pending standalone/kart split. Button wiring stays thin per the
Build-viewer precedent (untested Tk glue).
