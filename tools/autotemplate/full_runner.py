"""
full_runner.py — executes full_capture.yaml against a Switch via nxbt.

Capture is delegated to the mkw_tracker app (which already owns the capture card).
The runner connects to the tracker's WebSocket broadcaster and sends autotemplate
commands; the tracker crops, processes, and saves the files on Windows.

Runs in WSL2 with sudo (nxbt requires Bluetooth root access).

Usage:
    sudo /home/paul/autotemplate-venv/bin/python3 full_runner.py \\
        scripts/full_capture.yaml \\
        --mac E0:EF:BF:03:74:19 \\
        --capture-ws ws://localhost:8765 \\
        [--adapter hci0] \\
        [--start-lang en_us]   # skip earlier languages (resume support)
        [--dry-run]            # print steps, no controller / no capture

Output layout (written by the tracker, under its repo root):
    images/screens/{lang}/{name}.png
    images/characters/{lang}/{name}.png
    images/costumes/{lang}/{name}.png
    images/karts/{lang}/{name}.png
    images/courses/{lang}/{name}.png
    screenshots/{lang}/{name}.png
"""

import argparse
import asyncio
import json
import os
import queue
import re
import sys
import threading
import time

import yaml

sys.path.insert(0, os.path.dirname(__file__))

from switch_bridge import (
    ControllerState, sender_thread, replay as _replay_recording,
    BIT_TO_SWITCH, SEND_HZ,
)

# ── Button name → bit ─────────────────────────────────────────────────────────

_NAME_TO_BIT: dict[str, int] = {v: k for k, v in BIT_TO_SWITCH.items()}


# ── Capture client ────────────────────────────────────────────────────────────

class CaptureClient:
    """
    Persistent WebSocket connection to the mkw_tracker broadcaster.
    Sends autotemplate commands and collects at_done / at_error responses.
    """

    def __init__(self, ws_url: str):
        self._url      = ws_url
        self._loop     = asyncio.new_event_loop()
        self._ws       = None
        self._resp_q   = queue.Queue()
        self._ready    = threading.Event()
        self._err:     str | None = None
        self._thread   = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError(f"Timeout connecting to tracker at {ws_url}")
        if self._err:
            raise RuntimeError(self._err)

    def _run(self):
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
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, dict) and msg.get("type", "").startswith("at_"):
                        self._resp_q.put(msg)
        except Exception as exc:
            self._err = str(exc)
            self._ready.set()

    def send(self, cmd: dict, timeout: float = 15.0) -> dict:
        """Send a capture command and block until at_done / at_error is received."""
        asyncio.run_coroutine_threadsafe(
            self._ws.send(json.dumps(cmd)), self._loop
        )
        try:
            return self._resp_q.get(timeout=timeout)
        except queue.Empty:
            return {"type": "at_error", "message": f"Timeout waiting for response to {cmd.get('type')}"}

    def close(self):
        self._loop.call_soon_threadsafe(self._loop.stop)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_filename(name: str) -> str:
    """'Baby Mario' → 'baby_mario',  'R.O.B. H.O.G.' → 'rob_hog'"""
    slug = name.lower()
    slug = re.sub(r"[^\w\s'-]", "", slug)   # keep word chars, spaces, hyphens, apostrophes
    slug = re.sub(r"[']+", "", slug)         # drop apostrophes
    slug = re.sub(r"[\s-]+", "_", slug.strip())
    return slug


# ── Controller helpers ────────────────────────────────────────────────────────

def _press(state: ControllerState, button: str, duration: float = 0.1,
           after: float = 0.05, dry_run: bool = False):
    btn = button.upper()
    bit = _NAME_TO_BIT.get(btn)
    if bit is None:
        print(f"  [WARN] Unknown button: {button!r}")
        return
    hold_frames    = max(1, round(duration * SEND_HZ))
    release_frames = max(1, round(after    * SEND_HZ))
    print(f"    press {btn} ({hold_frames} frames hold, {release_frames} frames release)")
    if dry_run:
        return
    # Wait for exactly hold_frames sender cycles with button held, then
    # exactly release_frames cycles neutral — immune to sleep imprecision.
    state.replay_update(bit, 0, 0, 0, 0)
    target = state.frames_sent + hold_frames
    while state.frames_sent < target:
        time.sleep(1.0 / SEND_HZ)
    state.replay_update(0, 0, 0, 0, 0)
    target = state.frames_sent + release_frames
    while state.frames_sent < target:
        time.sleep(1.0 / SEND_HZ)


