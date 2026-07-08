# Two-machine matte sweep — runbook

Run the `process_all.py` matte batch on the rig (RTX 5080) and a second box (RTX 2080 Ti)
at once. They coordinate through atomic per-clip **claim files** in a shared folder on the
rig, so each clip is matted exactly once, either box can Start/Stop independently, and the
second box keeps only ~1–2 GB locally (it ships each finished clip to the rig and deletes it).
Design: `docs/superpowers/specs/2026-07-08-two-machine-matte-sweep-design.md`.

## One-time setup

### 1. Share `D:\kartoff` from the rig (SMB) — MANUAL

This is not automatic. On the **rig**, in an elevated PowerShell:

```powershell
# Grant the second box's account read/write to the share.
New-SmbShare -Name kartoff -Path D:\kartoff -FullAccess "PAUL-AM5-DT\<user>"
Get-SmbShare kartoff                         # verify it exists
```

- If the two boxes use different accounts, either add that account with `-FullAccess`, or use a
  dedicated account with `-ChangeAccess`. `-FullAccess "Everyone"` works only on a trusted LAN.
- Set the rig's network profile to **Private** (not Public) and allow **File and Printer
  Sharing** through the firewall for the Private profile (Settings → Network → Properties →
  Private; Windows Defender Firewall → Allow an app → File and Printer Sharing / Private).
- Both **share** permissions (above) and **NTFS** permissions on `D:\kartoff\asset_chips` and
  `…\claims` must allow the second box's account to **write**. GUI fallback: right-click
  `D:\kartoff` → Properties → Sharing → Advanced Sharing → Permissions.

On the **second box**, confirm access (and store credentials if the logins differ):

```powershell
Test-Path \\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips     # -> True
cmdkey /add:PAUL-AM5-DT /user:<rig-user> /pass               # only if logins differ
```

### 2. Stand up the GPU venv on the second box

Clone/copy this repo to the second box, then build `temp/asset-venv-matte` exactly as on the
rig (py3.12 + onnxruntime-gpu 1.22/CUDA 12 + torch cu128 + MatAnyone2). See
`tools/asset_matte/README.md` and the chip-asset-matting memory for the venv recipe. The
2080 Ti (Turing) runs the CUDA 12 wheels fine. `C:\kartoff_scratch` is created automatically —
no manual mkdir.

## Running

- **Rig:** double-click `run_console_m1.bat`, then use the **Process** button as usual. (Do not
  run this while the *recording* sweep is using the GPU — same rule as before.)
- **Second box:** double-click `run_console_m2.bat`, then **Process**. It reads clips over SMB,
  mattes locally, ships each clip to the rig.
- Either box can run **solo** (the other's Process off) and will chew through everything pending.
  Both consoles show the same **global** progress (X / 6273) from the shared `.done` markers.

## Stopping / powering off

- Click **Stop** (or close the window) on a box: it finishes and ships the in-flight clip, then
  exits — after which that box is **safe to power off**. Stop is per-box; it never stops the other.
- If a box is **hard powered off** mid-clip, nothing is corrupted or half-published: on its next
  launch it clears its own interrupted claim and redoes that one clip.

## If a box crashed and won't come back

Its claims for un-finished clips would otherwise stay held. Clear stale ones (older than 30 min,
not done) from the **rig** so they get redone:

```powershell
temp\asset-venv-matte\Scripts\python.exe tools\asset_matte\process_all.py --reclaim-orphans --claims-dir D:\kartoff\asset_chips\claims
```

## When the batch is done

- The full chip set + `index.html` viewer are in `D:\kartoff\asset_chips\matte\` on the rig.
- Delete the second box's scratch: `Remove-Item -Recurse -Force C:\kartoff_scratch`.
- Optionally delete `D:\kartoff\asset_chips\claims\` (only after you're sure the batch finished).
