"""WSL2 sweep runner: walk the grid, record one clip per item, ground keep/discard.

Hardware (nxbt) is injected as `controller`; the orchestrator WS as `client`, so
the per-item logic is unit-testable with fakes. main() wires the real ones.

Module-level imports are intentionally restricted to `time` only so that
`from sweep_runner import SweepRunner` works on Windows where nxbt and
websockets are not installed.  Every other import is lazy (inside methods).
"""
import time


class SweepRunner:
    GROUND_THRESHOLD = 0.70   # edge-method floor (SelectionTracker SELECTION_KART_FLOOR); tune live at bring-up
    MAX_VERIFY_ATTEMPTS = 30

    def __init__(self, grid, controller, client, *, idle_seconds=10.0, settle_seconds=0.8,
                 ground_timeout=4.0, ground_stable_reads=3, lang="en_uk"):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
        self.settle = settle_seconds
        self.ground_timeout = ground_timeout            # how long to POLL for a parked read (0 == one read)
        self.ground_stable_reads = ground_stable_reads  # consecutive identical reads == parked (not scrolling)
        self.lang = lang

    def _begin(self, item):
        self.client.send({"type": "at_record_clip_begin", "item": item})

    def _mark(self, event):
        self.client.send({"type": "at_record_clip_mark", "event": event})

    def _exists(self, item) -> bool:
        return self.client.send({"type": "at_clip_exists", "item": item}).get("done", False)

    def capture_char(self, slug):
        # ALWAYS press A to advance CHARACTER_SELECT -> KART_SELECT, even when the clip
        # already exists — otherwise sweep_karts runs on the character screen, DPAD_RIGHT
        # just slides the character cursor (Mario->Luigi->...), and grounding never finds
        # a kart (it re-begins the clip forever).
        if self._exists(slug):
            self.ctrl.press("A")                 # already captured; still advance to KART_SELECT
            return None
        self._begin(slug)
        time.sleep(self.idle)                    # settled idle (no spawn-in)
        self.ctrl.press("A")                     # flourish → advance to KART_SELECT
        self._mark("flourish")
        return self.client.wait_for("clip_done").get("events")

    # ------------------------------------------------------------------
    # Kart capture
    # ------------------------------------------------------------------

    def _live_selection(self) -> dict:
        """Current detected character/costume/kart from the live tracker. Raises a clear
        error if the tracker is an OLD build without the at_current_selection handler,
        so we don't silently read None and stampede DPAD_RIGHT."""
        sel = self.client.send({"type": "at_current_selection"})
        if sel.get("_dry"):
            return sel
        if sel.get("type") != "current_selection":
            raise RuntimeError(
                f"Tracker did not answer at_current_selection (got {sel.get('type')!r}: "
                f"{sel.get('message', '')}). You're running an OLD tracker build — close the "
                "tracker window and relaunch it (or run_sweep.bat) so it has the latest code, then retry.")
        return sel

    def _kart_key(self, sel):
        from grid import to_filename
        return to_filename(sel.get("kart") or "")

    def _char_key(self, sel):
        from grid import to_filename
        c = to_filename(sel.get("character") or "")
        return (c, to_filename(sel.get("costume") or "")) if c else None

    def _poll_until_stable(self, key_fn):
        """Poll the live tracker until key_fn(sel) is non-empty and UNCHANGED for
        ground_stable_reads consecutive reads — i.e. the cursor is PARKED on an item, not
        scrolling past it. An item seen only 'on the way' (one transient read) never
        satisfies this, so we never ground on something we're scrolling through. Returns
        the last selection dict (the caller checks whether it's the wanted item)."""
        deadline = time.monotonic() + self.ground_timeout
        prev, streak, sel = None, 0, {}
        while True:
            sel = self._live_selection()
            if sel.get("_dry"):
                return sel
            cur = key_fn(sel)
            if cur and cur == prev:
                streak += 1
                if streak >= self.ground_stable_reads:
                    return sel                      # parked
            else:
                prev, streak = cur, (1 if cur else 0)
            if time.monotonic() >= deadline:
                return sel                          # timeout: best-effort last read
            time.sleep(0.15)

    def _await_kart_change(self, prev):
        """After a nav press, wait until the detected kart LEAVES `prev`. Detection lags a press
        by a few frames, so re-reading immediately would show the stale pre-press kart — we'd
        think the step didn't land and press AGAIN, overshooting. No-op in dry-run / on timeout."""
        deadline = time.monotonic() + self.ground_timeout
        while time.monotonic() < deadline:
            sel = self._live_selection()
            if sel.get("_dry") or self._kart_key(sel) != prev:
                return
            time.sleep(0.05)

    def _park_on_kart(self, kart_slug):
        """Closed-loop: step ONE press at a time toward kart_slug (row first, then column),
        WAITING for each step to register (detection lags a press) before re-reading a PARKED
        selection — so the cursor can't fly past the target, and re-reading self-corrects even
        if a DPAD_DOWN doesn't land on the same column. Raises if it never arrives; dry-run no-op."""
        trow, tcol = self.grid.coord_of(kart_slug)
        here = ""
        for _ in range(self.MAX_VERIFY_ATTEMPTS):
            sel = self._poll_until_stable(self._kart_key)
            if sel.get("_dry"):
                return
            here = self._kart_key(sel)
            if here == kart_slug:
                return                              # parked on the target
            if not here and sel.get("character"):   # a character but no kart -> not on KART_SELECT
                raise RuntimeError(
                    f"_park_on_kart({kart_slug!r}): not on KART_SELECT — tracker shows "
                    f"character={sel.get('character')!r}, kart=None. capture_char must advance (press A) first.")
            try:
                hrow, hcol = self.grid.coord_of(here)
            except KeyError:
                self.ctrl.press("DPAD_RIGHT")       # unrecognised kart name: nudge
                self._await_kart_change(here)
                continue
            if hrow != trow:
                self.ctrl.press("DPAD_DOWN" if trow > hrow else "DPAD_UP")
            elif hcol != tcol:
                self.ctrl.press("DPAD_RIGHT" if tcol > hcol else "DPAD_LEFT")
            self._await_kart_change(here)           # let the step register before re-reading
        raise RuntimeError(f"_park_on_kart: never reached {kart_slug!r} (last parked on {here!r})")

    def capture_kart(self, combo_slug, kart_slug):
        item = f"{combo_slug}__{kart_slug}"
        if self._exists(item):
            return None
        # Step OFF the target to a neighbour, then CLOSED-LOOP back onto it (while recording) so
        # the return lands ON the target and captures its spawn-in. The back is a re-park, not a
        # single blind press, so a step eaten by the neighbour's spawn-in animation can't strand
        # us on the wrong kart. A column-0 kart has no left neighbour, so step right.
        _, tcol = self.grid.coord_of(kart_slug)
        off = "DPAD_RIGHT" if tcol == 0 else "DPAD_LEFT"
        attempts = 0
        while True:
            self._park_on_kart(kart_slug)           # park ON the target first
            self._begin(item)                       # record from before the spawn-in swap
            self.ctrl.press(off)                    # step OFF to a neighbour...
            self._park_on_kart(kart_slug)           # ...closed-loop BACK onto the target -> spawn-in
            self._mark("swap")
            sel = self._poll_until_stable(self._kart_key)   # confirm we're PARKED back on the target
            if sel.get("_dry") or self._kart_key(sel) == kart_slug:
                break
            self.client.send({"type": "at_record_clip_abort"})
            attempts += 1
            if attempts >= self.MAX_VERIFY_ATTEMPTS:
                raise RuntimeError(
                    f"capture_kart: {item!r} never grounded after {self.MAX_VERIFY_ATTEMPTS} attempts")
        time.sleep(self.idle)                       # capture idle (spawn-in already recorded)
        self.ctrl.press("A")                        # flourish → kart_select drops
        self._mark("flourish")
        ev = self.client.wait_for("clip_done").get("events")
        self.ctrl.press("B")                        # back to kart select (same kart, confirmed)
        return ev

    def sweep_karts(self, combo_slug):
        # Anti-spin runs all the time now (the agent holds it on by default), so no per-screen
        # gating here.
        karts = [c.slug for c in self.grid.cells("karts")]
        out = [self.capture_kart(combo_slug, kart) for kart in karts]
        self.ctrl.press("B")                        # kart select → character select
        return out

    def verify_on(self, slug, category):
        """Re-press the last navigation delta until at_check_asset_match >= threshold.

        For characters the slug is ``<char>__<costume>`` and we key on the
        ``name`` field using the character portion (everything before ``__``).
        When the costume is not ``base`` we also require the costume score to
        be >= 0.65 so that e.g. ``mario__touring`` never silently grounds on a
        different costume whose name plate still reads "Mario".
        For karts the slug is the full kart slug directly.

        In dry-run (or any situation where _DryClient returns a canned 0.95)
        this passes on the first check.  On real hardware we keep re-pressing
        DPAD_RIGHT until the tracker confirms the correct item is displayed.
        """
        if category == "characters":
            # Slug format: "mario__base" or "mario__touring"
            parts = slug.split("__")
            name = parts[0]
            costume = parts[1] if len(parts) > 1 else "base"
        else:
            name = slug
            costume = None

        from grid import to_filename
        key = self._char_key if category == "characters" else self._kart_key

        def matches(s):
            if category == "characters":
                return (to_filename(s.get("character") or "") == name
                        and (costume == "base" or to_filename(s.get("costume") or "") == costume))
            return to_filename(s.get("kart") or "") == name

        attempts = 0
        while True:
            # Wait for the cursor to PARK (held stable, not scrolling), THEN check — so we
            # never ground on an item that's only flashing past on the way.
            sel = self._poll_until_stable(key)
            if sel.get("_dry") or matches(sel):
                break                               # parked on the target (dry-run grounds at once)
            if attempts >= self.MAX_VERIFY_ATTEMPTS:
                raise RuntimeError(
                    f"verify_on: {slug!r} never grounded after {self.MAX_VERIFY_ATTEMPTS} presses "
                    f"(tracker last parked on character={sel.get('character')!r} "
                    f"costume={sel.get('costume')!r} kart={sel.get('kart')!r})"
                )
            attempts += 1
            print(f"  [verify_on] {slug!r}: parked on character={sel.get('character')!r} "
                  f"costume={sel.get('costume')!r} kart={sel.get('kart')!r} — pressing DPAD_RIGHT")
            self.ctrl.press("DPAD_RIGHT")


