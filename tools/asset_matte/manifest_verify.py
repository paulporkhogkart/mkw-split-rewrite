"""Share-bookkeeping audit + one-manifest merge (spec 2026-07-17-console-verify-manifest-design).

The sweep ships matte frames + marks claims PER CLIP, but each box's manifest reaches the share
only as an end-of-run snapshot (best-effort) — so frames can exist with no manifest entry
anywhere (259 kart combos + 5 standalones found 2026-07-17), and entries recorded only in another
box's manifest.<machine>.json are invisible to process_all's pending set (it reads the PRIMARY
manifest only). Policy: everything ends up in the ONE primary manifest.

  python tools/asset_matte/manifest_verify.py --clips D:\\kartoff\\captures_sdr\\en_uk\\clips \\
      --out D:\\kartoff\\asset_chips            # audit + merge (add --no-merge for audit only)

Pure build python (json/glob/os only) — also driven by the sweep console's "Verify manifest"
button, which runs it off the Tk thread and blocks the merge while processing is active
(process_all holds the manifest in memory and rewrites the whole file after every clip; a mid-run
merge would be clobbered by its next save).
"""
import argparse
import glob
import json
import os
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _clip_names(clips_dir):
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(clips_dir, "*.mkv")))


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _foreign_paths(out_dir, primary_path):
    """Every non-primary manifest*.json in out_dir; backups (anything with '.bak') excluded."""
    primary = os.path.abspath(primary_path)
    return sorted(p for p in glob.glob(os.path.join(out_dir, "manifest*.json"))
                  if os.path.abspath(p) != primary and ".bak" not in os.path.basename(p))


def has_core_frames(out_dir, name):
    """Both core segment dirs exist on the share (kart spawn is optional by design)."""
    return all(os.path.isdir(os.path.join(out_dir, "matte", f"{name}__{seg}_frames"))
               for seg in ("idle", "flourish"))


def is_kart(name):
    return len(name.split("__")) >= 3


def audit(clips_dir, out_dir, primary_path):
    """Cross-check every clip against the manifest union + on-disk frame dirs. Names sorted."""
    clips = _clip_names(clips_dir)
    primary = _load(primary_path)
    union = dict(primary)
    for p in _foreign_paths(out_dir, primary_path):
        for n, e in _load(p).items():
            union.setdefault(n, e)                      # primary (and earlier files) win
    done = {n for n, e in union.items() if e.get("status") == "done"}
    a = {
        "unrecorded_with_frames": [n for n in clips if n not in union and has_core_frames(out_dir, n)],
        "unrecorded_no_frames": [n for n in clips if n not in union and not has_core_frames(out_dir, n)],
        "foreign_only": [n for n in sorted(done) if primary.get(n, {}).get("status") != "done"],
        "missing_frames": [n for n in sorted(done) if not has_core_frames(out_dir, n)],
        "missing_idle_resume": [n for n in sorted(done) if is_kart(n) and "idle_resume" not in union[n]],
        "status_not_done": sorted(n for n, e in union.items() if e.get("status") != "done"),
        "pending": [n for n in clips if primary.get(n, {}).get("status") != "done"],
    }
    return a


def merge_foreign(out_dir, primary_path):
    """Additive-only union of every foreign manifest's status-done entries into the primary.
    The primary always wins on conflicts; nothing is ever removed. Returns (added, backup_path);
    (0, None) when there was nothing to merge (no backup written, primary untouched)."""
    primary = _load(primary_path)
    incoming = {}
    for p in _foreign_paths(out_dir, primary_path):
        for n, e in _load(p).items():
            if e.get("status") == "done" and n not in primary:
                incoming.setdefault(n, e)
    if not incoming:
        return 0, None
    backup = f"{primary_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    with open(primary_path) as src, open(backup, "w") as dst:
        dst.write(src.read())
    primary.update(incoming)
    tmp = primary_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(primary, f, indent=1)
    os.replace(tmp, primary_path)                       # atomic — a kill mid-write can't corrupt it
    return len(incoming), backup


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


def run_for_console(clips_dir, out_dir, primary_path, processing_active):
    """Audit; merge foreign entries into the primary when safe; return the report lines."""
    a = audit(clips_dir, out_dir, primary_path)
    lines = []
    if a["foreign_only"]:
        if processing_active:
            lines.append(f"merge skipped ({len(a['foreign_only'])} foreign entries) — processing "
                         "is active and would clobber the manifest; press again when idle")
        else:
            added, backup = merge_foreign(out_dir, primary_path)
            lines.append(f"merged {added} foreign entries into the primary manifest "
                         f"(backup: {os.path.basename(backup)})")
            a = audit(clips_dir, out_dir, primary_path)   # report the post-merge state
    return lines + format_report(a)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Audit share bookkeeping; merge into ONE manifest.")
    ap.add_argument("--clips", default=os.path.join(_REPO, "captures_sdr", "en_uk", "clips"))
    ap.add_argument("--out", default=os.path.join(_REPO, "temp", "asset_chips"))
    ap.add_argument("--manifest", default=None, help="default <out>/manifest.json")
    ap.add_argument("--no-merge", action="store_true", help="audit only, never write")
    a = ap.parse_args(argv)
    primary = a.manifest or os.path.join(a.out, "manifest.json")
    if a.no_merge:
        lines = format_report(audit(a.clips, a.out, primary))
    else:
        lines = run_for_console(a.clips, a.out, primary, processing_active=False)
    for ln in lines:
        print(ln, flush=True)


if __name__ == "__main__":
    main()
