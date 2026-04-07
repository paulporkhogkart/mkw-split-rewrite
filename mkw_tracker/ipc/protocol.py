"""Inbound/outbound IPC message types for the Tauri ↔ Python sidecar."""
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ── Inbound commands (Tauri → Python) ────────────────────────────────────────

@dataclass
class UpdateConfigCmd:
    key:   str
    value: Any

@dataclass
class ForceScreenCmd:
    screen: str

@dataclass
class ToggleDebugCmd:
    enabled: bool

@dataclass
class ExportPbCmd:
    course: str

@dataclass
class SetSeedCmd:
    course: str
    cx:     int
    cy:     int
    radius: int = 0

@dataclass
class SetRoiCmd:
    course: str
    x: int
    y: int
    w: int
    h: int


def parse_inbound(raw: str) -> Optional[dict]:
    """Parse a raw JSON line from stdin. Returns None on error."""
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return None


# ── Outbound events (Python → Tauri) ─────────────────────────────────────────

def _emit(type_: str, **payload) -> str:
    """Serialise an outbound event to a JSON line (no trailing newline)."""
    return json.dumps({"type": type_, **payload})


def emit_ready(version: str, setup_complete: bool = True,
               app_language: str = "en_uk",
               switch2_language: str = "en_uk") -> str:
    return _emit("ready", version=version, setup_complete=setup_complete,
                 app_language=app_language, switch2_language=switch2_language)


def emit_camera_status(ok: bool, error: str = "",
                       width: int = 0, height: int = 0,
                       device: str = "") -> str:
    return _emit("camera_status", ok=ok, error=error,
                 width=width, height=height, device=device)


def emit_screen_change(from_screen: str, to_screen: str) -> str:
    return _emit("screen_change", **{"from": from_screen, "to": to_screen})


def emit_selection_update(character: Optional[str], costume: Optional[str],
                          kart: Optional[str], course: Optional[str],
                          char_conf: float = 0.0, costume_conf: float = 0.0,
                          kart_conf: float = 0.0, course_conf: float = 0.0) -> str:
    return _emit("selection_update",
                 character=character, costume=costume, kart=kart, course=course,
                 char_conf=round(char_conf, 3), costume_conf=round(costume_conf, 3),
                 kart_conf=round(kart_conf, 3), course_conf=round(course_conf, 3))


def emit_lap_update(current: Optional[int], total: Optional[int],
                    split: Optional[str] = None) -> str:
    return _emit("lap_update", current=current, total=total, split=split)


def emit_split_recorded(lap: int, time: str, is_final: bool = False) -> str:
    return _emit("split_recorded", lap=lap, time=time, is_final=is_final)


def emit_coin_update(coins: Optional[int]) -> str:
    return _emit("coin_update", coins=coins)


def emit_mush_update(count: int) -> str:
    return _emit("mush_update", count=count)


def emit_finish(result: Optional[str], total_time: Optional[str],
                splits: Optional[Dict[int, str]] = None) -> str:
    return _emit("finish", result=result, total_time=total_time,
                 splits=splits or {})


def emit_pb_achieved(course: str, time: str) -> str:
    return _emit("pb_achieved", course=course, time=time)


def emit_pb_export(course: str, mkwreplay: dict) -> str:
    return _emit("pb_export", course=course, mkwreplay=mkwreplay)


def emit_state(state_dict: dict) -> str:
    return _emit("state", **state_dict)


def emit_devices_list(devices: List[str], configured: str, active: str, audio_label: str = "") -> str:
    return _emit("devices_list", devices=devices, configured=configured, active=active, audio_label=audio_label)


def emit_error(message: str) -> str:
    return _emit("error", message=message)


def emit_heartbeat(fps: float, screen: str, tracking: bool,
                   current_score: float = 0.0,
                   candidate_scores: Optional[Dict[str, float]] = None) -> str:
    return _emit("heartbeat", fps=round(fps, 1), screen=screen, tracking=tracking,
                 current_score=round(current_score, 4),
                 candidate_scores={k: round(v, 4) for k, v in (candidate_scores or {}).items()})


def emit_frame_data(data: str, width: int, height: int, label: str = "") -> str:
    return _emit("frame_data", data=data, width=width, height=height, label=label)


def emit_template_score(screen: str, score: float, threshold: float,
                        matched: bool, roi: list,
                        template_img: Optional[str] = None,
                        live_crop: Optional[str] = None,
                        roi_key: str = "primary", **_) -> str:
    data = dict(screen=screen, roi_key=roi_key, score=round(score, 4),
                threshold=threshold, matched=matched, roi=roi)
    if template_img is not None:
        data["template_img"] = template_img
    if live_crop is not None:
        data["live_crop"] = live_crop
    return _emit("template_score", **data)


def emit_template_images(screen: str, template_img: Optional[str],
                         live_crop: Optional[str] = None,
                         roi_key: str = "primary", **_) -> str:
    return _emit("template_images", screen=screen, roi_key=roi_key,
                 template_img=template_img, live_crop=live_crop)


def emit_template_saved(screen: str, score: float, threshold: float,
                        matched: bool, **_) -> str:
    return _emit("template_saved", screen=screen, score=round(score, 4),
                 threshold=threshold, matched=matched)


def emit_tells_list(tells: list) -> str:
    return _emit("tells_list", tells=tells)


def emit_rois_list(rois: dict) -> str:
    return _emit("rois_list", rois=rois)


def emit_camera_paused() -> str:
    return _emit("camera_paused")


def emit_camera_resumed() -> str:
    return _emit("camera_resumed")


def emit_roi_preview(data: Optional[str]) -> str:
    return _emit("roi_preview", data=data)


def emit_asset_preview(category: str, item_name: str,
                       template_img: Optional[str],
                       live_crop: Optional[str]) -> str:
    return _emit("asset_preview", category=category, item_name=item_name,
                 template_img=template_img, live_crop=live_crop)


def emit_asset_saved(category: str, item_name: str) -> str:
    return _emit("asset_saved", category=category, item_name=item_name)
