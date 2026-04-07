"""
Usage:
  python scripts/set_version.py patch      # 0.2.15 → 0.2.16
  python scripts/set_version.py minor      # 0.2.15 → 0.3.0
  python scripts/set_version.py major      # 0.2.15 → 1.0.0
  python scripts/set_version.py 0.2.16    # explicit version

Updates the version string in:
  - package.json
  - pyproject.toml
  - src-tauri/Cargo.toml
  - src-tauri/tauri.conf.json
Then runs `cargo update --workspace` to sync Cargo.lock.
"""

import json, re, subprocess, sys
from pathlib import Path

BUMPS = {"major", "minor", "patch"}

def bump(current: str, part: str) -> str:
    major, minor, patch = (int(x) for x in current.split("."))
    if part == "major":   return f"{major + 1}.0.0"
    if part == "minor":   return f"{major}.{minor + 1}.0"
    if part == "patch":   return f"{major}.{minor}.{patch + 1}"

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/set_version.py patch|minor|major|<x.y.z>")

    arg = sys.argv[1].lstrip("v")
    root = Path(__file__).parent.parent

    # Read current version from package.json as the source of truth
    current = json.loads((root / "package.json").read_text())["version"]

    if arg in BUMPS:
        version = bump(current, arg)
        print(f"  {current} → {version}  ({arg})")
    else:
        version = arg
        print(f"  {current} → {version}")

    root = Path(__file__).parent.parent

    # package.json
    p = root / "package.json"
    data = json.loads(p.read_text())
    data["version"] = version
    p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  package.json          → {version}")

    # pyproject.toml  (version = "x.y.z")
    p = root / "pyproject.toml"
    text = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', p.read_text(), count=1, flags=re.MULTILINE)
    p.write_text(text)
    print(f"  pyproject.toml        → {version}")

    # src-tauri/Cargo.toml  (first version = "x.y.z" line)
    p = root / "src-tauri" / "Cargo.toml"
    text = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', p.read_text(), count=1, flags=re.MULTILINE)
    p.write_text(text)
    print(f"  src-tauri/Cargo.toml  → {version}")

    # src-tauri/tauri.conf.json
    p = root / "src-tauri" / "tauri.conf.json"
    data = json.loads(p.read_text())
    data["version"] = version
    p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  tauri.conf.json       → {version}")

    # Sync Cargo.lock
    print("  syncing Cargo.lock …")
    subprocess.run(["cargo", "update", "--workspace"], cwd=root / "src-tauri", check=True)

    # Sync package-lock.json
    print("  syncing package-lock.json …")
    subprocess.run(["npm.cmd", "install"], cwd=root, check=True)

    print(f"\nDone. Now commit, then: git tag v{version} && git push && git push --tags")

if __name__ == "__main__":
    main()
