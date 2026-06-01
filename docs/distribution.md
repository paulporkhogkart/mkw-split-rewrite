# Distribution & Auto-Update Architecture

## Overview

The distribution model treats **everything as one release artifact**: the Tauri UI wraps the Python sidecar into a single NSIS installer. A single update replaces both, including the updater itself.

```
GitHub Release (v1.2.3)
├── mkw-tracker-v1.2.3-windows-x64.zip   ← portable engine bundle (internal name; testing)
├── pbenguin_1.2.3_x64-setup.exe         ← NSIS installer  (user-facing: "pbenguin")
└── latest.json                           ← Tauri update manifest
```

> **Naming split (intentional).** The user-facing app is **pbenguin** (`productName`):
> the installer, Start Menu entry, Add/Remove Programs, and `pbenguin.exe` all show it.
> The Tauri `identifier` (`com.paulporkhogkart.mkw-tracker`), the bundled Python engine
> (`mkw-tracker-engine.exe`), and the data folder (`%APPDATA%/mkw-tracker/`) keep the
> original internal name **on purpose** — that preserves the updater's app identity (so
> existing installs upgrade in place, not side-by-side) and the user's settings/replays
> across the rename. Only `productName` changed.

---

## Components

### Python sidecar (`mkw_tracker.spec`)

PyInstaller `--onedir` compiles the tracker into a standalone folder:

```
dist/mkw-tracker/
├── mkw-tracker.exe          ← entry point
└── _internal/
    ├── images/              ← all template PNGs (bundled via spec datas=[])
    ├── cv2/
    ├── numpy/
    └── ...DLLs...
```

`resource_path()` in `mkw_tracker/utils/paths.py` resolves `images/…` paths from
`sys._MEIPASS` when frozen, and from the repo root in development — no code changes
needed anywhere else when switching between modes.

`data_dir()` points `mkw_tracker.db` to `%APPDATA%/mkw-tracker/` when frozen so
the database persists across updates. In development it stays at the repo root.

### Tauri sidecar integration (when frontend is added)

Copy the built sidecar into the Tauri project before `tauri build`:

```
src-tauri/binaries/
└── mkw-tracker-x86_64-pc-windows-msvc.exe   ← copy of dist/mkw-tracker/mkw-tracker.exe
    _internal/                                ← copy of dist/mkw-tracker/_internal/
```

`tauri.conf.json`:
```json
{
  "bundle": {
    "externalBin": ["binaries/mkw-tracker"]
  }
}
```

The sidecar is launched with `Command::sidecar("mkw-tracker")` from Tauri's shell plugin, communicating over stdio (the existing IPC protocol).

---

## Auto-Update Flow

### Tauri updater (`@tauri-apps/plugin-updater`)

1. App starts → updater plugin checks `latest.json` at the configured endpoint.
2. If `version` in `latest.json` > installed version → download NSIS installer.
3. Installer runs (silently or with a prompt), replacing:
   - The Tauri binary (including the updater logic compiled into it)
   - The bundled Python sidecar
4. App restarts with the new version.

**The updater updates itself** because it is compiled into the Tauri binary. Every NSIS update replaces the entire binary, so there is no separate updater process to maintain.

`tauri.conf.json` update config:
```json
{
  "plugins": {
    "updater": {
      "endpoints": [
        "https://github.com/OWNER/REPO/releases/latest/download/latest.json"
      ],
      "dialog": true,
      "pubkey": "YOUR_TAURI_PUBLIC_KEY"
    }
  }
}
```

### `latest.json` schema

```json
{
  "version": "1.2.3",
  "notes": "See https://github.com/OWNER/REPO/releases/tag/v1.2.3",
  "pub_date": "2025-01-01T00:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<tauri-signer output>",
      "url": "https://github.com/OWNER/REPO/releases/download/v1.2.3/mkw-tracker-v1.2.3-x64-setup.nsis.zip"
    }
  }
}
```

The signature is required by Tauri's updater to prevent supply-chain attacks. It is produced by:
```bash
tauri signer sign -k "$TAURI_PRIVATE_KEY" -- path/to/installer.nsis.zip
```

---

## Releasing a New Version

### 1. Bump the version

Edit `pyproject.toml`:
```toml
[project]
version = "0.2.0"
```
When Tauri is added, also bump `src-tauri/tauri.conf.json` → `version`.

### 2. Tag and push

```bash
git tag v0.2.0
git push origin v0.2.0
```

GitHub Actions (`.github/workflows/release.yml`) takes over:
- Builds the PyInstaller bundle
- Generates `latest.json`
- Creates the GitHub Release with all artifacts

### 3. Users receive the update

- **Pre-Tauri (testing):** Friends re-download the zip manually from the release page, or you share the link. The `latest.json` endpoint is already live for when the Tauri updater is wired up.
- **Post-Tauri:** The app silently notifies users on next launch and applies the update automatically.

---

## Setting Up Signing (required before shipping Tauri updates)

```bash
# Generate a key pair once; store the private key securely
tauri signer generate -w ~/.tauri/mkw-tracker.key

# Add to GitHub Secrets:
#   TAURI_PRIVATE_KEY  = contents of ~/.tauri/mkw-tracker.key
#   TAURI_KEY_PASSWORD = the password you chose

# Public key goes in tauri.conf.json → plugins.updater.pubkey
```

---

## Development Workflow (no changes needed)

```bash
python -m mkw_tracker            # still works — resource_path() falls back to repo root
```

`mkw_tracker.db` is still created at the repo root in dev mode.

---

## Local Build Test

```bash
pip install pyinstaller
pyinstaller mkw_tracker.spec --noconfirm
# Test the bundle:
dist/mkw-tracker/mkw-tracker.exe --no-ipc
```
