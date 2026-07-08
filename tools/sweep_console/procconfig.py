"""Resolve the asset-processing paths from the environment (pure; unit-tested).

Single-machine default (no env) reproduces today's layout under KARTOFF_DATA_ROOT. The second
box sets KARTOFF_CLIPS_DIR (share), KARTOFF_PROCESS_OUT (local scratch), KARTOFF_CLAIMS_DIR
(shared coordinator), KARTOFF_SHIP_DIR (share) to run the same console pointed at the rig.
"""
import os
from collections import namedtuple

ProcessConfig = namedtuple("ProcessConfig", "clips out claims ship stop manifest")


def resolve_process_config(env, data_root_default=r"D:\kartoff"):
    data_root = env.get("KARTOFF_DATA_ROOT", data_root_default)
    clips = env.get("KARTOFF_CLIPS_DIR",
                    os.path.join(data_root, "captures_sdr", "en_uk", "clips"))
    out = env.get("KARTOFF_PROCESS_OUT", os.path.join(data_root, "asset_chips"))
    claims = env.get("KARTOFF_CLAIMS_DIR") or None
    ship = env.get("KARTOFF_SHIP_DIR") or None
    return ProcessConfig(
        clips=clips,
        out=out,
        claims=claims,
        ship=ship,
        stop=os.path.join(out, ".process_stop"),
        manifest=os.path.join(out, "manifest.json"),
    )
