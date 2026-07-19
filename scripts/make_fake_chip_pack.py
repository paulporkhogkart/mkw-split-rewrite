"""
Build a miniature fake chip pack + lock for pbenguin chip-store rehearsal.

Fabricates two tiny "combos" (a kart-carrying one and a base-only one) as fake
sheet/silhouette files, packs them into shard tars the same shape as the real
`chips-vN` release, writes a `chips-manifest.json` + `lock` (same line format
as `web/chips.lock`: `tag`, `base`, then `sha256␣␣filename` per asset), and
also lays out an on-demand `chips-v1/` tree extracted FROM those shards (not
regenerated) so the on-demand bytes, shard bytes, and lock shas all agree -
`fake_files()` uses os.urandom, so any separately-generated loose files would
silently diverge from what's inside the tars.

Usage:
    python scripts/make_fake_chip_pack.py temp/fakepack
    cd temp/fakepack && python -m http.server 8000
    # separate shell:
    PBENGUIN_CHIPS_URL=http://127.0.0.1:8000 npm run tauri dev
"""

import hashlib
import json
import os
import sys
import tarfile

COMBOS = {
    "mario__base__standard_kart": {
        "kart": True,
        "idle_resume": 3,
        "anims": {
            "idle": {"frames": 8, "cols": 3, "rows": 3},
            "spawn": {"frames": 4, "cols": 2, "rows": 2},
            "flourish": {"frames": 4, "cols": 2, "rows": 2},
        },
    },
    "luigi__base": {
        "kart": False,
        "idle_resume": 0,
        "anims": {
            "idle": {"frames": 8, "cols": 3, "rows": 3},
            "flourish": {"frames": 4, "cols": 2, "rows": 2},
        },
    },
}


def fake_files(combo):
    """(filename, random bytes) pairs for one combo's sheets + silhouettes."""
    files = []
    for anim in COMBOS[combo]["anims"]:
        files.append((f"{combo}__{anim}.webp", os.urandom(2048)))
        for k in range(4):
            files.append((f"{combo}__{anim}__sil_k{k}.png", os.urandom(256)))
    return files


def write_tar(out, name, files):
    path = os.path.join(out, name)
    with tarfile.open(path, "w") as tar:
        for fname, data in files:
            tmp = os.path.join(out, ".tmp")
            with open(tmp, "wb") as f:
                f.write(data)
            tar.add(tmp, arcname=fname)
    os.remove(os.path.join(out, ".tmp"))
    return path


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def main():
    if len(sys.argv) != 2:
        print("usage: python scripts/make_fake_chip_pack.py <outdir>", file=sys.stderr)
        return 1
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)

    manifest = {
        "version": 1,
        "scale": 0.2,
        "fps": 60,
        "fw": 205,
        "fh": 216,
        "base": "/chips/anim/chips-v1/",
        "combos": COMBOS,
    }

    shards = [
        write_tar(out, "chips-mario.tar", fake_files("mario__base__standard_kart")),
        write_tar(out, "chips-luigi.tar", fake_files("luigi__base")),
    ]

    # The real release asset chips-manifest.json has NO base field (base is
    # injected server-side by the Pi at serve time) - mirror that here so the
    # shard-borne copy matches the actual shipped shape.
    shard_manifest = dict(manifest)
    del shard_manifest["base"]
    mpath = os.path.join(out, "chips-manifest.json")
    with open(mpath, "w") as f:
        json.dump(shard_manifest, f)

    lock = ["tag chips-v1", "base http://127.0.0.1:8000"]
    for p in [mpath] + shards:
        lock.append(f"{sha256_of(p)}  {os.path.basename(p)}")
    with open(os.path.join(out, "lock"), "w") as f:
        f.write("\n".join(lock) + "\n")

    # On-demand path needs /manifest.json + /chips-v1/<file> - mirror the site
    # layout. The chips-v1/ tree is extracted FROM the shards (not regenerated
    # from fake_files again) so on-demand bytes == shard bytes == lock shas.
    # /manifest.json KEEPS the injected base - that's the Pi-served shape.
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    chips_dir = os.path.join(out, "chips-v1")
    os.makedirs(chips_dir, exist_ok=True)
    for shard in shards:
        with tarfile.open(shard) as tar:
            tar.extractall(chips_dir)

    print(f"fake pack in {out} - serve with: cd {out} && python -m http.server 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
