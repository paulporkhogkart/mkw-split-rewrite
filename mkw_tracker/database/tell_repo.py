"""Persistence + legacy migration for boolean-tree tells (config table)."""
from .config_repo import get_config, set_config, delete_configs_like, get_all_config

_LEGACY_KEYS = ("tell_roi_", "tell_thresh_", "tell_req_also_",
                "tell_alt_", "tell_and_thresh_", "tell_alt_thresh_")


def _region(kind, roi, image_path=None, thresh=170, icon_roi=None):
    return {"kind": kind, "roi": list(roi), "image_path": image_path,
            "thresh": int(thresh), "grayscale": True, "search_pad": 6,
            "icon_roi": list(icon_roi) if icon_roi else None}


def _region_dict_from_default(r) -> dict:
    return {"kind": r.kind, "roi": list(r.roi), "image_path": r.image_path,
            "thresh": int(getattr(r, "thresh", 170)), "grayscale": bool(r.grayscale),
            "search_pad": int(r.search_pad),
            "icon_roi": list(r.icon_roi) if r.icon_roi else None}


def _default_groups(screen_name: str) -> list | None:
    """Deep dict-copy of the hardcoded default tree for a screen, or None."""
    from ..detection.screen import TELLS, Screen
    try:
        screen = Screen[screen_name]
    except KeyError:
        return None
    for t in TELLS:
        if t.screen == screen:
            return [[_region_dict_from_default(r) for r in g] for g in t.groups]
    return None


def tree_from_legacy(screen_name: str) -> list | None:
    """Overlay legacy tell_* overrides onto the screen's default tree.
    Returns the groups tree, or None if no legacy keys / no such default screen."""
    roi   = get_config(f"tell_roi_{screen_name}")
    th    = get_config(f"tell_thresh_{screen_name}")
    alt   = get_config(f"tell_alt_{screen_name}")          # [path,[roi]] | False | None
    altth = get_config(f"tell_alt_thresh_{screen_name}")
    req   = get_config(f"tell_req_also_{screen_name}")      # [[path,[roi]], ...]
    reqth = get_config(f"tell_and_thresh_{screen_name}")    # [int, ...]
    if roi is None and alt is None and req is None and th is None:
        return None
    groups = _default_groups(screen_name)
    if not groups or not groups[0]:
        return None
    # primary region (group 0, region 0): override roi/thresh, KEEP kind/path/icon_roi
    if isinstance(roi, list) and len(roi) >= 4:
        groups[0][0]["roi"] = [int(v) for v in roi]
    if th is not None and groups[0][0]["kind"] == "template":
        groups[0][0]["thresh"] = int(th)
    # alt → OR region in group 0
    if isinstance(alt, list) and len(alt) >= 2 and alt[0]:
        alt_region = _region("template", alt[1], image_path=alt[0],
                             thresh=altth if altth is not None else 170)
        if len(groups[0]) >= 2:
            groups[0][1] = alt_region
        else:
            groups[0].append(alt_region)
    elif alt is False:
        groups[0] = [groups[0][0]]          # alt explicitly removed
    # required_also → AND groups (group index 1+i)
    if isinstance(req, list):
        for i, item in enumerate(req):
            if isinstance(item, list) and len(item) >= 2 and len(item[1]) >= 4:
                t = reqth[i] if isinstance(reqth, list) and i < len(reqth) else 170
                reg = _region("template", item[1], image_path=item[0], thresh=t)
                gi = 1 + i
                if gi < len(groups):
                    groups[gi] = [reg]
                else:
                    groups.append([reg])
    return groups


def migrate_tells_to_tree() -> int:
    """One-time: convert any legacy tell_* overrides to tell_tree_<SCREEN> blobs.
    Returns the number of screens migrated."""
    all_cfg = get_all_config()
    screens = set()
    for key in all_cfg:
        for pfx in _LEGACY_KEYS:
            if key.startswith(pfx):
                screens.add(key[len(pfx):])
    migrated = 0
    for sn in screens:
        if get_config(f"tell_tree_{sn}") is not None:
            continue
        tree = tree_from_legacy(sn)
        if tree:
            set_config(f"tell_tree_{sn}", tree)
            migrated += 1
    for pfx in _LEGACY_KEYS:
        delete_configs_like(f"{pfx}%")
    return migrated
