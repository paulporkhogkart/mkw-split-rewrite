"""Shard a built site pack into per-character release tars + chips.lock.

  python tools/asset_matte/pack_shards.py --pack D:\\kartoff\\asset_chips\\site_pack \\
      --tag chips-v1
Writes <pack>/release/. The lock is committed to web/chips.lock by the release runbook;
the pack itself NEVER enters git.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tarfile

DEFAULT_BASE = "https://github.com/paulporkhogkart/mkw-split-rewrite/releases/download"


def char_of(filename: str) -> str:
    return filename.split("__", 1)[0]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_shards(chips_dir: str, release_dir: str, tag: str, base_url: str) -> list[tuple[str, str]]:
    os.makedirs(release_dir, exist_ok=True)
    by_char: dict[str, list[str]] = {}
    for f in sorted(os.listdir(chips_dir)):
        if f == "manifest.json":
            continue
        by_char.setdefault(char_of(f), []).append(f)

    files: list[tuple[str, str]] = []
    man_out = os.path.join(release_dir, "chips-manifest.json")
    shutil.copyfile(os.path.join(chips_dir, "manifest.json"), man_out)
    files.append(("chips-manifest.json", _sha256(man_out)))

    for char, members in sorted(by_char.items()):
        tar_path = os.path.join(release_dir, f"chips-{char}.tar")
        with tarfile.open(tar_path, "w") as t:  # uncompressed: webp/png don't recompress
            for m in members:
                t.add(os.path.join(chips_dir, m), arcname=m)
        files.append((f"chips-{char}.tar", _sha256(tar_path)))

    with open(os.path.join(release_dir, "chips.lock"), "w", newline="\n") as f:
        f.write(f"tag {tag}\nbase {base_url}\n")
        for name, sha in files:
            f.write(f"{sha}  {name}\n")
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args(argv)
    base = args.base_url or f"{DEFAULT_BASE}/{args.tag}"
    files = build_shards(os.path.join(args.pack, "chips"),
                         os.path.join(args.pack, "release"), args.tag, base)
    total = sum(os.path.getsize(os.path.join(args.pack, "release", n)) for n, _ in files)
    print(f"{len(files)} release files, {total/1e9:.2f}GB; lock at "
          f"{os.path.join(args.pack, 'release', 'chips.lock')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
