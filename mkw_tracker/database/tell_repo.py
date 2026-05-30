"""Persistence + legacy migration for boolean-tree tells (config table)."""
from .config_repo import get_config, set_config, delete_configs_like, get_all_config

_LEGACY_KEYS = ("tell_roi_", "tell_thresh_", "tell_req_also_",
                "tell_alt_", "tell_and_thresh_", "tell_alt_thresh_")


def _region(kind, roi, image_path=None, thresh=170, icon_roi=None):
    return {"kind": kind, "roi": list(roi), "image_path": image_path,
            "thresh": int(thresh), "grayscale": True, "search_pad": 6,
            "icon_roi": list(icon_roi) if icon_roi else None}


def tree_from_legacy(screen_name: str) -> list | None:
    """Build a groups tree from legacy tell_* keys, or None if none are present."""
    roi   = get_config(f"tell_roi_{screen_name}")
    th    = get_config(f"tell_thresh_{screen_name}")
    alt   = get_config(f"tell_alt_{screen_name}")          # [path,[roi]] | False | None
    altth = get_config(f"tell_alt_thresh_{screen_name}")
    req   = get_config(f"tell_req_also_{screen_name}")      # [[path,[roi]], ...]
    reqth = get_config(f"tell_and_thresh_{screen_name}")    # [int, ...]
    if roi is None and alt is None and req is None and th is None:
        return None
    if roi is None:
        return None    # can't rebuild without a primary roi; leave for defaults
    primary_group = [_region("template", roi, thresh=th if th is not None else 170)]
    if isinstance(alt, list) and len(alt) >= 2 and alt[0]:
        primary_group.append(_region("template", alt[1], image_path=alt[0],
                                      thresh=altth if altth is not None else 170))
    groups = [primary_group]
    if isinstance(req, list):
        for i, item in enumerate(req):
            if isinstance(item, list) and len(item) >= 2 and len(item[1]) >= 4:
                t = reqth[i] if isinstance(reqth, list) and i < len(reqth) else 170
                groups.append([_region("template", item[1], image_path=item[0], thresh=t)])
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
