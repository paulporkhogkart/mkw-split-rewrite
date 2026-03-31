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


def emit_ready(version: str) -> str:
    return _emit("ready", version=version)


def emit_screen_change(from_screen: str, to_screen: str) -> str:
    return _emit("screen_change", **{"from": from_screen, "to": to_screen})


def emit_selection_update(character: Optional[str], costume: Optional[str],
                          kart: Optional[str], course: Optional[str]) -> str:
    return _emit("selection_update",
                 character=character, costume=costume, kart=kart, course=course)


def emit_lap_update(current: Optional[int], total: Optional[int],
                    split: Optional[str] = None) -> str:
    return _emit("lap_update", current=current, total=total, split=split)


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


def emit_devices_list(devices: List[str], configured: str, active: str) -> str:
    return _emit("devices_list", devices=devices, configured=configured, active=active)


def emit_error(message: str) -> str:
    return _emit("error", message=message)
