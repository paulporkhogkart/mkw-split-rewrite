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
                 ground_timeout=4.0, ground_stable_reads=3, lang="en_uk", stop_check=None,
                 screen_timeout=3.0, nav_settle=0.3):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
        self.settle = settle_seconds
        self.ground_timeout = ground_timeout
        self.ground_stable_reads = ground_stable_reads
        self.lang = lang
        self.stop_check = stop_check
        self.screen_timeout = screen_timeout    # per-attempt wait for a screen tell to confirm by score
        self.nav_settle = nav_settle            # pause after each grid nav press (cursor + costume settle)

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

    def _char_slug(self, sel) -> str:
        """Live (character, costume) as a cell slug '<char>__<costume>' ('' if no character)."""
        key = self._char_key(sel)
        return f"{key[0]}__{key[1]}" if key else ""

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

    def _await_change(self, prev, slug_fn):
        """After a nav press, wait until the detected cell slug (per slug_fn) LEAVES `prev` —
        detection lags a press, so re-reading immediately shows the stale cell and over-presses.
        No-op in dry-run / on timeout."""
        deadline = time.monotonic() + self.ground_timeout
        while time.monotonic() < deadline:
            sel = self._live_selection()
            if sel.get("_dry") or slug_fn(sel) != prev:
                return
            time.sleep(0.05)

    def _park_on(self, target_slug, key_fn, slug_fn, what):
        """Closed-loop grid nav shared by character + kart select. Read the live PARKED cell, step
        ONE press toward target_slug (row first, then column), wait for the step to register, then
        re-read — self-correcting overshoot/undershoot.

        CRITICAL: NEVER press on an invalid/unrecognised read. A transient mis-read — e.g. a
        costume mid-settle giving '<char>__' or a costume lagged from the previous cell — used to
        hit a blind DPAD_RIGHT 'nudge' that drifted the cursor rightward (overshoot) and oscillated
        it at the base↔costume boundary. Instead we RE-POLL until the read is a real cell, and
        space out inputs (nav_settle) so the cursor + costume settle. Logs each step (no hardware
        repro on the dev box). Raises if it never arrives; dry-run no-op."""
        trow, tcol = self.grid.coord_of(target_slug)
        here, invalid = "", 0
        for _ in range(self.MAX_VERIFY_ATTEMPTS):
            sel = self._poll_until_stable(key_fn)
            if sel.get("_dry"):
                return
            here = slug_fn(sel)
            if here == target_slug:
                return                              # parked on the target cell
            if not here:                            # nothing detected -> not on this screen
                raise RuntimeError(
                    f"_park_on({target_slug!r}): no item detected — not on {what} "
                    f"(tracker shows character={sel.get('character')!r} kart={sel.get('kart')!r}).")
            try:
                hrow, hcol = self.grid.coord_of(here)
            except KeyError:
                # Unrecognised cell = a transient mis-read (costume mid-settle / lagged). DO NOT
                # press — a blind nudge here is the overshoot/oscillation bug. Re-poll; raise only
                # if it stays unrecognised (a genuinely unmapped cell).
                invalid += 1
                if invalid > max(3, self.ground_stable_reads):
                    raise RuntimeError(
                        f"_park_on({target_slug!r}): stuck reading an unrecognised cell {here!r} on {what}.")
                time.sleep(self.nav_settle)
                continue
            invalid = 0
            btn = (("DPAD_DOWN" if trow > hrow else "DPAD_UP") if hrow != trow
                   else ("DPAD_RIGHT" if tcol > hcol else "DPAD_LEFT"))
            print(f"  [nav {what}] {here} {(hrow, hcol)} -> {target_slug} {(trow, tcol)}: {btn}", flush=True)
            self.ctrl.press(btn)
            self._await_change(here, slug_fn)       # let the step register before re-reading
            time.sleep(self.nav_settle)             # space out inputs so cursor + costume settle
        raise RuntimeError(f"_park_on: never reached {target_slug!r} (last on {here!r}) on {what}.")

    def _park_on_kart(self, kart_slug):
        """Closed-loop kart-select nav (overshoot/undershoot safe). See _park_on."""
        self._park_on(kart_slug, self._kart_key, self._kart_key, "KART_SELECT")

    def _park_on_char(self, char_slug):
        """Closed-loop character+costume nav (overshoot/undershoot safe; a different costume of the
        same character is a different cell). See _park_on."""
        self._park_on(char_slug, self._char_key, self._char_slug, "CHARACTER_SELECT")

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
            self._await_change(kart_slug, self._kart_key)   # wait for the OFF to land (detection lags)
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
        # kart -> (not char nor kart): the flourish A advances OFF kart_select. Verify the
        # DEPARTURE by score (kart_select tell stops scoring); if it's still scoring the A was
        # eaten, so re-fire it. Non-fatal — if it never departs, _return_to below still verifies.
        departed = False
        for _ in range(3):
            if self._wait_off_screen("KART_SELECT"):
                departed = True
                break
            self.ctrl.press("A")                                # eaten flourish → re-fire
        if not departed:
            print("  [capture_kart] kart_select still scoring after flourish "
                  "(A may not have registered) — returning anyway", flush=True)
        self._return_to("KART_SELECT", "after kart flourish")   # B back — verify it SCORES
        return ev

    def _confirm_screen(self, name) -> bool:
        """True iff screen `name`'s TELL is actively confirming on the live frame (its score is
        over the detector's threshold). NOT the held current_screen: that sticks on the
        unmatched post-kart intermediary screen and would falsely read KART_SELECT, so a return
        could 'succeed' while we're still on the intermediary. Dry-run True."""
        rep = self.client.send({"type": "at_screen_score", "screen": name})
        if rep.get("_dry"):
            return True
        return bool(rep.get("detected")) if rep.get("type") == "screen_score" else False

    def _wait_screen(self, name, timeout=None) -> bool:
        """True once screen `name`'s tell actively CONFIRMS by score within `timeout` (polls at
        least once). Score-based, not the held screen name — see _confirm_screen. Dry-run True."""
        timeout = self.screen_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            if self._confirm_screen(name):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def _wait_off_screen(self, name, timeout=None) -> bool:
        """True once screen `name` STOPS confirming by score within `timeout` (we've LEFT it) —
        the 'kart -> not-kart' departure after a flourish. Dry-run True."""
        timeout = self.screen_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            rep = self.client.send({"type": "at_screen_score", "screen": name})
            if rep.get("_dry"):
                return True
            if not (rep.get("type") == "screen_score" and rep.get("detected")):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def _return_to(self, screen_name, what):
        """Press B until screen `screen_name`'s tell SCORES (confirms) — a B can be eaten by a
        transition animation, and the held current_screen would lie, so verify by score."""
        for _ in range(6):
            self.ctrl.press("B")
            if self._wait_screen(screen_name):
                return
        raise RuntimeError(f"_return_to: never reached {screen_name} after 6 B presses ({what})")

    def _stop_requested(self) -> bool:
        return bool(self.stop_check and self.stop_check())

    def sweep_karts(self, combo_slug, karts=None):
        # Anti-spin runs all the time (the agent holds it on by default).  Returns True if a
        # pause was requested mid-row (after returning to CHARACTER_SELECT), else False.
        # `karts` defaults to the full kart row; --sample passes a subset.
        karts = karts if karts is not None else [c.slug for c in self.grid.cells("karts")]
        # CHARACTER_SELECT -> KART_SELECT (capture_char pressed A): confirm by tell SCORE that we
        # actually landed on KART_SELECT before navigating, else _park_on_kart spins on the char
        # screen reading the committed (persisted) kart.
        if not self._wait_screen("KART_SELECT", timeout=max(self.screen_timeout, 8.0)):
            raise RuntimeError(
                f"sweep_karts({combo_slug!r}): KART_SELECT never confirmed after capture_char "
                "advanced — the char->kart transition did not land.")
        for kart in karts:
            if self._stop_requested():
                self._return_to("CHARACTER_SELECT", "pause mid kart row")
                return True
            self.capture_kart(combo_slug, kart)
        self._return_to("CHARACTER_SELECT", "after kart row")
        return False


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
        "current_screen", "screen_score",
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
        "at_current_screen":       {"type": "current_screen", "_dry": True},
        "at_screen_score":         {"type": "screen_score", "_dry": True},
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

