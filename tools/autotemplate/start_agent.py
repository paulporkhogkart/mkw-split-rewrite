"""One-command launcher for the clip-sweep controller agent in WSL2 (run on Windows).

Reuses the sibling **nxauto** app's already-configured setup so you don't re-enter
anything: it reads the WSL distro, Bluetooth adapter busid, and Switch MAC from
`nxauto.db`, the WSL sudo password from the Windows keyring (nxauto stored it), and
nxauto's venv (which has nxbt — the only pip dep our agent needs). Then it:

    wake the WSL2 VM  ->  usbipd-attach the BT adapter  ->  spawn our controller_agent
    in WSL2 under sudo, reconnecting to the stored Switch MAC (no Change Grip/Order).

Run:
    python tools/autotemplate/start_agent.py                 # all values from nxauto
    python tools/autotemplate/start_agent.py --pair          # first pairing (Change Grip/Order)
    python tools/autotemplate/start_agent.py --distro Ubuntu --busid 4-15 --mac AA:BB:..  # overrides

This mirrors nxauto's `start_controller_agent` (src-tauri/src/lib.rs) but spawns OUR
`controller_agent.py` (which holds the R-stick down for anti-spin). Ctrl-C to stop.
"""
import argparse
import json
import os
import subprocess
import sys

# nxauto's stored config + venv (it was only ever set up on this machine, so these
# are the right values). DB: dev = repo root; release = %APPDATA%\nxauto.
_NXAUTO_DB_CANDIDATES = [
    r"C:\Development\nxauto\nxauto.db",
    os.path.join(os.environ.get("APPDATA", ""), "nxauto", "nxauto.db"),
]
NXAUTO_VENV_PY = "~/.local/share/nxauto/venv/bin/python"   # hardcoded in nxauto's lib.rs; has nxbt
KEYRING_SERVICE, KEYRING_USER = "nxauto", "wsl-sudo"
AGENT_PORT_DEFAULT = 7878


# ── pure helpers (unit-tested) ────────────────────────────────────────────────

def win_to_wsl_path(win_path: str) -> str:
    """`C:\\development\\x` -> `/mnt/c/development/x` (case-correct drive letter)."""
    p = os.path.abspath(win_path)
    drive, rest = p[0], p[2:].replace("\\", "/")
    return f"/mnt/{drive.lower()}{rest}"


def parse_cfg_value(raw: str) -> str:
    """nxauto stores config values JSON-encoded (`"Ubuntu"`, `7878`). Decode to str."""
    try:
        return str(json.loads(raw))
    except (ValueError, TypeError):
        return str(raw).strip().strip('"')


def parse_busid_from_usbipd(usbipd_list_output: str) -> str:
    """Find the Bluetooth adapter's busid in `usbipd list` output (first column)."""
    for line in usbipd_list_output.splitlines():
        if "bluetooth" in line.lower():
            parts = line.split()
            if parts and "-" in parts[0]:
                return parts[0]
    return ""


def first_distro(wsl_list_quiet_output: str) -> str:
    """First non-empty distro name from `wsl --list --quiet` (already decoded)."""
    for line in wsl_list_quiet_output.splitlines():
        name = line.strip().strip("\x00").replace("﻿", "")
        if name:
            return name
    return ""


# ── config / detection (touch the system) ─────────────────────────────────────

def _nxauto_db_path() -> str:
    for cand in _NXAUTO_DB_CANDIDATES:
        if cand and os.path.exists(cand):
            return cand
    return ""


def nxauto_cfg(key: str) -> str:
    """Read one config value from nxauto.db (read-only). '' if unavailable."""
    import sqlite3
    db = _nxauto_db_path()
    if not db:
        return ""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        finally:
            con.close()
        return parse_cfg_value(row[0]) if row else ""
    except Exception:
        return ""


def detect_distro() -> str:
    try:
        out = subprocess.run(["wsl", "--list", "--quiet"],
                             capture_output=True).stdout
        # wsl emits UTF-16LE on Windows
        for enc in ("utf-16-le", "utf-8"):
            try:
                return first_distro(out.decode(enc, errors="ignore"))
            except Exception:
                continue
    except Exception:
        pass
    return ""


def detect_busid() -> str:
    try:
        out = subprocess.run(["usbipd", "list"], capture_output=True, text=True).stdout
        return parse_busid_from_usbipd(out)
    except Exception:
        return ""


def sudo_password() -> str:
    try:
        import keyring
        pw = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if pw:
            return pw
    except Exception:
        pass
    import getpass
    return getpass.getpass("WSL sudo password (for nxbt Bluetooth; nxauto's keyring not readable): ")


# ── launch ────────────────────────────────────────────────────────────────────

