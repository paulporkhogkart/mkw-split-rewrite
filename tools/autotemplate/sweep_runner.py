"""WSL2 sweep runner: walk the grid, record one clip per item, ground keep/discard.

Hardware (nxbt) is injected as `controller`; the orchestrator WS as `client`, so
the per-item logic is unit-testable with fakes. main() wires the real ones.

Module-level imports are intentionally restricted to `time` only so that
`from sweep_runner import SweepRunner` works on Windows where nxbt and
websockets are not installed.  Every other import is lazy (inside methods).
"""
import time


class SweepRunner:
    GROUND_THRESHOLD = 0.85
    MAX_VERIFY_ATTEMPTS = 30

    def __init__(self, grid, controller, client, *, idle_seconds=10.0, settle_seconds=0.8, lang="en_uk"):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
        self.settle = settle_seconds
        self.lang = lang

    def _begin(self, item):
        self.client.send({"type": "at_record_clip_begin", "item": item})

    def _mark(self, event):
        self.client.send({"type": "at_record_clip_mark", "event": event})

    def _exists(self, item) -> bool:
        return self.client.send({"type": "at_clip_exists", "item": item}).get("done", False)

    def capture_char(self, slug):
        if self._exists(slug):
            return None
        self._begin(slug)
        time.sleep(self.idle)                    # settled idle (no spawn-in)
        self.ctrl.press("A")                     # flourish → character_select drops
        self._mark("flourish")
        return self.client.wait_for("clip_done").get("events")

    # ------------------------------------------------------------------
    # Kart capture
    # ------------------------------------------------------------------

    def _ground_kart(self, kart_slug) -> bool:
        r = self.client.send({"type": "at_check_asset_match", "category": "karts",
                              "lang": self.lang, "name": kart_slug})
        return r.get("name_score", 0.0) >= self.GROUND_THRESHOLD

    def _recover_to(self, kart_slug):
        """Find the actually-selected kart by scanning the row, then step the horizontal delta."""
        row = [c.slug for c in self.grid.cells("karts")
               if c.coord[0] == self.grid.coord_of(kart_slug)[0]]
        here = next((k for k in row
                     if self.client.send({"type": "at_check_asset_match", "category": "karts",
                                         "lang": self.lang, "name": k}).get("name_score", 0.0)
                     >= self.GROUND_THRESHOLD), None)
        if here is None:
            return                                  # next loop's press re-tries blindly
        for press in self.grid.horizontal_delta(here, kart_slug):
            self.ctrl.press(press)

    def capture_kart(self, combo_slug, kart_slug, *, first=False):
        item = f"{combo_slug}__{kart_slug}"
        if self._exists(item):
            return None
        while True:
            self._begin(item)
            if first:                               # Standard Kart: off-and-back for spawn-in
                self.ctrl.press("DPAD_RIGHT")
                self.ctrl.press("DPAD_LEFT")
            else:
                self.ctrl.press("DPAD_RIGHT")       # swap onto this kart
            self._mark("swap")
            time.sleep(self.settle)                 # name plate settles
            if self._ground_kart(kart_slug):
                break
            self.client.send({"type": "at_record_clip_abort"})
            self._recover_to(kart_slug)             # step back; loop re-begins
        time.sleep(self.idle)                       # spawn-in already rolling; capture idle
        self.ctrl.press("A")                        # flourish → kart_select drops
        self._mark("flourish")
        ev = self.client.wait_for("clip_done").get("events")
        self.ctrl.press("B")                        # back to kart select (same kart, confirmed)
        return ev

    def sweep_karts(self, combo_slug):
        karts = [c.slug for c in self.grid.cells("karts")]
        out = []
        for i, kart in enumerate(karts):
            out.append(self.capture_kart(combo_slug, kart, first=(i == 0)))
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

        attempts = 0
        while True:
            cmd = {
                "type": "at_check_asset_match",
                "category": category,
                "lang": self.lang,
                "name": name,
            }
            if category == "characters" and costume != "base":
                cmd["costume"] = costume
            r = self.client.send(cmd)
            name_score = r.get("name_score", 0.0)
            costume_score = r.get("costume_score")
            if category == "characters" and costume != "base" and costume_score is not None:
                ok = name_score >= self.GROUND_THRESHOLD and costume_score >= 0.65
            else:
                ok = name_score >= self.GROUND_THRESHOLD
            if ok:
                break
            if attempts >= self.MAX_VERIFY_ATTEMPTS:
                raise RuntimeError(
                    f"verify_on: {slug!r} never grounded after {self.MAX_VERIFY_ATTEMPTS} presses"
                )
            attempts += 1
            print(f"  [verify_on] {slug!r} name_score={name_score:.3f}"
                  + (f" costume_score={costume_score:.3f}" if costume_score is not None else "")
                  + " — re-pressing DPAD_RIGHT")
            self.ctrl.press("DPAD_RIGHT")


