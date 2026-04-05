"""
autotemplate runner — reads a YAML script and drives controller + capture.

Usage (in WSL2):
    python runner.py scripts/characters_en.yaml --db /mnt/c/dev/mkw-split-rewrite/mkw_tracker.db
    python runner.py scripts/characters_en.yaml --dry-run   # no controller, just print steps
    python runner.py scripts/characters_en.yaml --preview-roi characters
"""
import argparse
import os
import sys
import time

import yaml


# ── Script format ─────────────────────────────────────────────────────────────
#
# name:     "Characters (English)"
# category: characters          # characters | karts | courses | costumes | mushrooms
# language: en
#
# # Run once before the item sequence (navigate to the right screen)
# preamble:
#   - { HOME: 0.1 }             # press HOME for 0.1 s
#   - { wait: 2.0 }
#   - { A: 0.1 }
#
# items:
#   - name: "Baby Daisy"
#     file: baby_daisy           # filename without .png
#     before:                    # button presses to reach this item from previous
#       - { DPAD_RIGHT: 0.05 }
#       - { wait: 0.3 }
#     capture_wait: 0.5          # seconds to wait after `before` before capturing
#
# ── Step syntax ───────────────────────────────────────────────────────────────
# Each step is a dict with exactly one key:
#   { BUTTON_NAME: duration_seconds }   — press button
#   { wait: seconds }                   — sleep
#   { macro: "DPAD_UP 0.1s\n0.1s" }    — raw nxbt macro string


def _parse_args():
    p = argparse.ArgumentParser(description="autotemplate runner")
    p.add_argument("script",       help="Path to YAML script file")
    p.add_argument("--db",         default=None,
                   help="Path to mkw_tracker.db (default: auto-detect next to runner.py)")
    p.add_argument("--repo",       default=None,
                   help="Path to repo root (images/ lives here)")
    p.add_argument("--device",     default="0",
                   help="Capture card device index or path (default: 0)")
    p.add_argument("--adapter",    default="hci0",
                   help="Bluetooth adapter (default: hci0)")
    p.add_argument("--reconnect",  default=None,
                   help="Switch Bluetooth MAC to reconnect without pairing mode")
    p.add_argument("--dry-run",    action="store_true",
                   help="Print steps without opening controller or capture card")
    p.add_argument("--preview-roi", metavar="CATEGORY", default=None,
                   help="Open a live ROI preview window for the given category then exit")
    p.add_argument("--start-from", metavar="FILE", default=None,
                   help="Skip items until this file name is reached (resume support)")
    return p.parse_args()


def _find_repo_root(script_path: str) -> str:
    """Walk up from this file until we find images/ — that's the repo root."""
    candidate = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isdir(os.path.join(candidate, "images")):
            return candidate
        candidate = os.path.dirname(candidate)
    # Fallback: repo root is two levels up from tools/autotemplate/
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _find_db(repo_root: str) -> str:
    candidate = os.path.join(repo_root, "mkw_tracker.db")
    if os.path.exists(candidate):
        return candidate
    # Try Windows path via WSL mount
    win_candidate = "/mnt/c" + repo_root.replace("/mnt/c", "")
    if os.path.exists(win_candidate + "/mkw_tracker.db"):
        return win_candidate + "/mkw_tracker.db"
    print(f"[WARN] mkw_tracker.db not found near {repo_root!r} — ROI defaults will be used")
    return candidate


def _execute_steps(steps: list, ctrl, dry_run: bool) -> None:
    """Execute a list of step dicts against the controller."""
    for step in (steps or []):
        if not isinstance(step, dict) or len(step) != 1:
            print(f"  [WARN] Malformed step: {step!r}")
            continue
        key, value = next(iter(step.items()))

        if key == "wait":
            secs = float(value)
            if dry_run:
                print(f"    wait {secs}s")
            else:
                time.sleep(secs)

        elif key == "macro":
            if dry_run:
                print(f"    macro: {value!r}")
            else:
                ctrl.macro(str(value))

        else:
            # Treat key as button name
            duration = float(value) if value is not None else 0.1
            if dry_run:
                print(f"    press {key} ({duration}s)")
            else:
                ctrl.press(key, duration=duration)


def main():
    args = _parse_args()

    # ── Load script ───────────────────────────────────────────────────────────
    with open(args.script) as f:
        script = yaml.safe_load(f)

    category = script.get("category", "characters")
    items    = script.get("items", [])
    preamble = script.get("preamble", [])
    name     = script.get("name", args.script)

    print(f"Script : {name}")
    print(f"Category: {category}  ({len(items)} items)")

    # ── Resolve paths ─────────────────────────────────────────────────────────
    repo_root = args.repo or _find_repo_root(args.script)
    db_path   = args.db   or _find_db(repo_root)
    device    = int(args.device) if args.device.isdigit() else args.device

    print(f"Repo root: {repo_root}")
    print(f"DB:        {db_path}")
    print(f"Device:    {device}")

    # ── Preview ROI mode ─────────────────────────────────────────────────────
    if args.preview_roi:
        from capture import CaptureSession
        with CaptureSession(db_path, repo_root, device) as cap:
            cap.preview_roi(args.preview_roi)
        return

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n--- DRY RUN ---")
        print("PREAMBLE:")
        _execute_steps(preamble, None, dry_run=True)
        skipping = bool(args.start_from)
        for item in items:
            ifile = item.get("file", "")
            if skipping:
                if ifile == args.start_from:
                    skipping = False
                else:
                    print(f"  (skip {ifile})")
                    continue
            print(f"\nItem: {item.get('name')} ({ifile})")
            _execute_steps(item.get("before", []), None, dry_run=True)
            cw = item.get("capture_wait", 0.5)
            print(f"    wait {cw}s (capture_wait)")
            print(f"    >>> CAPTURE {category}/{ifile}.png")
        return

    # ── Live run ──────────────────────────────────────────────────────────────
    from controller import ProController
    from capture    import CaptureSession

    skipping = bool(args.start_from)

    with ProController(adapter=args.adapter) as ctrl, \
         CaptureSession(db_path, repo_root, device) as cap:

        ctrl.connect(reconnect_addr=args.reconnect)

        # Preamble
        if preamble:
            print("\nRunning preamble…")
            _execute_steps(preamble, ctrl, dry_run=False)

        # Items
        for i, item in enumerate(items):
            iname = item.get("name", "?")
            ifile = item.get("file", "")

            if skipping:
                if ifile == args.start_from:
                    skipping = False
                    print(f"\nResuming from: {iname}")
                else:
                    continue

            print(f"\n[{i+1}/{len(items)}] {iname} ({ifile})")

            _execute_steps(item.get("before", []), ctrl, dry_run=False)

            cw = float(item.get("capture_wait", 0.5))
            if cw > 0:
                time.sleep(cw)

            ok = cap.capture_template(category, ifile)
            if not ok:
                print(f"  [WARN] Capture failed for {iname!r} — skipping")

        print("\nDone.")


if __name__ == "__main__":
    main()
