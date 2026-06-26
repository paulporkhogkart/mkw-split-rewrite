"""EventBroadcaster - WebSocket pub-sub server for external subscribers.

Runs inside IpcServer's asyncio loop. All events emitted via IpcServer.emit()
are broadcast to every connected client. Clients are read-only (subscribe-only);
any inbound data is silently ignored unless autotemplate mode is enabled.

Usage (via --ws-port flag):
    python -m mkw_tracker --ws-port 8765

External subscriber example:
    import asyncio, json, websockets

    async def main():
        async with websockets.connect("ws://localhost:8765") as ws:
            async for msg in ws:
                print(json.loads(msg))

    asyncio.run(main())

Autotemplate capture commands (sent TO the server, received on the same WS):
    {"type": "at_capture_asset",      "category": "characters", "name": "mario",  "lang": "en_uk"}
    {"type": "at_capture_screenshot",  "name": "title",   "lang": "en_uk"}
    {"type": "at_capture_screen_tell", "screen": "title", "lang": "en_uk"}
    {"type": "at_capture_screen_roi",  "screen": "mainmenu", "lang": "en_uk"}
    {"type": "at_check_tell_score",    "screen": "course_select", "lang": "en_uk"}
    {"type": "at_check_asset_match",   "category": "characters", "name": "mario", "lang": "en_uk"}
    {"type": "at_check_asset_match",   "category": "characters", "name": "mario", "lang": "en_uk", "costume": "touring"}

Responses (sent back to the requesting client only):
    {"type": "at_done",        "paths": [...], "roi": [...], "roi_source": "..."}
    {"type": "at_tell_score",  "screen": "...", "score": 0.0}
    {"type": "at_asset_score", "name_score": 0.0, "costume_score": 0.0}  # costume_score absent if not requested
    {"type": "at_error",       "message": "..."}

Screen ROIs come from the live detector (which has all DB overrides applied at startup).
Asset ROIs come from the settings object (which reads from the DB config table).
No hardcoded coordinate fallbacks are used.
"""
import asyncio
import json
import logging
import os
from typing import Optional, Set

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── YAML screen name → (Screen enum name, roi mode) ──────────────────────────
# mode "primary"      → first entry only (tell.roi)
# mode "alt"          → alt ROI only (tell.alt_roi)
# mode "primary_alt"  → primary + alt (if alt exists)
# mode "required_0"   → tell.required_also[0]

_YAML_SCREEN_MAP: dict[str, tuple[str, str]] = {
    "title":            ("TITLE",               "primary"),
    "home":             ("HOME",                "primary_alt"),
    "home2":            ("HOME",                "alt"),
    "starttimetrial":   ("START_TIME_TRIAL",    "primary"),
    "startreplay":      ("START_REPLAY",        "primary"),
    "reset":            ("RESET",               "primary"),
    "posttimetrial":    ("POST_TIME_TRIAL",     "primary_alt"),
    "mainmenu":         ("MAIN_MENU",           "primary"),
    "character_screen": ("CHARACTER_SELECT",    "primary"),
    "kart_screen":      ("KART_SELECT",         "primary"),
    "course_select":    ("COURSE_SELECT",       "primary_alt"),
    "racing-coin":      ("RACING",              "primary"),
    "racing-flag":      ("RACING",              "required_0"),
    "racemenu":         ("RACE_MENU",           "primary_alt"),
    "ghostmenu":        ("REPLAY_MENU",         "primary"),
    "ghostmenu-red":    ("REPLAY_RACE_AGAINST", "primary"),
    "gallery":          ("GALLERY",             "primary"),
    "singleplayer":     ("SINGLEPLAYER_MENU",   "primary"),
    "timetrials":       ("TIME_TRIALS",         "primary"),
}

_REF_W, _REF_H = 1920, 1080


def _basename(path: str) -> str:
    """'images/screens/title.png' → 'title'"""
    return os.path.splitext(os.path.basename(path))[0]