# ── Real hardware controller (nxbt) ──────────────────────────────────────────

class NxbtController:
    """Real controller: nxbt sender holds the right-stick down for anti-spin.

    All nxbt imports are lazy so this class can be defined on Windows even
    though nxbt is only available in WSL2/Linux.
    """

    def __init__(self, mac, adapter="hci0"):
        import threading
        from controller import ProController
        from switch_bridge import ControllerState, sender_thread
        from full_runner import _press as _press_fn
        self._press_fn = _press_fn
        self.mac = mac
        self.ctrl = ProController(adapter=adapter)
        self.ctrl.connect(reconnect_addr=mac)
        self.state = ControllerState()
        # Hold right-stick down continuously for anti-spin before sender starts
        self.state.replay_update(0, 0, 0, 0, -127)
        self._stop = threading.Event()
        threading.Thread(
            target=sender_thread,
            args=(self.ctrl, self.state, self._stop),
            daemon=True,
        ).start()

    def press(self, b, duration=0.1):
        self._press_fn(self.state, b, duration=duration, dry_run=False)

    def hold(self, b, dur):
        from full_runner import _hold
        _hold(self.state, b, dur)

    def rstick_down(self, dur):
        # Already held continuously — no-op for explicit calls
        pass


# ── Real WS client ────────────────────────────────────────────────────────────

class WsClient:
    """Blocking request/reply + wait_for("clip_done") over the broadcaster WS.

    Mirrors CaptureClient from full_runner.py but adds a second unsolicited
    queue routed by message type == "clip_done".  Every other message goes to
    the reply queue used by send().
    """

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
                    if isinstance(msg, dict) and msg.get("type") == "clip_done":
                        self._unsolicited.put(msg)
                    else:
                        self._reply_q.put(msg)
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

    def wait_for(self, type_):
        """Block until an unsolicited message of the given type arrives."""
        assert type_ == "clip_done", f"wait_for: only clip_done supported, got {type_!r}"
        return self._unsolicited.get()


# ── Dry-run stubs ─────────────────────────────────────────────────────────────

class _DryController:
    """Logs every action to stdout; no hardware."""

    def press(self, b, duration=0.1):
        print(f"  [DRY] press {b}")

    def hold(self, b, dur):
        print(f"  [DRY] hold {b} {dur}s")

    def rstick_down(self, dur):
        print(f"  [DRY] rstick_down {dur}s")


class _DryClient:
    """Returns canned replies so the traversal completes without hanging."""

    _REPLIES = {
        "at_clip_exists":          {"type": "exists_result", "done": False},
        "at_record_clip_begin":    {"type": "clip_begun"},
        "at_record_clip_mark":     {"type": "marked"},
        "at_record_clip_abort":    {"type": "clip_aborted"},
        "at_check_asset_match":    {"type": "at_asset_score", "name_score": 0.95},
    }

    def send(self, msg, timeout=15.0):
        t = msg.get("type", "")
        print(f"  [DRY] send {t}")
        return self._REPLIES.get(t, {"type": "ok"})

    def wait_for(self, type_):
        print(f"  [DRY] wait_for {type_}")
        return {"type": "clip_done", "events": {"fps": 60}}


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    import argparse
    import os
    from grid import load_grid

    p = argparse.ArgumentParser(
        description="WSL2 clip sweep: walk the character/kart grid, record one clip per item."
    )
    p.add_argument("--mac", required=True,
                   help="Switch Bluetooth MAC address (e.g. E0:EF:BF:03:74:19)")
    p.add_argument("--capture-ws", default="ws://localhost:8766",
                   help="WebSocket URL of the clip-recorder broadcaster (default: ws://localhost:8766)")
    p.add_argument("--start-from", default=None, metavar="SLUG",
                   help="Resume from this character slug (skip earlier cells)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print all steps without opening controller or capture WS")
    a = p.parse_args()

    yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "clip_sweep.yaml")
    g = load_grid(yaml_path)

    print(f"MAC:         {a.mac}")
    print(f"Capture WS:  {a.capture_ws}")
    print(f"Grid YAML:   {yaml_path}")
    print(f"Start from:  {a.start_from or '(beginning)'}")
    print(f"Mode:        {'DRY RUN' if a.dry_run else 'LIVE'}\n")

    if a.dry_run:
        ctrl = _DryController()
        client = _DryClient()
        runner = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0)
    else:
        ctrl = NxbtController(a.mac)
        client = WsClient(a.capture_ws)
        runner = SweepRunner(g, ctrl, client)

    # Preamble: HOME -> Time Trials -> character select
    # Mirror the nav from full_capture.yaml: wake the Switch, open game,
    # navigate into the character select screen.  In dry-run the exact timings
    # are irrelevant -- the button sequence just needs to be present.
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


if __name__ == "__main__":
    main()
