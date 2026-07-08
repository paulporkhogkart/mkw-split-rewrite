# Two-Machine Matte Sweep — Design

**Date:** 2026-07-08 · **Surface:** desktop tools (`tools/asset_matte/`, `tools/sweep_console/`) · **Status:** approved design, pending spec review · **Branch context:** `asset-clip-sweep` lineage (chip asset matting)

## Context

The chip asset sweep has two phases. Phase 1 (record) is done — 6,273 clips sit in
`D:\kartoff\captures_sdr\en_uk\clips` on the rig. Phase 2 is the **matte batch**:
`tools/asset_matte/process_all.py` turns every clip into a transparent RGBA chip
(spawn/idle/flourish segments). It is a **single-machine, GPU-bound** job of roughly
**~100 GPU-hours** on the rig alone.

A second machine is available — **i9-9900k + RTX 2080 Ti, Windows 10** — on the same LAN.
Running the batch on both roughly halves wall-clock. This spec covers doing that **without
clashing, with independent per-machine control, and without disturbing the rig when it is
being used for other things.**

### The driving workflow (this shapes the whole design)

The user's actual usage pattern, stated explicitly:

- **Daytime:** rig (machine 1) is for work/gaming — its matte processing is **off**.
  Machine 2 processes **solo** and must chew through *whatever is pending*, not a capped slice.
- **Overnight / away:** *both* machines process together and must split the remaining work
  with **zero duplication and zero clashes**.
- Machine 1 (machine 2) can join or drop at **any time** with no reconfiguration.
- When machine 1's processing is off, machine 2's activity must be **as unintrusive as
  possible** on machine 1.

### Measured facts (this machine, PAUL-AM5-DT = the rig)

| Fact | Value | Source |
|---|---|---|
| Clips | 6,273 `.mkv` | `D:\kartoff\captures_sdr\en_uk\clips` |
| Clip set size | **570 GB** (~93 MB/clip) | measured |
| Matte output | **~283 MB/clip** → **~1.7 TB** full set | measured over the 466-clip existing set (128.7 GB) |
| Output shape | 115k `.png` frame files + `.webp` loops = ~500 files/clip-ish, **~1.5 M small files** total | `matte/` extension breakdown |
| Rig GPU | RTX 5080 (fast) | `Win32_VideoController` |
| Machine 2 GPU | RTX 2080 Ti (slower, Turing / CUDA 12 OK) | user |
| **Machine 2 free disk** | **~50 GB SSD** (maybe +50–100 GB after cleanup); 350 GB slow HDD (do not use) | user |

The 570 GB clip set means machine 2 **reads clips over SMB**, never copies them (a clip read
is ~93 MB / ~1 s, hidden behind the ~90–180 s GPU matte). The ~1.7 TB / ~1.5 M-file output and
machine 2's ~50 GB SSD together mean machine 2 **must not accumulate output locally** — it
ships each finished clip to the rig and deletes the local copy, capping local use at ~1–2 GB.

## Goals

- Both machines matte concurrently over **disjoint** clips — no duplicate GPU work, no
  manifest clobber, no output-file collision.