def run(args) -> int:
    distro = args.distro or nxauto_cfg("wsl_distro") or detect_distro()
    busid = args.busid or detect_busid() or nxauto_cfg("bt_busid")   # prefer the CURRENT busid; usb ids drift
    mac = args.mac or nxauto_cfg("switch_mac")
    agent_dir_wsl = win_to_wsl_path(os.path.dirname(os.path.abspath(__file__)))

    if not distro:
        print("[start_agent] Could not determine WSL distro (pass --distro).", file=sys.stderr)
        return 2
    print(f"[start_agent] distro={distro!r}  busid={busid or '(none)'}  "
          f"mac={mac or '(pair mode)'}  agent_dir={agent_dir_wsl}")

    pw = sudo_password()

    # 1. Wake the WSL2 VM (blocks until up) so the usbipd attach below can't race a cold VM.
    subprocess.run(["wsl", "-d", distro, "--", "echo", "ready"],
                   capture_output=True)

    # 2. Attach the BT adapter to WSL2 (idempotent; warns if already attached / not bound).
    if busid:
        r = subprocess.run(["usbipd", "attach", "--busid", busid, "--wsl", distro],
                           capture_output=True, text=True)
        msg = (r.stderr or r.stdout).strip()
        if r.returncode == 0:
            print(f"[start_agent] BT adapter {busid} attached to WSL2.")
        else:
            print(f"[start_agent] usbipd attach note: {msg}")
            if "not shared" in msg.lower() or "bind" in msg.lower():
                print(f"[start_agent]   -> one-time, run as Admin: usbipd bind --busid {busid}")

    # 3. Spawn our agent in WSL2 (sudo -S reads the password from stdin; -E keeps env).
    reconnect = "" if args.pair or not mac else f" --reconnect-addr {mac}"
    inner = (f"cd '{agent_dir_wsl}' && sudo -S -E {args.venv_python} "
             f"controller_agent.py --port {args.port}{reconnect}")
    print(f"[start_agent] launching agent on :{args.port} "
          f"({'PAIR mode — open Change Grip/Order on the Switch' if not reconnect else 'reconnecting to stored MAC'})\n")
    proc = subprocess.Popen(["wsl", "-d", distro, "--", "bash", "-c", inner],
                            stdin=subprocess.PIPE, text=True)
    try:
        proc.stdin.write(pw + "\n")   # consumed by `sudo -S`
        proc.stdin.flush()
        proc.stdin.close()            # the agent itself never reads stdin
    except (BrokenPipeError, OSError):
        pass
    try:
        return proc.wait()            # stream the agent's output until Ctrl-C
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def pkill_cmd(distro: str, port: int = 7878) -> list:
    """argv that kills the in-WSL controller_agent for `port` (run under sudo -S)."""
    return ["wsl", "-d", distro, "--", "sudo", "-S", "pkill", "-f",
            f"controller_agent.py --port {port}"]


def stop_agent(distro=None, port: int = 7878) -> int:
    """Best-effort: kill the in-WSL controller_agent so a later start_agent can reconnect
    cleanly (the agent has no in-band shutdown command). Returns the subprocess rc (0 = ok)."""
    distro = distro or nxauto_cfg("wsl_distro") or detect_distro()
    if not distro:
        print("[stop_agent] no WSL distro found; nothing to stop.", file=sys.stderr)
        return 2
    pw = sudo_password()
    proc = subprocess.run(pkill_cmd(distro, port), input=pw + "\n",
                          capture_output=True, text=True)
    print(f"[stop_agent] pkill controller_agent on {distro}: rc={proc.returncode}")
    return proc.returncode


def main():
    p = argparse.ArgumentParser(description="Launch the clip-sweep nxbt controller agent in WSL2 (reuses nxauto's config).")
    p.add_argument("--distro", default=None, help="WSL distro (default: nxauto.db wsl_distro, else autodetect).")
    p.add_argument("--busid", default=None, help="Bluetooth adapter usbipd busid (default: nxauto.db bt_busid, else autodetect).")
    p.add_argument("--mac", default=None, help="Switch MAC to reconnect to (default: nxauto.db switch_mac).")
    p.add_argument("--pair", action="store_true", help="First-time pairing: ignore the MAC and wait for Change Grip/Order.")
    p.add_argument("--port", type=int, default=AGENT_PORT_DEFAULT, help="Agent TCP port (default 7878).")
    p.add_argument("--venv-python", default=NXAUTO_VENV_PY, dest="venv_python",
                   help="WSL python with nxbt (default: nxauto's venv).")
    p.add_argument("--stop", action="store_true",
                   help="Kill the in-WSL controller_agent and exit (clean teardown).")
    args = p.parse_args()
    if args.stop:
        sys.exit(stop_agent(distro=args.distro, port=args.port))
    sys.exit(run(args))


if __name__ == "__main__":
    main()