# ── Bridge controller (Windows → WSL2 agent) ─────────────────────────────────

class BridgeController:
    """Windows-side controller that delegates all button presses to the WSL2
    controller_agent via TCP (controller_bridge.ControllerBridge).

    The agent holds the R-stick DOWN continuously (anti-spin) for the whole
    session — BridgeController doesn't need to manage that itself.

    The import of ControllerBridge is lazy (inside __init__) so this class
    can be defined even before controller_bridge.py is on sys.path.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7878):
        import sys
        import os
        # Ensure tools/autotemplate is on sys.path so controller_bridge is importable.
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        from controller_bridge import ControllerBridge
        self._bridge = ControllerBridge(host=host, port=port)
        self._bridge.connect()

    def press(self, button: str, duration: float = 0.1) -> None:
        self._bridge.press(button, duration=duration)

    def hold(self, button: str, dur: float = 1.0) -> None:
        self._bridge.hold(button, duration=dur)

    def rstick_down(self) -> None:
        """Re-assert anti-spin on the agent (idempotent)."""
        self._bridge.rstick_down()

    def antispin(self, on: bool) -> None:
        """Hold the R-stick DOWN on the agent while `on` (kart-screen anti-spin)."""
        self._bridge.antispin(on)

    def get_status(self) -> dict:
        """{'connected': bool, 'mac': str} from the agent (safe before nxbt is ready)."""
        return self._bridge.get_status()

    def wait_ready(self, timeout: float = 90.0) -> bool:
        """Block until the agent reports the Switch is connected (or timeout)."""
        import time as _t
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            st = self._bridge.get_status()
            if st.get("connected"):
                print(f"  controller ready (Switch MAC={st.get('mac') or '?'})")
                return True
            _t.sleep(1.0)
        return False

    def stop(self) -> None:
        """Close the TCP connection to the agent."""
        self._bridge.close()


# ── Real WS client ────────────────────────────────────────────────────────────

class WsClient:
    """Blocking request/reply + wait_for("clip_done") over the broadcaster WS.

    Mirrors CaptureClient from full_runner.py but adds a second unsolicited
    queue routed by message type == "clip_done".  Every other message goes to
    the reply queue used by send().
    """

    # Command-reply types (responses to send()). EVERYTHING else the broadcaster
    # pushes — heartbeat, selection_update, screen_change, lap_update, … — is an
    # UNSOLICITED broadcast and must NOT be mistaken for a reply.
    _REPLY_TYPES = frozenset({
        "at_done", "at_error", "at_tell_score", "at_asset_score",
        "clip_begun", "marked", "clip_aborted", "exists_result", "current_selection",
    })

    def __init__(self, url):
        import asyncio
        import threading
        import queue as _queue
        import json as _json
        self._json = _json
        self._url = url
        self._loop = asyncio.new_event_loop()
        self._ws = None
        self._reply_q = _queue.Queue()
        self._unsolicited = _queue.Queue()
        self._ready = threading.Event()
        self._err = None
        threading.Thread(target=self._run, daemon=True).start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError(f"cannot connect to broadcaster at {url} (timeout)")
        if self._err:
            raise RuntimeError(self._err)

    def _run(self):
        import asyncio
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        try:
            import websockets
        except ImportError:
            self._err = "websockets not installed — pip install websockets"
            self._ready.set()
            return
        try:
            async with websockets.connect(self._url) as ws:
                self._ws = ws
                self._ready.set()
                async for raw in ws:
                    try:
                        msg = self._json.loads(raw)
                    except Exception:
                        continue
                    t = msg.get("type") if isinstance(msg, dict) else None
                    if t == "clip_done":
                        self._unsolicited.put(msg)
                    elif t in self._REPLY_TYPES:
                        self._reply_q.put(msg)
                    # else: unsolicited broadcast (heartbeat/selection_update/screen_change/…) — ignore
        except Exception as exc:
            self._err = str(exc)
            self._ready.set()

    def send(self, cmd, timeout=15.0):
        """Send a command dict; block until a non-clip_done reply arrives."""
        import asyncio
        asyncio.run_coroutine_threadsafe(self._ws.send(self._json.dumps(cmd)), self._loop)
        try:
            return self._reply_q.get(timeout=timeout)
        except Exception:
            return {"type": "error", "message": f"Timeout waiting for reply to {cmd.get('type')}"}

    def wait_for(self, type_, timeout=60.0):
        """Block until an unsolicited message of the given type arrives (or timeout).
        The tracker's record watchdog normally emits clip_done (with an error field) within
        seconds of a failed record; this timeout is only a backstop against an infinite hang."""
        assert type_ == "clip_done", f"wait_for: only clip_done supported, got {type_!r}"
        try:
            return self._unsolicited.get(timeout=timeout)
        except Exception:
            print(f"  [wait_for] no clip_done in {timeout:.0f}s — tracker may not be recording; continuing.",
                  flush=True)
            return {"type": "clip_done", "_timeout": True, "events": {}}

    def close(self):
        """Stop the asyncio loop (disconnects the WebSocket)."""
        self._loop.call_soon_threadsafe(self._loop.stop)


# ── Dry-run stubs ─────────────────────────────────────────────────────────────

class _DryController:
    """Logs every action to stdout; no hardware."""

    def press(self, b, duration=0.1):
        print(f"  [DRY] press {b}")

    def hold(self, b, dur):
        print(f"  [DRY] hold {b} {dur}s")

    def rstick_down(self):
        print("  [DRY] rstick_down")

    def antispin(self, on):
        print(f"  [DRY] antispin {'ON' if on else 'OFF'}")

    def stop(self):
        print("  [DRY] controller stop (no-op)")


class _DryClient:
    """Returns canned replies so the traversal completes without hanging."""

    _REPLIES = {
        "at_clip_exists":          {"type": "exists_result", "done": False},
        "at_record_clip_begin":    {"type": "clip_begun"},
        "at_record_clip_mark":     {"type": "marked"},
        "at_record_clip_abort":    {"type": "clip_aborted"},
        "at_check_asset_match":    {"type": "at_asset_score", "name_score": 0.95},
        "at_current_selection":    {"type": "current_selection", "_dry": True},
    }

    def send(self, msg, timeout=15.0):
        t = msg.get("type", "")
        print(f"  [DRY] send {t}")
        return self._REPLIES.get(t, {"type": "ok"})

    def wait_for(self, type_):
        print(f"  [DRY] wait_for {type_}")
        return {"type": "clip_done", "events": {"fps": 60}}

    def close(self):
        print("  [DRY] client close (no-op)")


# ── CLI entry point ───────────────────────────────────────────────────────────

def _pilot(ctrl) -> bool:
    """Keyboard-drive the Switch via the emulated controller until the operator presses
    Enter (start the sweep from the current screen) or q (quit). Windows-only (msvcrt)."""
    import msvcrt
    KEYS = {"a": "A", "b": "B", "x": "X", "y": "Y", "l": "L", "r": "R",
            "+": "PLUS", "-": "MINUS", "h": "HOME"}
    ARROWS = {"H": "DPAD_UP", "P": "DPAD_DOWN", "K": "DPAD_LEFT", "M": "DPAD_RIGHT"}
    print("\n== PILOT — drive to character-select, then start ==")
    print("  arrow keys -> D-pad    .    a b x y l r  + - (plus/minus)  h (HOME) -> buttons")
    print("  ENTER -> start the sweep from the current screen    .    q -> quit\n", flush=True)
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print("  -> starting sweep from here\n", flush=True)
            return True
        if ch in ("q", "Q", "\x03"):
            return False
        if ch in ("\x00", "\xe0"):                 # arrow / function-key prefix
            btn = ARROWS.get(msvcrt.getwch())
            if btn:
                ctrl.press(btn)
                print(f"  {btn}", flush=True)
            continue
        btn = KEYS.get(ch.lower())
        if btn:
            ctrl.press(btn)
            print(f"  {btn}", flush=True)


def main():
    import argparse
    import os
    from grid import load_grid

    p = argparse.ArgumentParser(
        description="Clip sweep: walk the character/kart grid, record one clip per item. "
                    "Runs on Windows; delegates controller to the WSL2 controller_agent via TCP."
    )
    p.add_argument("--capture-ws", default="ws://127.0.0.1:8766",
                   help="WebSocket URL of the clip-recorder broadcaster (default: ws://127.0.0.1:8766). "
                        "Use 127.0.0.1, NOT 'localhost' — the broadcaster binds IPv4 (0.0.0.0); localhost can "
                        "resolve to IPv6 (::1) on Windows and the connection is refused (WinError 1225).")
    p.add_argument("--agent-host", default="127.0.0.1",
                   help="Host where controller_agent.py is listening (default: 127.0.0.1)")
    p.add_argument("--agent-port", type=int, default=7878,
                   help="Port where controller_agent.py is listening (default: 7878)")
    p.add_argument("--start-from", default=None, metavar="SLUG",
                   help="Resume from this character slug (skip earlier cells)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print all steps without opening controller or capture WS")
    p.add_argument("--pilot", action="store_true",
                   help="Drive the Switch to character-select yourself with the keyboard, then press "
                        "Enter to start the sweep from there (skips the blind HOME preamble).")
    a = p.parse_args()

    yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "clip_sweep.yaml")
    g = load_grid(yaml_path)

    print(f"Agent:       {a.agent_host}:{a.agent_port}")
    print(f"Capture WS:  {a.capture_ws}")
    print(f"Grid YAML:   {yaml_path}")
    print(f"Start from:  {a.start_from or '(beginning)'}")
    print(f"Mode:        {'DRY RUN' if a.dry_run else 'LIVE'}\n")

    if a.dry_run:
        ctrl = _DryController()
        client = _DryClient()
        runner = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0)
    else:
        ctrl = BridgeController(host=a.agent_host, port=a.agent_port)
        client = WsClient(a.capture_ws)
        runner = SweepRunner(g, ctrl, client)

    try:
        if a.pilot and not a.dry_run:
            print("Waiting for the controller agent to connect to the Switch...", flush=True)
            if hasattr(ctrl, "wait_ready") and not ctrl.wait_ready():
                print("Controller never became ready — is start_agent.py running and the Switch on? Aborting.")
                return
            if not _pilot(ctrl):
                print("Pilot aborted; sweep not started.")
                return
        else:
            # Blind preamble: HOME -> Time Trials -> character select.  Assumes you START at
            # HOME with MKW hovered; use --pilot if you're not in that exact state.
            print("-- Preamble: HOME -> Time Trials -> character select --")
            for btn in ["HOME", "A", "A", "A"]:         # wake + title screen
                ctrl.press(btn)
            if not a.dry_run:
                time.sleep(3.0)                           # wait for menu to load
            ctrl.press("A")                               # single player
            if not a.dry_run:
                time.sleep(1.5)
            ctrl.press("A")                               # Time Trials
            if not a.dry_run:
                time.sleep(2.0)                           # wait for character select

        # Main sweep: characters
        skipping = bool(a.start_from)
        for slug, presses in g.sweep_steps("characters"):
            if skipping:
                if slug == a.start_from:
                    skipping = False
                else:
                    print(f"  [skip] {slug}")
                    continue

            print(f"\n-- char: {slug} --")
            for btn in presses:
                ctrl.press(btn)

            runner.verify_on(slug, "characters")
            runner.capture_char(slug)
            runner.sweep_karts(slug)

        print("\nSweep complete.")
    finally:
        ctrl.stop()
        client.close()


if __name__ == "__main__":
    main()