def sample_grid(grid, chars, karts, seed=0):
    """Resolve the --sample picks. `chars`/`karts` are each either a count (int or digit-string →
    that many RANDOM cells, reproducible by `seed`; characters default to base costume) OR an
    explicit comma-string / list of exact cell slugs (which MAY include costumes, e.g.
    'mario__touring'). Returns (char slugs, kart slugs). Raises ValueError on an unknown slug."""
    import random
    rng = random.Random(seed)

    def pick(arg, category, base_only):
        cells = grid.cells(category)
        if isinstance(arg, int) or (isinstance(arg, str) and arg.strip().isdigit()):
            pool = [c.slug for c in cells if not base_only or c.slug.endswith("__base")]
            return sorted(rng.sample(pool, min(int(arg), len(pool))))
        items = arg if isinstance(arg, (list, tuple)) else [s.strip() for s in str(arg).split(",") if s.strip()]
        valid = {c.slug for c in cells}
        bad = [s for s in items if s not in valid]
        if bad:
            raise ValueError(f"sample_grid: unknown {category} slug(s): {bad}")
        return list(items)

    return pick(chars, "characters", True), pick(karts, "karts", False)


def main():
    import argparse
    import os
    from grid import load_grid

    p = argparse.ArgumentParser(
        description="Clip sweep: walk the character/kart grid, record one clip per item. "
                    "Runs on Windows; delegates the controller to the WSL2 controller_agent via TCP. "
                    "Drive to character-select first (the console's manual cluster), then start this.")
    p.add_argument("--capture-ws", default="ws://127.0.0.1:8766",
                   help="WebSocket URL of the clip-recorder broadcaster (use 127.0.0.1, not localhost).")
    p.add_argument("--agent-host", default="127.0.0.1", help="controller_agent host (default 127.0.0.1)")
    p.add_argument("--agent-port", type=int, default=7878, help="controller_agent port (default 7878)")
    p.add_argument("--start-from", default=None, metavar="SLUG",
                   help="Resume from this character slug (skip earlier cells).")
    p.add_argument("--stop-file", default=None, metavar="PATH",
                   help="Graceful-stop flag: when this file exists, finish the current clip, return "
                        "to CHARACTER_SELECT, and exit (the console creates it for Pause/Stop).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print all steps without opening controller or capture WS.")
    p.add_argument("--sample-chars", default=None, metavar="N|slug,slug,...",
                   help="SAMPLE mode: N random base characters, OR an explicit comma-list of cell "
                        "slugs (may include costumes, e.g. mario__touring). Omit for the full sweep.")
    p.add_argument("--sample-karts", default="3", metavar="M|slug,...",
                   help="SAMPLE mode: M random karts, OR an explicit comma-list of kart slugs (default 3).")
    p.add_argument("--sample-seed", type=int, default=0, metavar="S",
                   help="SAMPLE mode: RNG seed for the reproducible draw (default 0).")
    a = p.parse_args()

    yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "clip_sweep.yaml")
    g = load_grid(yaml_path)

    stop_check = (lambda: bool(a.stop_file and os.path.exists(a.stop_file)))

    sampled_chars = sampled_karts = None
    if a.sample_chars is not None:
        sampled_chars, sampled_karts = sample_grid(g, a.sample_chars, a.sample_karts, a.sample_seed)

    print(f"Agent:       {a.agent_host}:{a.agent_port}")
    print(f"Capture WS:  {a.capture_ws}")
    print(f"Start from:  {a.start_from or '(beginning)'}")
    print(f"Stop file:   {a.stop_file or '(none)'}")
    print(f"Mode:        {'DRY RUN' if a.dry_run else 'LIVE'}")
    if sampled_chars is not None:
        print(f"Sample:      {len(sampled_chars)} chars × {len(sampled_karts)} karts (seed {a.sample_seed})")
        print(f"  chars: {', '.join(sampled_chars)}")
        print(f"  karts: {', '.join(sampled_karts)}")
    print()

    if a.dry_run:
        ctrl, client = _DryController(), _DryClient()
        runner = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0, stop_check=stop_check)
    else:
        ctrl = BridgeController(host=a.agent_host, port=a.agent_port)
        client = WsClient(a.capture_ws)
        runner = SweepRunner(g, ctrl, client, stop_check=stop_check)

    try:
        if not a.dry_run:
            print("Waiting for the controller agent to connect to the Switch...", flush=True)
            if hasattr(ctrl, "wait_ready") and not ctrl.wait_ready():
                print("Controller never became ready — is start_agent.py running and the Switch on? Aborting.")
                return

        char_slugs = [c.slug for c in g.cells("characters")]
        if sampled_chars is not None:
            targets = [s for s in char_slugs if s in sampled_chars]   # the picks, in grid order
        else:
            targets = list(char_slugs)
        if a.start_from:
            if a.start_from in char_slugs:
                keep = set(char_slugs[char_slugs.index(a.start_from):])
                targets = [s for s in targets if s in keep]
            else:
                print(f"  --start-from {a.start_from!r}: not a character slug; starting from the top.")

        paused = False
        recorded = 0
        for slug in targets:
            if runner._stop_requested():
                paused = True
                break                                  # at CHARACTER_SELECT (anchor) already
            print(f"\n-- char: {slug} --")
            runner._park_on_char(slug)                 # closed-loop char+costume nav (overshoot-safe)
            runner.capture_char(slug)
            if runner.sweep_karts(slug, karts=sampled_karts):
                paused = True
                break
            recorded += 1
        if paused:
            print("\nPaused (stop-file present).")
        elif sampled_chars is not None:
            print(f"\nSample complete ({recorded} chars).")
        else:
            print("\nSweep complete.")
    finally:
        ctrl.stop()
        client.close()


if __name__ == "__main__":
    main()
