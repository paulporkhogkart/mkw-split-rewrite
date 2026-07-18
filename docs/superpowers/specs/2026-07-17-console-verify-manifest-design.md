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
- `merge_foreign(out_dir, primary_path) -> (added: int, backup_path: str | None, stale_skipped: list)`
  — additive-only: every status-done entry present in a foreign manifest and absent from the
  primary is copied in **only when its core frames exist on the share** — a foreign "done" whose
  frames are gone is STALE (e.g. the primary was deliberately cleared to force a re-matte) and
  absorbing it would make `process_all` skip the clip (the resurrect hazard, review wave
  2026-07-18). The primary always wins on conflicts; nothing is ever removed; both audit and
  merge select from the SAME union (primary wins; among foreign files a done entry wins over a
  non-done one) so the report can never disagree with the merge. Timestamped `shutil.copy2`
  backup first (skipped when the primary doesn't exist yet — a fresh box is a normal state);
  atomic `.tmp` + `os.replace` write. `(0, None, stale)` when nothing was mergeable.
- `format_report(audit_result) -> list[str]` — stable human lines shared by console + CLI, ending
  with `pending for next Process run: N (S standalones + K karts)`.
- `run_for_console(clips_dir, out_dir, primary_path, processing_active, claims_dir=None)` —
  orchestrator: audit → report; if `foreign_only` is non-empty: merge when not processing, else
  warn (`process_all` holds the manifest in memory and rewrites the whole file after every clip —
  a mid-run merge would be clobbered by its next save). `processing_active` may be a bool or a
  ZERO-ARG CALLABLE evaluated right before the write (the audit scan can be SMB-slow; a
  click-time snapshot goes stale — review wave 2026-07-18). Handles the `(0, None, stale)` merge
  result and reports stale-skipped names.
- `audit(..., claims_dir=None)` — when given (multi-machine sweeps), `pending` subtracts claimed
  clips via `claims.pending_names`, matching `process_all`'s real pending set exactly.
- CLI `main()` — `--clips/--out/--manifest/--claims-dir`; **audit-only by default**, `--merge` to
  opt in (a headless run cannot see whether `process_all` is mid-run on this box).

### Console button (`tools/sweep_console/app.py`)

"Verify manifest" beside "Build viewer", same pattern: always-clickable, `_verifying` re-entry
guard, worker thread (never the Tk thread), report lines into the `process` log pane. Audits
against `SHIP_DIR or PROCESS_OUT` (Build-viewer precedent — on the ship-and-delete box the
frames and foreign manifests live on the share, review wave 2026-07-18) with the box-local
`PROCESS_MANIFEST` as primary and `CLAIMS_DIR` for pending accuracy. Two guards close the
click-Process-mid-verify clobber: the Process button is LOCKED while verifying
(`_refresh_buttons` honors `_verifying`), and `processing_active` is passed as a live
`lambda: self.pstate.state != ps.IDLE` evaluated at merge time (PAUSED also blocks — resume
relaunches `process_all`, which reloads the manifest and picks merged entries up anyway).
`asset_matte` is already on `app.py`'s `sys.path`. One button serves both machines: paths come
from `procconfig` per machine.

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