def _hold(state: ControllerState, button: str, duration: float,
          dry_run: bool = False):
    btn = button.upper()
    bit = _NAME_TO_BIT.get(btn)
    if bit is None:
        print(f"  [WARN] Unknown button: {button!r}")
        return
    print(f"    hold {btn} ({duration}s)")
    if dry_run:
        return
    state.replay_update(bit, 0, 0, 0, 0)
    time.sleep(duration)
    state.replay_update(0, 0, 0, 0, 0)
    time.sleep(0.05)


# ── Main runner class ─────────────────────────────────────────────────────────

class FullRunner:
    def __init__(self, script: dict, capture_ws: str, mac_addr: str, adapter: str,
                 recordings_dir: str, dry_run: bool, start_lang: str | None,
                 output_path: str | None = None,
                 force: bool = False):
        self.script        = script
        self.capture_ws    = capture_ws
        self.mac_addr      = mac_addr
        self.adapter       = adapter
        self.recordings_dir = recordings_dir
        self.dry_run       = dry_run
        self.start_lang    = start_lang
        self.output_path   = output_path
        self.force         = force

        # Runtime state
        self.lang:           str      = ""
        self.seen_chars:     set[str] = set()
        self.seen_costumes:  set[str] = set()
        self._skip_chars:    bool     = False
        self._skip_karts:    bool     = False
        self._last_replay:   str      = ""

        # Selection tracking — previous char/kart filename for nav-retry checks
        self._last_char_name:    str | None = None
        self._last_char_costume: str | None = None
        self._last_kart_name:    str | None = None

        # Pre-collect all unique names from the flow for completion checks
        self._all_chars, self._all_costumes, self._all_karts = self._collect_names()

        # Hardware (None in dry-run)
        self.ctrl         = None
        self.state        = None
        self.stop         = None
        self.capture:     CaptureClient | None = None

    # ── Name collection + completion checks ───────────────────────────────────

    def _collect_names(self) -> tuple[set[str], set[str], set[str]]:
        """Scan the flow and all macros to collect unique char/costume/kart filenames."""
        chars: set[str] = set()
        costumes: set[str] = set()
        karts: set[str] = set()
        macros = self.script.get("macros", {})

        def scan(steps):
            for step in (steps or []):
                if not isinstance(step, dict):
                    continue
                if "macro" in step and len(step) == 1:
                    scan(macros.get(step["macro"], []))
                    continue
                if "char" in step:
                    chars.add(_to_filename(str(step["char"])))
                if "costume" in step:
                    costumes.add(_to_filename(str(step["costume"])))
                if "kart" in step:
                    karts.add(_to_filename(str(step["kart"])))

        scan(self.script.get("flow", []))
        return chars, costumes, karts

    def _existing_names(self, *path_parts: str) -> set[str]:
        """Return the set of stem filenames in output_path/path_parts/, or empty set."""
        if not self.output_path:
            return set()
        d = os.path.join(self.output_path, *path_parts)
        if not os.path.isdir(d):
            return set()
        return {os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".png")}

    def _chars_complete(self) -> bool:
        if self.force or not self.output_path:
            return False
        existing_chars = self._existing_names("images", "characters", self.lang)
        existing_costumes = self._existing_names("images", "costumes", self.lang)
        return self._all_chars <= existing_chars and self._all_costumes <= existing_costumes

    def _karts_complete(self) -> bool:
        if self.force or not self.output_path:
            return False
        existing = self._existing_names("images", "karts", self.lang)
        return self._all_karts <= existing

    def _course_already_captured(self, display_name: str) -> bool:
        if self.force or not self.output_path:
            return False
        filename = _to_filename(display_name)
        return os.path.exists(
            os.path.join(self.output_path, "images", "courses",
                         self.lang, f"{filename}.png"))

    def _exit_course_select(self, macros: dict) -> None:
        """Press B (longer) until COURSE_SELECT is no longer detected, then reset cursor."""
        attempt = 0
        while True:
            attempt += 1
            _press(self.state, "B", duration=0.3, after=0.3, dry_run=self.dry_run)
            self._do_wait(2.0)
            if self.dry_run:
                break
            resp = self.capture.send(
                {"type": "at_check_tell_score", "screen": "course_select", "lang": self.lang},
                timeout=10.0,
            )
            score = resp.get("score", 0.0) if resp.get("type") == "at_tell_score" else 0.0
            print(f"    course_select exit score: {score:.4f}")
            if score < 0.600:
                break
            print(f"  [retry #{attempt}] COURSE_SELECT still showing after B — pressing again…")
        self._execute_steps(macros.get("reset_cursor", []), macros)

    def _check_course_select(self, threshold: float = 0.900) -> bool:
        """Ask the tracker to score the live frame against the saved course_select tell template."""
        if self.dry_run:
            return True
        resp = self.capture.send(
            {"type": "at_check_tell_score", "screen": "course_select", "lang": self.lang},
            timeout=10.0,
        )
        if resp.get("type") == "at_tell_score":
            score = resp.get("score", 0.0)
            print(f"    course_select score: {score:.4f}")
            return score >= threshold
        print(f"  [WARN] at_check_tell_score error: {resp.get('message', resp)}")
        return False

    # ── Hardware setup ─────────────────────────────────────────────────────────

    def _ctrl_connect_with_retry(self) -> None:
        """
        Call self.ctrl.connect() in a thread; if it hasn't succeeded in 5 s,
        abandon it, create a fresh ProController, and try again — infinitely.
        """
        from controller import ProController
        attempt = 0
        while True:
            attempt += 1
            print(f"  Connecting to Switch ({self.mac_addr}) — attempt {attempt}…")
            success = threading.Event()
            error   = [None]

            def _try(ctrl=self.ctrl):
                try:
                    ctrl.connect(reconnect_addr=self.mac_addr)
                    success.set()
                except Exception as exc:
                    error[0] = exc
                    success.set()   # unblock the wait so we can check error[0]

            threading.Thread(target=_try, daemon=True).start()
            if success.wait(timeout=10.0) and error[0] is None:
                print("  Switch connected.")
                return

            reason = f"error: {error[0]}" if error[0] else "timed out after 10s"
            print(f"  Connection {reason} — retrying with fresh controller…")
            try:
                self.ctrl.disconnect()
            except Exception:
                pass
            self.ctrl = ProController(adapter=self.adapter)

    def _setup_hardware(self):
        from controller import ProController
        self.ctrl  = ProController(adapter=self.adapter)
        self.state = ControllerState()
        self.stop  = threading.Event()
        self._ctrl_connect_with_retry()
        threading.Thread(target=sender_thread,
                         args=(self.ctrl, self.state, self.stop),
                         daemon=True).start()
        print(f"Connecting to tracker at {self.capture_ws}…")
        self.capture = CaptureClient(self.capture_ws)
        print("Tracker connected.")

    def _teardown_hardware(self):
        if self.stop:
            self.stop.set()
        if self.ctrl:
            self.ctrl.disconnect()
        if self.capture:
            self.capture.close()

    # ── Capture helpers ────────────────────────────────────────────────────────

    def _send_capture(self, cmd: dict) -> bool:
        """Send a capture command to the tracker and return True on success."""
        resp = self.capture.send(cmd)
        if resp.get("type") == "at_done":
            for p in resp.get("paths", []):
                print(f"    Saved: {p}")
            if "roi" in resp:
                print(f"      roi={resp['roi']}  source={resp.get('roi_source','?')}")
            return True
        else:
            print(f"  [WARN] Capture failed: {resp.get('message', resp)}")
            return False

    def _capture_screenshot(self, name: str):
        print(f"    screenshot → screenshots/{self.lang}/{name}.png")
        if not self.dry_run:
            self._send_capture({"type": "at_capture_screenshot",
                                "name": name, "lang": self.lang})

    def _capture_screen_tell(self, screen: str):
        print(f"    tell {screen} → screens/{self.lang}/{screen}.png + screenshot")
        if not self.dry_run:
            self._send_capture({"type": "at_capture_screen_tell",
                                "screen": screen, "lang": self.lang})

    def _capture_screen_roi(self, screen: str):
        print(f"    roi {screen} → screens/{self.lang}/")
        if not self.dry_run:
            self._send_capture({"type": "at_capture_screen_roi",
                                "screen": screen, "lang": self.lang})

    def _capture_asset(self, category: str, display_name: str):
        filename = _to_filename(display_name)
        print(f"    capture {category[:-1]} {display_name!r} → {category}/{self.lang}/{filename}.png")
        if not self.dry_run:
            self._send_capture({"type": "at_capture_asset",
                                "category": category, "name": filename, "lang": self.lang})

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _do_press(self, button: str, duration: float = 0.1):
        _press(self.state, button, duration=duration, dry_run=self.dry_run)

    def _do_hold(self, button: str, duration: float):
        _hold(self.state, button, duration, dry_run=self.dry_run)

    def _do_wait(self, secs: float):
        print(f"    wait {secs}s")
        if not self.dry_run:
            time.sleep(secs)

    def _do_replay(self, filename: str):
        path = os.path.join(self.recordings_dir, filename)
        if not os.path.exists(path):
            print(f"  [WARN] Recording not found: {path!r} — skipping")
            return
        print(f"    replay {filename}")
        if not self.dry_run:
            _replay_recording(self.state, path)

    def _do_manual_scroll(self) -> None:
        """
        Ring the terminal bell, then let the operator steer the Switch with
        arrow keys until they press Enter.  After Enter: 3-second delay, then
        the runner resumes (caller proceeds with the next step).
        """
        import sys, tty, termios

        print("\a", end="", flush=True)   # terminal bell
        print("\n" + "=" * 60)
        print("  ACTION REQUIRED — manual scroll")
        print("  Arrow keys → DPAD on Switch.")
        print("  Press ENTER when done — script resumes after 3 s.")
        print("=" * 60)

        if self.dry_run:
            input("  [dry-run] Press Enter to simulate scroll done: ")
            return

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    break
                if ch == "\x03":   # Ctrl-C — bail out
                    raise KeyboardInterrupt
                if ch == "\x1b":
                    seq = sys.stdin.read(2)
                    btn = {"[A": "DPAD_UP", "[B": "DPAD_DOWN",
                           "[C": "DPAD_RIGHT", "[D": "DPAD_LEFT"}.get(seq)
                    if btn:
                        _press(self.state, btn, duration=0.1, after=0.15)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        print("\n  Resuming in 3 s…", flush=True)
        time.sleep(3.0)

    def _do_reconnect(self):
        if self.dry_run:
            print(f"    reconnect (MAC: {self.mac_addr})")
            return
        print(f"  Reconnecting to {self.mac_addr}…")
        # Stop the sender thread before touching the controller
        if self.stop:
            self.stop.set()
        try:
            self.ctrl.disconnect()
        except Exception:
            pass
        self._ctrl_connect_with_retry()
        # Restart sender thread with the (possibly new) ctrl instance
        self.stop = threading.Event()
        threading.Thread(target=sender_thread,
                         args=(self.ctrl, self.state, self.stop),
                         daemon=True).start()
        print("  Reconnected.")

    # ── Selection nav-retry helpers ────────────────────────────────────────────

    def _is_selection_unchanged(self, category: str,
                                 curr_name: str, curr_costume: str | None,
                                 prev_name: str, prev_costume: str | None) -> bool:
        """
        Ask the tracker to score the live frame against the PREVIOUS asset template.
        Returns True if the selection appears not to have moved yet.

        When the character name doesn't change (costume-only step), the costume
        template is used as the change indicator; otherwise the name template is used.
        """
        same_char = (curr_name == prev_name)
        cmd: dict = {
            "type":     "at_check_asset_match",
            "category": category,
            "lang":     self.lang,
            "name":     prev_name,
        }
        if same_char and prev_costume:
            cmd["costume"] = prev_costume

        resp = self.capture.send(cmd, timeout=10.0)
        if resp.get("type") != "at_asset_score":
            print(f"  [WARN] at_check_asset_match: {resp.get('message', resp)} — assuming changed")
            return False

        name_score    = resp.get("name_score", 0.0)
        costume_score = resp.get("costume_score")

        if same_char and costume_score is not None:
            still = costume_score >= 0.65
            print(f"    nav-check costume_score={costume_score:.3f} ({'SAME — retry' if still else 'changed — ok'})")
        else:
            still = name_score >= 0.85
            print(f"    nav-check name_score={name_score:.3f} ({'SAME — retry' if still else 'changed — ok'})")
        return still

    def _retry_if_nav_unchanged(self, category: str, btn: str, dur: float, wt: float,
                                 curr_name: str, curr_costume: str | None,
                                 prev_name: str, prev_costume: str | None) -> None:
        """
        After the initial press+wait has already run, check whether the cursor
        actually moved.  If the previous template still matches, re-press + re-wait
        and check again — retrying indefinitely until the cursor moves.

        An extra 0.5 s settle is inserted before the first check (the 2 s wait may
        end while the transition animation is still rendering) and again after
        confirming movement (so the arriving frame is stable before the caller
        captures it).
        """
        # Extra settle: give the transition animation time to fully land.
        print(f"    nav-settle 0.5s (post-wait animation buffer)")
        time.sleep(0.5)

        attempt = 0
        while True:
            if not self._is_selection_unchanged(
                    category, curr_name, curr_costume, prev_name, prev_costume):
                # Confirmed moved — let the arriving frame stabilise before capture.
                print(f"    nav-settle 0.5s (pre-capture animation buffer)")
                time.sleep(0.5)
                return
            attempt += 1
            print(f"  [nav-retry #{attempt}] cursor still on previous — re-pressing {btn}")
            _press(self.state, btn, duration=dur, dry_run=False)
            time.sleep(wt)

    # ── Step execution ─────────────────────────────────────────────────────────

    def _execute_step(self, step: dict, macros: dict):
        if not isinstance(step, dict):
            print(f"  [WARN] Malformed step: {step!r}")
            return

        # Macro expansion (sole key only — multi-key steps can't have macro)
        if "macro" in step and len(step) == 1:
            name = step["macro"]
            if name not in macros:
                print(f"  [WARN] Unknown macro: {name!r}")
                return
            for sub in macros[name]:
                self._execute_step(sub, macros)
            return

        # ── Navigation ─────────────────────────────────────────────────────────
        if "press" in step:
            dur = float(step["duration"]) if "duration" in step else 0.1
            self._do_press(str(step["press"]), duration=dur)

        if "hold" in step:
            self._do_hold(str(step["hold"]), float(step.get("duration", 1.0)))

        if "replay" in step:
            self._last_replay = str(step["replay"])
            self._do_wait(1.5)   # settle before replaying
            self._do_replay(self._last_replay)

        if "rstick_down" in step:
            dur = float(step["rstick_down"])
            print(f"    rstick_down ({dur}s)")
            if not self.dry_run:
                self.state.replay_update(0, 0, 0, 0, -127)
                time.sleep(dur)
                self.state.replay_update(0, 0, 0, 0, 0)
                time.sleep(0.05)

        if "reconnect" in step:
            self._do_reconnect()

        if "manual_scroll" in step:
            self._do_manual_scroll()

        if "exit_course_select" in step:
            self._exit_course_select(macros)

        # ── Timing ─────────────────────────────────────────────────────────────
        if "wait" in step:
            self._do_wait(float(step["wait"]))

        # ── Captures ───────────────────────────────────────────────────────────
        captured = False

        if "screenshot" in step:
            self._capture_screenshot(str(step["screenshot"]))
            self._do_wait(1.0)

        if "tell" in step:
            self._capture_screen_tell(str(step["tell"]))
            self._do_wait(1.0)

        if "roi" in step:
            self._capture_screen_roi(str(step["roi"]))
            self._do_wait(1.0)

        if "char" in step:
            char_name    = str(step["char"])
            char_fn      = _to_filename(char_name)
            costume_name = str(step["costume"]) if "costume" in step else None
            costume_fn   = _to_filename(costume_name) if costume_name else None

            # After initial press+wait, verify cursor actually moved; retry if not.
            if "press" in step and not self.dry_run and self._last_char_name is not None:
                self._retry_if_nav_unchanged(
                    "characters",
                    str(step["press"]).upper(),
                    float(step.get("duration", 0.1)),
                    float(step.get("wait", 2.0)),
                    char_fn, costume_fn,
                    self._last_char_name, self._last_char_costume,
                )

            if char_name not in self.seen_chars:
                self.seen_chars.add(char_name)
                self._capture_asset("characters", char_name)
                self._do_wait(1.0)
            elif self.dry_run:
                print(f"    char {char_name!r} (skip — already captured this pass)")

            self._last_char_name    = char_fn
            self._last_char_costume = costume_fn

        if "costume" in step:
            costume_name = str(step["costume"])
            if costume_name not in self.seen_costumes:
                self.seen_costumes.add(costume_name)
                self._capture_asset("costumes", costume_name)
                self._do_wait(1.0)
            elif self.dry_run:
                print(f"    costume {costume_name!r} (skip — already captured this pass)")

        if "kart" in step:
            kart_name = str(step["kart"])
            kart_fn   = _to_filename(kart_name)

            # After initial press+wait, verify cursor actually moved; retry if not.
            if "press" in step and not self.dry_run and self._last_kart_name is not None:
                self._retry_if_nav_unchanged(
                    "karts",
                    str(step["press"]).upper(),
                    float(step.get("duration", 0.1)),
                    float(step.get("wait", 2.0)),
                    kart_fn, None,
                    self._last_kart_name, None,
                )

            self._capture_asset("karts", kart_name)
            self._do_wait(1.0)
            self._last_kart_name = kart_fn

        if "course" in step:
            course_name = str(step["course"])
            # Retry loop: settle briefly, check tell score, retry if < 0.900.
            # On failure: reset map cursor (no X press) and re-replay the last recording.
            attempt = 0
            while True:
                self._do_wait(1.5)   # settle before checking
                if self._check_course_select():
                    break
                attempt += 1
                print(f"  [retry #{attempt}] COURSE_SELECT not confirmed — resetting and replaying {self._last_replay!r}")
                self._execute_steps(macros.get("reset_map_cursor", []), macros)
                self._do_wait(1.5)   # settle before replaying
                self._do_replay(self._last_replay)
                self._do_wait(2.0)
                self._do_press("A")
                self._do_wait(2.0)
            self._do_wait(1.0)   # extra settle after confirmation
            self._capture_asset("courses", course_name)
            self._do_wait(2.0)

    def _execute_steps(self, steps: list, macros: dict):
        steps = list(steps or [])
        i = 0
        while i < len(steps):
            step = steps[i]

            # ── Skip chars/costumes if all images already exist ────────────────
            if self._skip_chars:
                if isinstance(step, dict) and step.get("_marker") == "confirm_char":
                    self._skip_chars = False
                    self._execute_step(step, macros)
                elif self.dry_run:
                    print(f"    [skip-chars] {step}")
                i += 1
                continue

            # ── Skip karts if all images already exist ─────────────────────────
            if self._skip_karts:
                if isinstance(step, dict) and step.get("_marker") == "confirm_kart":
                    self._skip_karts = False
                    self._execute_step(step, macros)
                elif self.dry_run:
                    print(f"    [skip-karts] {step}")
                i += 1
                continue

            # ── Skip course block if already captured (except the last course) ──
            if isinstance(step, dict) and "replay" in step and i + 1 < len(steps):
                next_step = steps[i + 1]
                if isinstance(next_step, dict) and "course" in next_step:
                    course_name = str(next_step["course"])
                    is_last = not any(
                        isinstance(s, dict) and "course" in s
                        for s in steps[i + 2:]
                    )
                    if not is_last and self._course_already_captured(course_name):
                        # Scan forward to just past the exit_course_select that closes this block
                        j = i
                        while j < len(steps):
                            s = steps[j]
                            j += 1
                            if isinstance(s, dict) and "exit_course_select" in s:
                                break
                        print(f"  [skip] course {course_name!r} already captured for {self.lang!r}")
                        i = j
                        continue

            self._execute_step(step, macros)

            # After the character/kart tell, check if we can skip ahead
            if isinstance(step, dict):
                if step.get("tell") == "character_screen" and self._chars_complete():
                    self._skip_chars = True
                    print(f"  [skip] All chars/costumes already exist for {self.lang!r} — skipping to confirm")
                elif step.get("tell") == "kart_screen" and self._karts_complete():
                    self._skip_karts = True
                    print(f"  [skip] All karts already exist for {self.lang!r} — skipping to confirm")

            i += 1

    # ── Language pass ──────────────────────────────────────────────────────────

    def _run_language_pass(self, lang_id: str, lang_name: str):
        self.lang = lang_id
        self.seen_chars.clear()
        self.seen_costumes.clear()
        self._skip_chars     = False
        self._skip_karts     = False
        self._last_char_name    = None
        self._last_char_costume = None
        self._last_kart_name    = None

        print(f"\n{'='*60}")
        print(f"  Language: {lang_name} ({lang_id})")
        print(f"{'='*60}")

        macros = self.script.get("macros", {})
        flow   = self.script.get("flow", [])
        self._execute_steps(flow, macros)

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self):
        languages   = self.script.get("languages", [])
        change_lang = self.script.get("change_language", [])
        macros      = self.script.get("macros", {})

        if not languages:
            print("[ERROR] No languages defined in script.")
            return

        if not self.dry_run:
            self._setup_hardware()

        try:
            skipping = bool(self.start_lang)

            for i, lang in enumerate(languages):
                lang_id   = lang["id"]
                lang_name = lang.get("name", lang_id)

                if skipping:
                    if lang_id == self.start_lang:
                        skipping = False
                    else:
                        print(f"  (skipping language: {lang_id})")
                        continue

                # change_language: never before the first pass, never after the last
                if i > 0 and (not self.start_lang or lang_id != self.start_lang):
                    print(f"\n── Changing language → {lang_name} ──")
                    self._execute_steps(change_lang, macros)

                self._run_language_pass(lang_id, lang_name)

        finally:
            if not self.dry_run:
                self._teardown_hardware()

        print("\nAll languages complete.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="Execute full_capture.yaml to automate MKW template capture.")
    p.add_argument("script",
                   help="Path to YAML script (e.g. scripts/full_capture.yaml)")
    p.add_argument("--mac",        default="E0:EF:BF:03:74:19",
                   help="Switch Bluetooth MAC address")
    p.add_argument("--adapter",    default="hci0",
                   help="Bluetooth adapter (default: hci0)")
    p.add_argument("--capture-ws", default="ws://localhost:8765",
                   metavar="URL",
                   help="WebSocket URL of mkw_tracker broadcaster "
                        "(default: ws://localhost:8765). "
                        "Use 'ws://<windows-host-ip>:8765' from WSL2.")
    p.add_argument("--recordings", default=None,
                   help="Directory containing recording JSON files "
                        "(default: <script_dir>/../recordings)")
    p.add_argument("--start-lang", default=None, metavar="LANG_ID",
                   help="Skip languages before this ID (resume support)")
    p.add_argument("--force", action="store_true",
                   help="Disable all skip-if-exists logic — always capture chars, "
                        "costumes, karts, and courses even if the files already exist.")
    p.add_argument("--output-path", default=None, metavar="PATH",
                   help="Path to the repo root where images/ lives (e.g. /mnt/c/development/mkw-split-rewrite). "
                        "Used to detect already-captured chars/costumes/karts and skip hovering.")
    p.add_argument("--dry-run",    action="store_true",
                   help="Print all steps without opening controller or capture")
    return p.parse_args()


def main():
    args = _parse_args()

    with open(args.script) as f:
        script = yaml.safe_load(f)

    script_dir  = os.path.dirname(os.path.abspath(args.script))
    recordings  = args.recordings or os.path.normpath(
        os.path.join(script_dir, "..", "recordings"))

    print(f"Script:      {args.script}")
    print(f"Recordings:  {recordings}")
    print(f"Capture WS:  {args.capture_ws}")
    print(f"MAC:         {args.mac}")
    print(f"Output path: {args.output_path or '(not set — skip detection disabled)'}")
    print(f"Force:       {'yes — all skip logic disabled' if args.force else 'no'}")
    print(f"Mode:        {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    runner = FullRunner(
        script=script,
        capture_ws=args.capture_ws,
        mac_addr=args.mac,
        adapter=args.adapter,
        recordings_dir=recordings,
        dry_run=args.dry_run,
        start_lang=args.start_lang,
        output_path=args.output_path,
        force=args.force,
    )
    runner.run()


if __name__ == "__main__":
    main()
