"""Share-bookkeeping audit + one-manifest merge (spec 2026-07-17-console-verify-manifest-design).

The sweep ships matte frames + marks claims PER CLIP, but each box's manifest reaches the share
only as an end-of-run snapshot (best-effort) — so frames can exist with no manifest entry
anywhere (259 kart combos + 5 standalones found 2026-07-17), and entries recorded only in another
box's manifest.<machine>.json are invisible to process_all's pending set (it reads the PRIMARY
manifest only). Policy: everything ends up in the ONE primary manifest.

  python tools/asset_matte/manifest_verify.py --clips D:\\kartoff\\captures_sdr\\en_uk\\clips \\
      --out D:\\kartoff\\asset_chips                    # audit only (default)
  ... --merge                                           # also absorb foreign manifests

Merge safety: process_all holds its manifest in memory and rewrites the WHOLE file after every
clip, so a merge landing mid-run is silently reverted by its next save. The console button
therefore re-checks processing state right before writing (callable `processing_active`) and
locks the Process button while verifying; the CLI defaults to audit-only because a headless run
cannot see whether process_all is mid-run on this box — pass --merge only when you know it is not.
Stale foreign entries (status done but no frames on the share — e.g. the primary was deliberately
cleared to force a re-matte) are never absorbed: merging them would make process_all skip clips
that need re-processing.

Pure build python (stdlib + the GPU-free claims module).
"""
import argparse
import glob
import json
import os
import shutil
import time

import claims                     # atomic per-clip coordination; same pending semantics as process_all

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _clip_names(clips_dir):
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(clips_dir, "*.mkv")))


def _load(path):
    """Manifest dict, {} when missing/corrupt. Non-dict rows (a hand-edited null, a stray
    string) are dropped rather than crashing the audit — this tool exists FOR messy books."""
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    return {n: e for n, e in raw.items() if isinstance(e, dict)}


def _foreign_paths(out_dir, primary_path):
    """Every non-primary manifest*.json in out_dir; backups (anything with '.bak') excluded."""
    primary = os.path.abspath(primary_path)
    return sorted(p for p in glob.glob(os.path.join(out_dir, "manifest*.json"))
                  if os.path.abspath(p) != primary and ".bak" not in os.path.basename(p))


def _union(primary, foreign_paths):
    """One entry per name: the primary ALWAYS wins; among foreign files a status-done entry
    wins over a non-done one (sorted file order breaks remaining ties). merge_foreign selects
    from this same union, so the report and the merge can never disagree about an entry."""
    union = dict(primary)
    for p in foreign_paths:
        for n, e in _load(p).items():
            if n in primary:
                continue
            cur = union.get(n)
            if cur is None or (cur.get("status") != "done" and e.get("status") == "done"):
                union[n] = e
    return union


def has_core_frames(out_dir, name):
    """Both core segment dirs exist on the share (kart spawn is optional by design)."""
    return all(os.path.isdir(os.path.join(out_dir, "matte", f"{name}__{seg}_frames"))
               for seg in ("idle", "flourish"))


def is_kart(name):
    return len(name.split("__")) >= 3


def audit(clips_dir, out_dir, primary_path, claims_dir=None):
    """Cross-check every clip against the manifest union + on-disk frame dirs. Names sorted.
    `pending` matches process_all's real pending set: not done in the PRIMARY manifest, and —
    when claims_dir is given (multi-machine sweeps) — not claimed by any box either."""
    clips = _clip_names(clips_dir)
    primary = _load(primary_path)
    union = _union(primary, _foreign_paths(out_dir, primary_path))
    done = {n for n, e in union.items() if e.get("status") == "done"}
    done_sorted = sorted(done)
    frame_cache = {}

    def _has(n):
        if n not in frame_cache:
            frame_cache[n] = has_core_frames(out_dir, n)
        return frame_cache[n]

    unrecorded = [n for n in clips if n not in union]
    own_done = {n for n in clips if primary.get(n, {}).get("status") == "done"}
    pending = (claims.pending_names(clips, claims_dir, own_done) if claims_dir
               else [n for n in clips if n not in own_done])
    return {
        "unrecorded_with_frames": [n for n in unrecorded if _has(n)],
        "unrecorded_no_frames": [n for n in unrecorded if not _has(n)],
        "foreign_only": [n for n in done_sorted if primary.get(n, {}).get("status") != "done"],
        "missing_frames": [n for n in done_sorted if not _has(n)],
        "missing_idle_resume": [n for n in done_sorted if is_kart(n) and "idle_resume" not in union[n]],
        "status_not_done": sorted(set(union) - done),
        "pending": pending,
    }


