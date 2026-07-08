# Two-machine matte sweep — runbook

Run the `process_all.py` matte batch on the rig (RTX 5080) and a second box (RTX 2080 Ti)
at once. They coordinate through atomic per-clip **claim files** in a shared folder on the
rig, so each clip is matted exactly once, either box can Start/Stop independently, and the
second box keeps only ~1–2 GB locally (it ships each finished clip to the rig and deletes it).
Design: `docs/superpowers/specs/2026-07-08-two-machine-matte-sweep-design.md`.

## One-time setup

### 1. Share `D:\kartoff` from the rig (SMB) — MANUAL

Not automatic, and it needs care: local accounts don't cross machines, so the second box must
authenticate to the rig as a **rig-local account**. Use a rig account **with a password** — here
the existing **`vr`** account — which sidesteps blank-password network-logon problems entirely.
Box 2's own login account is irrelevant to this.

On the **rig** (`PAUL-AM5-DT`), elevated PowerShell:

```powershell
# Create the share granting vr. If it already exists ("The name has already been shared"),
# skip New-SmbShare and just add vr to the existing share with Grant-SmbShareAccess instead:
New-SmbShare -Name kartoff -Path D:\kartoff -FullAccess "PAUL-AM5-DT\vr"
Grant-SmbShareAccess -Name kartoff -AccountName "PAUL-AM5-DT\vr" -AccessRight Full -Force
Get-SmbShareAccess -Name kartoff                 # confirm PAUL-AM5-DT\vr = Full

# vr isn't the folder owner, so grant it NTFS read+write on the tree (reads clips, writes
# chips/claims). (OI)(CI)=inherit to new files, M=Modify, /T=apply to the existing tree.
icacls D:\kartoff /grant "PAUL-AM5-DT\vr:(OI)(CI)M" /T /C /Q

# Network must be Private with File & Printer Sharing allowed through the firewall:
Set-NetFirewallRule -DisplayGroup "File and Printer Sharing" -Enabled True -Profile Private
```

On the **second box**, store the `vr` credential once (so the console is never prompted) and test:

```powershell
# If box 2 already holds a connection to the rig: net use \\PAUL-AM5-DT\kartoff /delete
net use \\PAUL-AM5-DT\kartoff /user:PAUL-AM5-DT\vr * /persistent:yes   # * prompts for vr's password
Test-Path \\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips              # -> True
```

Nothing else references `vr`: the `.bat` files use the `\\PAUL-AM5-DT\kartoff` path, and claims
key off `COMPUTERNAME` (distinct per box), so exactly-once coordination is unaffected by the login.

> **Blank-password trap (bit us once).** A local account with a **blank** password can't do *any*
> network logon — SMB, RDP, WinRM — by default (the `LimitBlankPasswordUse` policy). The passworded
> `vr` account avoids this, so **leave that policy alone.** If you ever set it to `1` on a
> **headless** box whose only account is blank-password, you lock yourself out of RDP ("a user
> account restriction is preventing you from logging on") with **no remote fix** — every network
> path is blocked, so you'd need a monitor + keyboard on that box to set it back to `0`.

### 2. Stand up the GPU venv on the second box

Clone/copy this repo to the second box, then build `temp/asset-venv-matte` exactly as on the
rig (py3.12 + onnxruntime-gpu 1.22/CUDA 12 + torch cu128 + MatAnyone2). See
`tools/asset_matte/README.md` and the chip-asset-matting memory for the venv recipe. The
2080 Ti (Turing) runs the CUDA 12 wheels fine. `C:\kartoff_scratch` is created automatically —
no manual mkdir.

## Before a fresh sweep — clear stale output

The production crop widened (chips are 1024×1080 now), so any earlier `asset_chips` output is
invalid and the old `manifest.json` would make `process_all` skip those clips. On the rig, wipe the
previous run before a fresh sweep:

```powershell
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
  D:\kartoff\asset_chips\matte, D:\kartoff\asset_chips\claims, `
  D:\kartoff\asset_chips\loopframes, D:\kartoff\asset_chips\manifest.json
```

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

## Building the index / when the batch is done

In a two-box sweep **nobody auto-builds the viewer** (the other box may not have published its
manifest yet). Each box publishes its own `manifest.<hostname>.json` to the share when it cleanly
Stops/finishes, and `make_viewer` unions every `manifest*.json` — so the flourish→idle handoff
(`idle_resume`) comes out right for the whole roster, not just the rig's half.

- Make sure **box 2 has cleanly Stopped at least once** (that's when its manifest lands on the share).
- On the rig, press **Build viewer** (the asset-processing bar) — it builds over local `D:\` and
  unions both manifests into `D:\kartoff\asset_chips\matte\index.html`. Safe to press any time for a
  mid-run snapshot; it just shows whatever's finished so far.
- Delete the second box's scratch: `Remove-Item -Recurse -Force C:\kartoff_scratch`.
- Optionally delete `D:\kartoff\asset_chips\claims\` — only once you're sure the batch finished
  **and** you've built the index.

(Single-machine `run_console.bat` runs are unchanged: they still auto-build the viewer on exit.)