- **Independent Start/Stop** on each machine (one machine's Stop never stops the other).
- **Machine 2 solo processes the entire pending set** — never capped at a fixed slice.
- **Machine 1 undisturbed** while its own processing is off — only light SMB file-serving.
- **Machine 2 local storage capped at ~1–2 GB** (ship-and-delete), no HDD needed.
- The 5080/2080 Ti speed gap **auto-balances** with no manual tuning.
- **Existing single-machine runs stay byte-identical** — every new behaviour is opt-in.

## Non-goals (YAGNI)

- **No static shards / no manual weight tuning** — a static split caps machine 2 solo (the
  core workflow need) and would need hand-tuned weights for the speed gap. Replaced by claims.
- **No central job server / queue / DB** (Redis, a scheduler, etc.) — the shared filesystem
  *is* the coordinator. Overkill for two supervised boxes.
- **No 3+ machine orchestration** — the claim model scales to N trivially, but only the
  2-machine case is built and tested.
- **No auto-restart** of a crashed matte process (a crash wants human eyes; see chip memory:
  a corrupt run is worse than a stalled one).
- **No change to the matte pipeline** — birefnet/MatAnyone2, segment detection, predark,
  output format are all untouched. This is pure *orchestration*.
- **No copying the 570 GB clip set** to machine 2.

## Architecture

```
   ┌──────────────────────────── MACHINE 1 (rig, RTX 5080, Win11) ────────────────────────────┐
   │  D:\kartoff\  (shared over SMB as \\PAUL-AM5-DT\kartoff)                                   │
   │    captures_sdr\en_uk\clips\*.mkv         ← input (read locally by M1, over SMB by M2)     │
   │    asset_chips\matte\        ← FINAL output dir (M1 writes here; M2 ships here)            │
   │    asset_chips\claims\       ← SHARED COORDINATOR (atomic claim files)                     │
   │                                                                                            │
   │  process_all.py  --out D:\kartoff\asset_chips                                              │
   │                  --claims-dir D:\kartoff\asset_chips\claims   (no --ship-dir: writes here) │
   │                  --stop-file  D:\kartoff\asset_chips\.process_stop   (local)               │
   └───────────────────────────────────────────▲──────────────────────────────────────────────┘
                          SMB (LAN, ~3–6 MB/s)  │  reads clips · writes chips · atomic claims
   ┌───────────────────────────────────────────┴─── MACHINE 2 (RTX 2080 Ti, Win10) ────────────┐
   │  process_all.py  --clips     \\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips                 │
   │                  --out        C:\kartoff_scratch\asset_chips   (LOCAL SSD, transient)       │
   │                  --ship-dir   \\PAUL-AM5-DT\kartoff\asset_chips  (move+delete per clip)     │
   │                  --claims-dir \\PAUL-AM5-DT\kartoff\asset_chips\claims                      │
   │                  --stop-file  C:\kartoff_scratch\.process_stop   (local)                    │
   │  Local peak: loop-frames + in-flight clip ≈ 1–2 GB.                                         │
   └────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Data flow per clip:** claim it (atomic) → segment (CPU, local scratch) → matte (GPU, local
scratch) → on machine 2, move `matte\<name>__*` to the share and delete local → mark the claim
done. Machine 1's output already lands in the shared `matte\`, so both machines converge into
**one** `matte\` dir with nothing to merge.

## The coordination primitive: atomic claim files

The one piece of genuinely shared mutable state is **who is processing which clip**. The
existing `manifest.json` cannot serve this across machines — it is a whole-file read/modify/
`os.replace` (process_all.py:56–60), so two writers last-writer-win-clobber each other. Instead
we coordinate through a **directory of one small file per clip**, claimed with an **atomic
exclusive create**:

- To take clip `<name>`, a worker does `os.open(<claims>/<name>.claim, O_CREAT|O_EXCL|O_WRONLY)`
  and writes `"<machine-id> <unix-ts>"` (`machine-id` = `socket.gethostname()` /
  `%COMPUTERNAME%`, overridable via `--machine-id`; it only has to be distinct between the two).
  - **Succeeds** → this worker owns the clip.
  - **Fails `FileExistsError`** → someone else owns/finished it → skip to the next pending clip.
- On completion (machine 2: *after* the ship succeeds), the worker creates `<name>.done`.

**Why this is atomic on this exact setup.** Both machines are Windows; the share is NTFS on
machine 1. `O_CREAT|O_EXCL` maps to Win32 `CREATE_NEW`, whose create-if-absent is performed
**atomically by NTFS**, and SMB2/3 forwards the `FILE_CREATE` disposition to the server
uncached. Two racers — even one local on machine 1 and one over SMB from machine 2 — both hit
the same NTFS volume, which serialises `FILE_CREATE` for a given name: exactly one wins. (This
is the property that is unreliable on some NFS setups; it is reliable Windows→Windows SMB2/3 to
NTFS.)

**Skip / pending logic** (per worker, in `process_all`'s loop):

1. Snapshot `os.listdir(<claims>)` once at start (and refresh every ~50 clips) → the set of
   already-claimed names. `pending = clip_names − claimed − (own manifest "done")`.
2. Iterate `pending`; for each, attempt the atomic claim. A lost race (create fails) is a cheap
   skip — the snapshot only trims obvious losers, the atomic create is the real gate.

**Behaviour that falls out:**

- **Machine 2 solo** → claims and processes *everything* pending, never capped. ✓
- **Both running** → each clip claimed once; the faster 5080 simply wins more claims, so the
  speed gap **auto-balances** with no weights. ✓
- **Join/drop anytime** → no coordination, no restart; a worker just starts/stops claiming. ✓

**Crash recovery** (rare, supervised):

- *Own restart* (common): on startup a worker deletes any `.claim` bearing **its own**
  machine-id that has no `.done` and no shipped output — reclaiming its own interrupted work.
  Race-free (only that worker uses its id).
- *Other machine died* (rare): a separate one-shot `process_all.py --reclaim-orphans` scans for
  `.claim` files older than `STALE_SECS = 1800` (≫ any clip's ~90–180 s) with no `.done` and no
  output, and deletes them so the next run redoes them. Kept out of the hot path so the steady
  state stays race-free; run by hand only if a box crashed mid-run.

## `process_all.py` changes (two opt-in flags)

The batch driver (process_all.py:67–158) gains two flags. **Both default off → the current
single-machine code path is byte-identical** (no claims dir, no shipping).

### `--claims-dir <path>` (coordination; default: none)

When set, wrap the per-clip body in the claim protocol above:

- After building `pending` (process_all.py:99), subtract the claimed snapshot.
- At the top of the loop, atomically claim `name`; on failure `continue` (skip) — this replaces
  "process every pending clip in sorted order" with "process every pending clip **I win the
  claim for**". The existing stop-file check (process_all.py:110) and `--limit` stay.
- After `save_manifest` (process_all.py:141) and — on machine 2 — after the ship, create
  `<name>.done`.
- When unset: no claiming, exactly today's loop.

### `--ship-dir <path>` (relocate output; default: none)

When set, after a clip is fully matted + manifested:

- Move every artifact matching `<out>/matte/<name>__*` into `<ship-dir>/matte/`, then delete the
  local copies. The `<name>__*` glob is **exact and collision-free**: every artifact
  `_write_chip` emits is prefixed `"<name>__<seg>"` (matte_blankplate.py:278–297 →
  `<name>__<seg>_frames/`, `<name>__<seg>_loop.webp`, `<name>__<seg>_checker.webp`), and clip
  names never nest (`char__costume__kart`), so `<name>__*` cannot match a different clip.
- **Idempotent re-ship:** before moving, delete any same-named target already under
  `<ship-dir>/matte/` (`rmtree`/`remove` per artifact). This makes a re-ship after an interrupted
  copy (network drop / hard power-off) cleanly overwrite a partial target rather than error on a
  half-copied dir.
- Machine-2→share is cross-volume, so the move is copy-then-delete (`shutil.move`). Order is
  **ship → then write `.done`**: if the ship fails (network blip), `.done` is not written, the
  local output stays, and a retry (this run's later pass or a restart's own-reclaim) re-ships it.
  A clip is never marked complete to the cluster until its bytes are on the share.
- When unset (machine 1): output is written in place under `<out>/matte/` — today's behaviour —
  because `<out>` *is* the shared final dir on machine 1.

**Directory bootstrap:** on startup the driver `makedirs(exist_ok=True)` for `<out>` (+
`<out>/matte`, `<out>/loopframes`), `<ship-dir>/matte`, and `<claims-dir>` — so machine 2's
`C:\kartoff_scratch` and the share's `claims\` are created automatically if missing. (The console
already does this for `<out>` and clips; app.py:68–72.)

Loop-frames stay local automatically: `loopdir = <out>/loopframes` (process_all.py:82), and on
machine 2 `<out>` is the local scratch — so the big transient frames never touch the network,
and the existing `shutil.rmtree(seg_base)` (process_all.py:138) still deletes them per clip. No
separate `--loop-dir` flag is needed.

`--manifest` and `--stop-file` already exist (process_all.py:71–72); each machine passes its own
**local** paths, which is what gives independent Start/Stop and per-machine resume.

## Console changes (`tools/sweep_console/app.py`) — env plumbing

Machine 2 runs the **same Tk console** (preview, progress, Process/Pause/Stop), pointed at the
share via env vars — extending the existing `KARTOFF_DATA_ROOT` pattern (app.py:35). New vars,
each defaulting to today's value so an unset environment is unchanged:

| Env var | Default (unset) | Machine 2 value |
|---|---|---|
| `KARTOFF_CLIPS_DIR` | `<DATA_ROOT>\captures_sdr\en_uk\clips` | `\\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips` |
| `KARTOFF_PROCESS_OUT` | `<DATA_ROOT>\asset_chips` | `C:\kartoff_scratch\asset_chips` (local) |
| `KARTOFF_CLAIMS_DIR` | *(none → single-machine)* | `\\PAUL-AM5-DT\kartoff\asset_chips\claims` |
| `KARTOFF_SHIP_DIR` | *(none → write in place)* | `\\PAUL-AM5-DT\kartoff\asset_chips` |

Machine 1 sets only `KARTOFF_CLAIMS_DIR = D:\kartoff\asset_chips\claims` (to join the cluster);
everything else stays default. Both are launched by tiny per-machine wrappers
(`run_console_m1.bat` / `run_console_m2.bat`) that `set` these before `python app.py`.

Wiring (all mechanical):

- `process_cmd` (commands.py:30) and `ProcessSupervisor.start_processing`
  (supervisor.py:90) gain `--claims-dir` / `--ship-dir` pass-through when the env vars are set.
- `PROCESS_STOP`/`PROCESS_MANIFEST` already derive from `PROCESS_OUT` (app.py:41–42) → on
  machine 2 they are automatically local. The stop-file being local is what keeps Stop
  independent between machines.
- **Global progress:** `process_done_count` (supervisor.py:173) reads the local manifest today.
  When `KARTOFF_CLAIMS_DIR` is set, count `<claims>\*.done` instead — so **both** consoles show
  the same true *batch* progress (X/6273) rather than each showing only its own contribution.
- **Preview** (`_update_proc_preview`, app.py:302) globs `<PROCESS_OUT>\matte\…` — on machine 2
  that is the local scratch, holding exactly the in-flight clip being matted (shipped+deleted
  only after it completes), so the preview stays correct and fast.
- **Viewer build** on exit (`build_viewer`, app.py:265) targets `<SHIP_DIR>\matte` when shipping
  (the shared full set), else `<PROCESS_OUT>\matte` as today. Whichever machine finishes last
  regenerates a correct full-set `index.html`; `make_viewer` discovers combos by globbing the
  matte dir's `*__idle_frames` (make_viewer.py:204), never the manifest, so the union just works.

## Disruption on machine 1 (the daytime requirement)

- Machine 1 runs **zero** matte processing unless its own Process button is on — daytime = no
  GPU/CPU matte load from this system at all.
- The only load while machine 2 works solo is **SMB file-serving**: one 93 MB clip read + one
  283 MB chip write + a handful of tiny claim ops **per ~90–180 s clip ≈ 3–6 MB/s**, bursty.
  That is <1 % of the D: SSD's throughput and is **LAN-local** (switch traffic — it never
  touches the internet uplink), so online-gaming latency is untouched. If games live off `C:`/a
  games drive, machine 2's I/O and gaming do not even share a spindle.
- Sustained ~3–6 MB/s fits inside even a 100 Mbit link 4× over, so **any** wired Ethernet
  (100M/1G/2.5G) is comfortable — no link benchmark needed.

## Correctness argument

- **Exactly-once:** a clip is processed only by the worker that wins its atomic `CREATE_NEW`
  claim; all others skip. No two workers ever matte the same clip → no output-file collision in
  the shared `matte\`.
- **No manifest clobber:** the shared truth is the claims dir (one atomic file per clip), never a
  shared JSON. Each machine keeps only its **own local** manifest — for its own fast resume and
  per-clip records (segments/secs); batch progress/ETA comes from the shared `.done` count.
- **No lost work on interruption:** ship precedes `.done`; a clip counts as complete to the
  cluster only once its bytes are on the share. A network/stop/crash mid-clip leaves the clip
  unclaimed-complete → redone (own-reclaim or orphan sweep), never half-published.

## Failure / edge cases

- **Network blip mid-clip (machine 2):** the GPU matte already writes to the *local* SSD, so it
  finishes; only the ship waits. Ship failure → no `.done`, local kept, retried. GPU work never
  wasted.
- **Share/machine 1 down:** machine 2 cannot read the next clip *or* reach the claims dir → it
  stalls cleanly (errors logged, current clip already local) and resumes when the share returns.
  Inherent — the 570 GB clips only exist on machine 1; the workflow already assumes machine 1 is
  on whenever machine 2 runs.
- **Both claim the same clip simultaneously:** impossible — `CREATE_NEW` lets exactly one win.
- **Orphaned claim (a box crashed mid-clip):** that one clip is skipped until own-restart-reclaim
  (same box) or a manual `--reclaim-orphans` (other box). `STALE_SECS=1800` ≫ clip time, so a
  merely-slow clip is never falsely reclaimed.
- **Safe stop / powering off machine 2:** the console's **Stop** (and window-close) writes the
  local stop-file, checked at the top of the loop (process_all.py:110), so the in-flight clip
  **finishes matting, ships, and marks `.done`** before the process exits — after which machine 2
  is idle and safe to power off. Stop is per-machine (local stop-file) so it never touches the
  other box. If instead machine 2 is **hard powered off** mid-clip, its claim orphans (no
  `.done`); on next startup the box's **own-reclaim** clears it and the idempotent re-ship cleanly
  redoes that one clip — no corruption, nothing half-published (the interrupted clip was never
  marked `.done`, so nothing downstream ever saw it).
- **Scratch disk fills on machine 2:** ship-and-delete caps it at ~1–2 GB; a runaway (ship
  lagging badly) surfaces as write errors — acceptable for a supervised run, and far under the
  50 GB headroom.
- **Re-matting after a shorter re-segment:** `_write_chip` already `rmtree`s the frames dir
  before writing (matte_blankplate.py:281), so a re-run never leaves stale tail frames — this
  holds identically for shipped dirs (ship overwrites the whole `<name>__*` set).

## Testing strategy

- **Claim atomicity (machine test):** spawn 2 processes racing to claim the same 500 names in a
  shared temp dir; assert every name is won exactly once, union == full set, no dupes. (Same-
  volume proxy for the SMB path; the SMB atomicity is a documented platform property, spot-
  checked once live.)
- **`--ship-dir` glob correctness:** a fixture `matte/` with `a__b__c__{spawn,idle,flourish}_*`
  plus a decoy `a__b__cx__idle_frames`; assert shipping clip `a__b__c` moves exactly its own
  artifacts and leaves the decoy.
- **Opt-out unchanged:** with neither flag, a small `--limit` run is byte-identical to current
  `main()` (same files, same manifest) — guards the no-regression requirement.
- **Own-reclaim / `--reclaim-orphans`:** seed stale + fresh claims with/without `.done`/output;
  assert only genuine orphans are cleared.
- **Console wiring:** unit-test that the env vars produce the right `process_cmd` argv and that
  `process_done_count` counts `.done` when claims mode is on.
- **Live 2-machine dry-run:** both boxes with `--limit N` against the real share; confirm
  disjoint clips, chips land in the one `matte\`, `.done` count == N₁+N₂, viewer opens the union.

## Runbook / docs

A `docs/two-machine-sweep.md` is a **first-class deliverable of this plan**, because the two
enabling steps are one-time **manual** setup the app cannot do for you:

1. **Share `D:\kartoff` from machine 1 (SMB) — step by step.** Not automatic. The guide gives the
   concrete commands, e.g.:
   ```powershell
   # On machine 1 (elevated PowerShell). Grant the machine-2 account read/write.
   New-SmbShare -Name kartoff -Path D:\kartoff -FullAccess "PAUL-AM5-DT\<user>"
   # (or -ChangeAccess for a dedicated account; -FullAccess "Everyone" only on a trusted LAN)
   Get-SmbShare kartoff                     # verify
   ```
   plus: enabling File & Printer Sharing through the firewall for the Private profile, setting the
   network to **Private** (not Public), and — from machine 2 — testing access and (if accounts
   differ) storing credentials:
   ```powershell
   # On machine 2
   Test-Path \\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips
   cmdkey /add:PAUL-AM5-DT /user:<user> /pass         # if machine-2 login != machine-1 account
   ```
   The GUI equivalent (right-click `D:\kartoff` → Properties → Sharing → Advanced Sharing →
   Permissions) is documented as the fallback. NTFS *and* share permissions both must allow the
   machine-2 account write access to `asset_chips\` and `claims\`.
2. **Stand up the GPU venv on machine 2** (the one real cost): clone/copy the repo + build
   `temp/asset-venv-matte` (py3.12 + onnxruntime-gpu 1.22/CUDA 12 + torch cu128 + MatAnyone2,
   per `tools/asset_matte/README.md` and the chip-asset-matting memory). The 2080 Ti (Turing) is
   CUDA-12 fine.

Plus the operational bits: the per-machine `run_console_m1.bat` / `run_console_m2.bat` contents
(env vars from the config table), how to Start/Stop each box (and that **Stop → wait for exit** is
the safe way to power machine 2 off), the `--reclaim-orphans` command (only if a box crashed
mid-run), and "delete `C:\kartoff_scratch` on machine 2 when the batch is done." `C:\kartoff_scratch`
itself is created automatically by the driver/console — no manual mkdir needed.
`tools/asset_matte/README.md` + the chip-asset-matting memory get a pointer to this doc.

## Alternatives considered

- **Static `--shard i/N` split** (the first proposal): dead simple, no claim primitive — but
  **caps machine 2 solo at its slice**, which breaks the daytime workflow, and needs hand-tuned
  weights for the 5080/2080 Ti gap. Rejected once the workflow was known.
- **Bidirectional march + separate output dirs + end-union:** machine 2 top-down, machine 1
  bottom-up, no atomic primitive. Near-zero duplication, but reintroduces an end-merge and its
  meeting-zone dedup is non-atomic (relies on existence-checks). Claims give exactly-once with a
  *shared* output dir (no merge) for the same ~20 lines. Rejected as strictly worse here.
- **Mirror-that-accumulates / end-of-run batch copy:** would need ~590 GB local on machine 2 and
  a 1.5 M-small-file copy at the end (slow, fragile). Rejected — ship-and-delete caps local at
  ~1–2 GB and spreads the transfer, hidden behind GPU time.
- **Write matte *directly* to the share (loop-frames on local scratch):** viable on solid wired
  gigabit and needs no ship logic, but puts network in the matte hot loop and (with a shared
  `--out`) reintroduces a shared manifest/loopdir. Ship-and-delete is link-agnostic and keeps the
  hot loop on the local SSD. Kept as a documented fallback, not the default.
- **Central job queue (Redis / SQLite / HTTP scheduler):** real distributed-work infra — wholly
  unjustified for two supervised boxes when an NTFS directory already provides atomic claims.
- **Copy the 570 GB clip set to machine 2:** impossible (50 GB free) and pointless (clip reads
  are cheap over SMB and hidden behind the GPU).
