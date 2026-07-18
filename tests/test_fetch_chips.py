"""fetch_chips.sh against file:// URLs (bash from Git Bash / Pi both fine)."""
import hashlib
import os
import shutil
import subprocess
import tarfile

import pytest

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash unavailable")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "deploy", "fetch_chips.sh")


def _mk_release(root):
    rel = os.path.join(root, "release"); os.makedirs(rel)
    chip = os.path.join(root, "a__idle.webp"); open(chip, "wb").write(b"RIFFfake")
    with tarfile.open(os.path.join(rel, "chips-a.tar"), "w") as t:
        t.add(chip, arcname="a__idle.webp")
    open(os.path.join(rel, "chips-manifest.json"), "w").write('{"version":1}')
    lines = [f"tag chips-v1", f"base file://{rel.replace(os.sep, '/')}"]
    for n in ("chips-manifest.json", "chips-a.tar"):
        sha = hashlib.sha256(open(os.path.join(rel, n), "rb").read()).hexdigest()
        lines.append(f"{sha}  {n}")
    lock = os.path.join(root, "chips.lock")
    open(lock, "w", newline="\n").write("\n".join(lines) + "\n")
    return lock, rel


def _run(lock, data):
    # Git Bash's MSYS runtime mangles backslash-separated Windows paths passed as argv
    # (e.g. pytest's tmp_path) - forward slashes are unambiguous to both CreateProcess
    # and bash, so normalize before invoking. No-op on POSIX (no backslashes to swap).
    lock = lock.replace(os.sep, "/")
    data = data.replace(os.sep, "/")
    return subprocess.run([BASH, SCRIPT, lock, data], capture_output=True, text=True)


def test_fetch_deploys_and_is_idempotent(tmp_path):
    lock, _ = _mk_release(str(tmp_path))
    data = os.path.join(str(tmp_path), "data")
    os.makedirs(data)
    r = _run(lock, data)
    assert r.returncode == 0, r.stderr
    assert open(os.path.join(data, "current")).read().strip() == "chips-v1"
    assert os.path.exists(os.path.join(data, "chips-v1", "chips", "a__idle.webp"))
    assert os.path.exists(os.path.join(data, "chips-v1", "chips", "manifest.json"))
    assert os.path.exists(os.path.join(data, "chips-v1", ".complete"))
    r2 = _run(lock, data)
    assert r2.returncode == 0 and "already present" in r2.stdout


def test_fetch_fails_on_bad_sha_and_leaves_no_tag(tmp_path):
    lock, rel = _mk_release(str(tmp_path))
    open(os.path.join(rel, "chips-a.tar"), "ab").write(b"corrupt")
    data = os.path.join(str(tmp_path), "data"); os.makedirs(data)
    r = _run(lock, data)
    assert r.returncode != 0
    assert not os.path.exists(os.path.join(data, "chips-v1"))
    assert not os.path.exists(os.path.join(data, "current"))