def merge_foreign(out_dir, primary_path):
    """Additive-only absorb of foreign status-done entries into the primary. The primary always
    wins on conflicts; nothing is ever removed. Foreign done-entries whose core frames are NOT
    on the share are STALE (deliberately-cleared work) and are skipped, not absorbed — merging
    them would make process_all skip clips that need re-processing.

    Returns (added, backup_path, stale_skipped). (0, None, stale) when nothing was mergeable —
    the primary is untouched and no backup is written. A missing primary file is a normal state
    (fresh box): the merge creates it, with no backup to take."""
    primary = _load(primary_path)
    union = _union(primary, _foreign_paths(out_dir, primary_path))
    incoming, stale = {}, []
    for n, e in union.items():
        if n in primary or e.get("status") != "done":
            continue
        if has_core_frames(out_dir, n):
            incoming[n] = e
        else:
            stale.append(n)
    if not incoming:
        return 0, None, sorted(stale)
    backup = None
    if os.path.exists(primary_path):
        backup = f"{primary_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(primary_path, backup)              # byte-exact; no text-mode decode trap
    primary.update(incoming)
    tmp = primary_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(primary, f, indent=1)
    os.replace(tmp, primary_path)                       # atomic — a kill mid-write can't corrupt it
    return len(incoming), backup, sorted(stale)


def format_report(a):
    """Stable human lines shared by console + CLI; last line is the pending summary."""
    lines = []

    def _row(label, names):
        if names:
            ex = ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
            lines.append(f"{label}: {len(names)}  [{ex}]")
    _row("frames on share but in NO manifest", a["unrecorded_with_frames"])
    _row("never processed (no entry, no frames)", a["unrecorded_no_frames"])
    _row("recorded only in a foreign manifest", a["foreign_only"])
    _row("done but core frame dirs MISSING", a["missing_frames"])
    _row("kart done but NO idle_resume", a["missing_idle_resume"])
    _row("recorded with status != done", a["status_not_done"])
    if len(lines) == 0:
        lines.append("bookkeeping clean: every clip recorded in the primary manifest with frames")
    std = sum(1 for n in a["pending"] if not is_kart(n))
    lines.append(f"pending for next Process run: {len(a['pending'])} "
                 f"({std} standalones + {len(a['pending']) - std} karts)")
    return lines


def run_for_console(clips_dir, out_dir, primary_path, processing_active, claims_dir=None):
    """Audit; merge foreign entries into the primary when safe; return the report lines.

    `processing_active` may be a bool or a ZERO-ARG CALLABLE. A callable is evaluated right
    before the merge writes — the audit scan can take a while (SMB), so a click-time snapshot
    could go stale; the console passes a live state read plus a UI lock on the Process button."""
    a = audit(clips_dir, out_dir, primary_path, claims_dir=claims_dir)
    lines = []
    if a["foreign_only"]:
        active = processing_active() if callable(processing_active) else processing_active
        if active:
            lines.append(f"merge skipped ({len(a['foreign_only'])} foreign entries) — processing "
                         "is active and would clobber the manifest; press again when idle")
        else:
            added, backup, stale = merge_foreign(out_dir, primary_path)
            if added:
                lines.append(f"merged {added} foreign entries into the primary manifest "
                             f"(backup: {os.path.basename(backup) if backup else 'none — primary was new'})")
            else:
                lines.append("nothing mergeable (foreign entries stale, or state changed mid-audit)")
            if stale:
                ex = ", ".join(stale[:4]) + (" ..." if len(stale) > 4 else "")
                lines.append(f"NOT merged — foreign done-entries with no frames on the share "
                             f"(stale; they stay pending): {len(stale)}  [{ex}]")
            a = audit(clips_dir, out_dir, primary_path, claims_dir=claims_dir)
    return lines + format_report(a)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit share bookkeeping; merge into ONE manifest.")
    ap.add_argument("--clips", default=os.path.join(_REPO, "captures_sdr", "en_uk", "clips"))
    ap.add_argument("--out", default=os.path.join(_REPO, "temp", "asset_chips"))
    ap.add_argument("--manifest", default=None, help="default <out>/manifest.json")
    ap.add_argument("--claims-dir", default=None, help="subtract claimed clips from 'pending'")
    ap.add_argument("--merge", action="store_true",
                    help="absorb foreign manifests into the primary (default: audit only — a "
                         "headless run cannot see whether process_all is mid-run on this box)")
    a = ap.parse_args(argv)
    primary = a.manifest or os.path.join(a.out, "manifest.json")
    if a.merge:
        lines = run_for_console(a.clips, a.out, primary, processing_active=False,
                                claims_dir=a.claims_dir)
    else:
        lines = format_report(audit(a.clips, a.out, primary, claims_dir=a.claims_dir))
    for ln in lines:
        print(ln, flush=True)


if __name__ == "__main__":
    main()