def _crop_and_process(frame: np.ndarray, roi: tuple, thresh,
                      grayscale: bool = False) -> np.ndarray:
    x1, y1, x2, y2 = [int(v) for v in roi]
    crop = frame[y1:y2, x1:x2]
    if grayscale:
        # Save the continuous-tone crop - grayscale tells match it directly.
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    if thresh is not None:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        _, out = cv2.threshold(gray, int(thresh), 255, cv2.THRESH_BINARY)
        return out
    return crop


def _save(img: np.ndarray, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)


class EventBroadcaster:
    """
    WebSocket broadcast server.  Must be attached to a running asyncio loop
    via attach() before any broadcast() calls are made.
    """

    def __init__(self, port: int):
        self._port = port
        self._clients: Set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Autotemplate capture state (set via enable_autotemplate())
        self._at_enabled:    bool           = False
        self._at_frame_ref:  Optional[list] = None   # [frame_or_None]
        self._at_settings                   = None
        self._at_detector                   = None
        self._at_base_path:  str            = ""
        self._at_tracker                    = None
        self._clip_mgr                      = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule the WS server startup inside an already-running asyncio loop."""
        self._loop = loop
        self._serve_task = loop.create_task(self._serve())

    def stop(self) -> None:
        """Cancel the server task so the socket is closed and the port released."""
        task = getattr(self, "_serve_task", None)
        if task is not None and not task.done() and self._loop is not None:
            self._loop.call_soon_threadsafe(task.cancel)

    def enable_autotemplate(self, current_frame: list, settings,
                            detector, base_path: str, tracker=None) -> None:
        """
        Enable inbound autotemplate capture commands.

        current_frame : the [frame_or_None] list updated each tick by main.py
        settings      : tracker Settings instance (for asset ROI lookups)
        detector      : ScreenDetector instance (for screen ROIs with DB overrides)
        base_path     : absolute path to the repo root (output files go here)
        """
        self._at_enabled   = True
        self._at_frame_ref = current_frame
        self._at_settings  = settings
        self._at_detector  = detector
        self._at_base_path = base_path
        self._at_tracker   = tracker

    def set_clip_manager(self, mgr) -> None:
        self._clip_mgr = mgr

    # ── Outbound broadcast ────────────────────────────────────────────────────

    def broadcast(self, line: str) -> None:
        """Thread-safe: fan a JSON line out to all connected WS clients."""
        if self._loop is None or not self._clients:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(line), self._loop)

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _serve(self) -> None:
        try:
            import websockets  # type: ignore
        except ImportError:
            logger.error(
                "websockets package not installed - event broadcaster disabled. "
                "Run: pip install websockets"
            )
            return

        try:
            async with websockets.serve(self._handler, "0.0.0.0", self._port) as server:
                logger.info("Event broadcaster listening on ws://localhost:%d", self._port)
                try:
                    await asyncio.Future()  # run until cancelled
                except asyncio.CancelledError:
                    pass
                finally:
                    server.close()
                    await server.wait_closed()
                    logger.info("Event broadcaster closed (port %d released)", self._port)
        except OSError as exc:
            logger.error("Event broadcaster failed to bind on port %d: %s", self._port, exc)
        except asyncio.CancelledError:
            pass

    async def _handler(self, websocket) -> None:
        self._clients.add(websocket)
        addr = websocket.remote_address
        logger.info("Subscriber connected: %s (total: %d)", addr, len(self._clients))
        try:
            async for raw in websocket:
                if not self._at_enabled:
                    continue
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(msg, dict) and msg.get("type", "").startswith("at_"):
                    response = await asyncio.get_event_loop().run_in_executor(
                        None, self._handle_at_command, msg
                    )
                    try:
                        await websocket.send(json.dumps(response))
                    except Exception:
                        pass
        finally:
            self._clients.discard(websocket)
            logger.info("Subscriber disconnected: %s (total: %d)", addr, len(self._clients))

    async def _broadcast(self, line: str) -> None:
        if not self._clients:
            return
        for ws in set(self._clients):
            try:
                await ws.send(line)
            except Exception:
                pass

    # ── Autotemplate capture (runs in executor - blocking I/O OK) ─────────────

    def _handle_at_command(self, msg: dict) -> dict:
        t = msg.get("type", "")
        if t == "at_capture_asset":
            return self._at_asset(
                msg.get("category", ""), msg.get("name", ""), msg.get("lang", ""))
        if t == "at_capture_screenshot":
            return self._at_screenshot(msg.get("name", ""), msg.get("lang", ""))
        if t == "at_capture_screen_tell":
            return self._at_screen_tell(msg.get("screen", ""), msg.get("lang", ""))
        if t == "at_capture_screen_roi":
            return self._at_screen_roi(msg.get("screen", ""), msg.get("lang", ""))
        if t == "at_check_tell_score":
            return self._at_check_tell_score(msg.get("screen", ""), msg.get("lang", ""))
        if t == "at_check_asset_match":
            return self._at_check_asset_match(
                msg.get("category", ""), msg.get("lang", ""),
                msg.get("name", ""), msg.get("costume"))
        if t == "at_record_clip_begin":
            if self._clip_mgr is None:
                return {"type": "at_error", "message": "clip manager not set"}
            self._clip_mgr.begin(msg.get("item", ""))
            return {"type": "clip_begun"}
        if t == "at_record_clip_mark":
            if self._clip_mgr is None:
                return {"type": "at_error", "message": "clip manager not set"}
            self._clip_mgr.mark(msg.get("event", ""))
            return {"type": "marked"}
        if t == "at_record_clip_abort":
            if self._clip_mgr is None:
                return {"type": "at_error", "message": "clip manager not set"}
            self._clip_mgr.abort()
            return {"type": "clip_aborted"}
        if t == "at_clip_exists":
            return {"type": "exists_result",
                    "done": bool(self._clip_mgr and self._clip_mgr.exists(msg.get("item", "")))}
        if t == "at_current_selection":
            # Ground off the LIVE SelectionTracker (the detection the overlay shows),
            # not a parallel re-match.  Prefer the committed state; fall back to the top
            # score-map candidate (the exact list the overlay/readout displays).
            tr = self._at_tracker
            st = getattr(tr, "state", None)
            maps = getattr(tr, "score_maps", None) or {}

            def _best(cat, floor):
                cands = maps.get(cat) or []
                if cands and cands[0].get("score", 0.0) >= floor:
                    return cands[0].get("name")
                return None

            return {"type": "current_selection",
                    "character": getattr(st, "character", None) or _best("char", 0.55),
                    "costume":   getattr(st, "costume", None)   or _best("costume", 0.30),
                    "kart":      getattr(st, "kart", None)      or _best("kart", 0.60),
                    "course":    getattr(st, "course", None)    or _best("course", 0.60)}
        if t == "at_current_screen":
            # The live detector's screen — reliable for "are we on KART_SELECT / CHARACTER_SELECT"
            # (committed kart/character persist across screens, so the selection can't tell them apart).
            det = self._at_detector
            scr = getattr(getattr(det, "current_screen", None), "name", "") if det is not None else ""
            return {"type": "current_screen", "screen": scr}
        return {"type": "at_error", "message": f"Unknown autotemplate command: {t!r}"}

    def _current_frame(self) -> Optional[np.ndarray]:
        if self._at_frame_ref is None:
            return None
        return self._at_frame_ref[0]

    def _out(self, *parts: str) -> str:
        return os.path.join(self._at_base_path, *parts)

    # ── Screen ROI lookup (from live detector, DB overrides already applied) ──

    def _resolve_tell(self, yaml_name: str):
        """Return (tell, mode) for the given YAML screen name, or (None, None)."""
        mapping = _YAML_SCREEN_MAP.get(yaml_name)
        if mapping is None or self._at_detector is None:
            return None, None
        screen_enum_name, mode = mapping
        from ..detection.screen import Screen
        try:
            screen_enum = Screen[screen_enum_name]
        except KeyError:
            return None, None
        tell = self._at_detector._tells_by_screen.get(screen_enum)
        return tell, mode

    def _primary_entry(self, tell) -> list:
        """Primary ROI only."""
        return [(tell.roi, tell.binary_thresh, _basename(tell.image_path))]

    def _all_entries(self, tell) -> list:
        """Primary + alt (if configured) + all required_also (if configured)."""
        entries = [(tell.roi, tell.binary_thresh, _basename(tell.image_path))]
        if tell.alt_roi and tell.alt_image_path:
            thresh = tell.alt_binary_thresh if tell.alt_binary_thresh is not None \
                     else tell.binary_thresh
            entries.append((tell.alt_roi, thresh, _basename(tell.alt_image_path)))
        for i, (path, roi) in enumerate(tell.required_also or []):
            thresh = (tell.required_also_thresh[i]
                      if tell.required_also_thresh and i < len(tell.required_also_thresh)
                      else tell.binary_thresh)
            entries.append((roi, thresh, _basename(path)))
        return entries

    def _screen_roi_entries(self, yaml_name: str, all_rois: bool = False) -> list:
        """
        Return a list of (roi_tuple, binary_thresh, output_filename) for the
        given YAML screen name, read from the live ScreenDetector.

        all_rois=False (used by tell): primary entry only.
        all_rois=True  (used by roi):  all entries - primary + alt + required_also.

        Special sub-entry modes ("alt", "required_0") always return their one entry
        regardless of all_rois.
        """
        tell, mode = self._resolve_tell(yaml_name)
        if tell is None:
            return []

        if mode == "alt":
            if tell.alt_roi and tell.alt_image_path:
                thresh = tell.alt_binary_thresh if tell.alt_binary_thresh is not None \
                         else tell.binary_thresh
                return [(tell.alt_roi, thresh, _basename(tell.alt_image_path))]
            return []

        if mode == "required_0":
            if tell.required_also:
                path, roi = tell.required_also[0]
                thresh = (tell.required_also_thresh[0]
                          if tell.required_also_thresh else tell.binary_thresh)
                return [(roi, thresh, _basename(path))]
            return []

        # "primary" or "primary_alt"
        if all_rois:
            return self._all_entries(tell)
        return self._primary_entry(tell)

    # ── Capture methods ───────────────────────────────────────────────────────

    def _at_asset(self, category: str, name: str, lang: str) -> dict:
        if not category or not name or not lang:
            return {"type": "at_error", "message": "at_capture_asset: missing field"}

        frame = self._current_frame()
        if frame is None:
            return {"type": "at_error", "message": "No frame available"}

        if self._at_settings is None:
            return {"type": "at_error", "message": "Settings not available"}

        _roi_map = {
            "characters": self._at_settings.get("char_name_roi"),
            "karts":      self._at_settings.get("kart_name_roi"),
            "courses":    self._at_settings.get("course_name_roi"),
            "costumes":   self._at_settings.get("costume_roi"),
        }
        roi = _roi_map.get(category)
        if not roi:
            return {"type": "at_error",
                    "message": f"No ROI configured for category {category!r} - "
                               f"complete first-time setup first"}

        x1, y1, x2, y2 = [int(v) for v in roi]
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return {"type": "at_error", "message": "Empty crop"}

        if category == "costumes":
            img = crop
        else:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            _, img = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)

        path = self._out("images", category, lang, f"{name}.png")
        _save(img, path)
        logger.debug("at_capture_asset: %s roi=%s → %s", category, list(roi), path)
        return {"type": "at_done", "paths": [path], "roi": list(roi)}

    def _at_screenshot(self, name: str, lang: str) -> dict:
        if not name or not lang:
            return {"type": "at_error", "message": "at_capture_screenshot: missing field"}
        frame = self._current_frame()
        if frame is None:
            return {"type": "at_error", "message": "No frame available"}
        path = self._out("screenshots", lang, f"{name}.png")
        _save(frame, path)
        return {"type": "at_done", "paths": [path]}

    def _at_screen_tell(self, screen: str, lang: str) -> dict:
        """Primary ROI crop + full screenshot, from DB-overridden detector."""
        if not screen or not lang:
            return {"type": "at_error", "message": "at_capture_screen_tell: missing field"}
        entries = self._screen_roi_entries(screen, all_rois=True)
        if not entries:
            return {"type": "at_error", "message": f"Unknown or unconfigured screen: {screen!r}"}

        frame = self._current_frame()
        if frame is None:
            return {"type": "at_error", "message": "No frame available"}

        _tell, _ = self._resolve_tell(screen)
        gray = bool(getattr(_tell, "grayscale", False))
        paths = []
        for roi, thresh, filename in entries:
            img  = _crop_and_process(frame, roi, thresh, gray)
            path = self._out("images", "screens", lang, f"{filename}.png")
            _save(img, path)
            paths.append(path)
            logger.debug("at_capture_screen_tell: %s roi=%s → %s", screen, list(roi), path)

        shot_path = self._out("screenshots", lang, f"{screen}.png")
        _save(frame, shot_path)
        paths.append(shot_path)
        return {"type": "at_done", "paths": paths}

    def _at_screen_roi(self, screen: str, lang: str) -> dict:
        """All ROIs for this screen (primary + alt + required_also), from DB-overridden detector."""
        if not screen or not lang:
            return {"type": "at_error", "message": "at_capture_screen_roi: missing field"}
        entries = self._screen_roi_entries(screen, all_rois=True)
        if not entries:
            return {"type": "at_error", "message": f"Unknown or unconfigured screen: {screen!r}"}

        frame = self._current_frame()
        if frame is None:
            return {"type": "at_error", "message": "No frame available"}

        _tell, _ = self._resolve_tell(screen)
        gray = bool(getattr(_tell, "grayscale", False))
        paths = []
        for roi, thresh, filename in entries:
            img  = _crop_and_process(frame, roi, thresh, gray)
            path = self._out("images", "screens", lang, f"{filename}.png")
            _save(img, path)
            paths.append(path)
            logger.debug("at_capture_screen_roi: %s roi=%s → %s", screen, list(roi), path)
        return {"type": "at_done", "paths": paths}

    def _at_check_tell_score(self, screen: str, lang: str) -> dict:
        """
        Match the live frame against the freshly saved per-lang tell template for this screen.
        Uses the primary ROI + threshold from the DB-overridden detector.
        Returns {"type": "at_tell_score", "screen": ..., "score": float}.
        """
        if not screen or not lang:
            return {"type": "at_error", "message": "at_check_tell_score: missing field"}

        frame = self._current_frame()
        if frame is None:
            return {"type": "at_error", "message": "No frame available"}

        tell, _ = self._resolve_tell(screen)
        if tell is None:
            return {"type": "at_error", "message": f"Unknown or unconfigured screen: {screen!r}"}

        # Load the template that was just captured for this language (not the startup default)
        filename = _basename(tell.image_path)
        template_path = self._out("images", "screens", lang, f"{filename}.png")
        if not os.path.exists(template_path):
            return {"type": "at_error",
                    "message": f"Template not found - capture tell first: {template_path}"}

        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            return {"type": "at_error", "message": f"Failed to load template: {template_path}"}

        from ..detection.screen import _match_tell
        score = _match_tell(frame, tell.roi, template, tell.binary_thresh,
                            getattr(tell, "grayscale", False), getattr(tell, "search_pad", 0))
        logger.debug("at_check_tell_score: %s lang=%s score=%.4f", screen, lang, score)
        return {"type": "at_tell_score", "screen": screen, "score": score}

    def _at_check_asset_match(self, category: str, lang: str,
                               name: str, costume: "str | None") -> dict:
        """
        Score the live frame against a previously-saved asset template.
        Used by the autotemplate runner to verify that a DPAD press actually
        moved the selection cursor before it proceeds to capture.

        For names (characters/karts): Canny-edge both the live crop and the
        saved BGR template via prepare_text_edges (same method as the live
        SelectionTracker — background-agnostic, no binary threshold).
        For costumes: Canny-edge both the live crop and the saved BGR template,
        then TM_CCOEFF_NORMED.

        Returns {"type": "at_asset_score", "name_score": float}
        with an additional "costume_score" key only when costume was requested.
        """
        if not category or not lang or not name:
            return {"type": "at_error", "message": "at_check_asset_match: missing field"}
        if category not in ("characters", "karts"):
            return {"type": "at_error",
                    "message": f"at_check_asset_match: unsupported category {category!r}"}

        frame = self._current_frame()
        if frame is None:
            return {"type": "at_error", "message": "No frame available"}

        if self._at_settings is None:
            return {"type": "at_error", "message": "Settings not available"}

        roi = self._at_settings.get(
            "char_name_roi" if category == "characters" else "kart_name_roi")
        if not roi:
            return {"type": "at_error",
                    "message": f"No ROI configured for {category!r} - complete setup first"}

        # ── Name score (Canny edges — mirrors live SelectionTracker) ────────────
        tmpl_path = self._out("images", category, lang, f"{name}.png")
        if not os.path.exists(tmpl_path):
            return {"type": "at_error",
                    "message": f"Template not found (capture it first): {tmpl_path}"}

        from ..detection.templates import prepare_text_edges
        tmpl_bgr = cv2.imread(tmpl_path, cv2.IMREAD_COLOR)
        if tmpl_bgr is None:
            return {"type": "at_error", "message": f"Failed to load template: {tmpl_path}"}
        tmpl = prepare_text_edges(tmpl_bgr)

        # Pad the live crop so matchTemplate can SLIDE the template to its best alignment
        # (mirrors the live SelectionTracker's SELECTION_SEARCH_PAD). Without this, the
        # exact-ROI match scores low on any minor shift and grounding fails on the right item.
        _PAD = 8   # = detection.selection.SELECTION_SEARCH_PAD
        x1, y1, x2, y2 = [int(v) for v in roi]
        fh, fw = frame.shape[:2]
        crop = frame[max(0, y1 - _PAD):min(fh, y2 + _PAD), max(0, x1 - _PAD):min(fw, x2 + _PAD)]
        live = prepare_text_edges(crop)

        if tmpl.shape[0] > live.shape[0] or tmpl.shape[1] > live.shape[1]:
            name_score = 0.0
        else:
            result = cv2.matchTemplate(live, tmpl, cv2.TM_CCOEFF_NORMED)
            name_score = float(cv2.minMaxLoc(result)[1])

        response: dict = {"type": "at_asset_score", "name_score": name_score}

        # ── Optional costume score ───────────────────────────────────────────────
        if costume:
            costume_roi = self._at_settings.get("costume_roi")
            ctmpl_path  = self._out("images", "costumes", lang, f"{costume}.png")
            if costume_roi and os.path.exists(ctmpl_path):
                ctmpl_bgr = cv2.imread(ctmpl_path, cv2.IMREAD_COLOR)
                if ctmpl_bgr is not None:
                    from ..detection.templates import prepare_text_edges
                    ctmpl = prepare_text_edges(ctmpl_bgr)
                    cx1, cy1, cx2, cy2 = [int(v) for v in costume_roi]
                    fh, fw = frame.shape[:2]
                    ccrop = frame[max(0, cy1 - 8):min(fh, cy2 + 8), max(0, cx1 - 8):min(fw, cx2 + 8)]
                    clive = prepare_text_edges(ccrop)
                    if ctmpl.shape[0] <= clive.shape[0] and ctmpl.shape[1] <= clive.shape[1]:
                        result = cv2.matchTemplate(clive, ctmpl, cv2.TM_CCOEFF_NORMED)
                        response["costume_score"] = float(cv2.minMaxLoc(result)[1])

        logger.debug("at_check_asset_match: %s/%s/%s name=%.3f costume=%s",
                     category, lang, name, name_score, response.get("costume_score"))
        return response
