import os

import procconfig


def test_defaults_match_single_machine_layout():
    cfg = procconfig.resolve_process_config({}, data_root_default=r"D:\kartoff")
    assert cfg.clips == os.path.join(r"D:\kartoff", "captures_sdr", "en_uk", "clips")
    assert cfg.out == os.path.join(r"D:\kartoff", "asset_chips")
    assert cfg.claims is None                              # single-machine: no claims
    assert cfg.ship is None
    assert cfg.stop == os.path.join(cfg.out, ".process_stop")
    assert cfg.manifest == os.path.join(cfg.out, "manifest.json")


def test_machine2_env_overrides():
    env = {
        "KARTOFF_CLIPS_DIR": r"\\RIG\kartoff\captures_sdr\en_uk\clips",
        "KARTOFF_PROCESS_OUT": r"C:\kartoff_scratch\asset_chips",
        "KARTOFF_CLAIMS_DIR": r"\\RIG\kartoff\asset_chips\claims",
        "KARTOFF_SHIP_DIR": r"\\RIG\kartoff\asset_chips",
    }
    cfg = procconfig.resolve_process_config(env)
    assert cfg.clips == r"\\RIG\kartoff\captures_sdr\en_uk\clips"
    assert cfg.out == r"C:\kartoff_scratch\asset_chips"
    assert cfg.claims == r"\\RIG\kartoff\asset_chips\claims"
    assert cfg.ship == r"\\RIG\kartoff\asset_chips"
    assert cfg.stop == os.path.join(cfg.out, ".process_stop")   # local to machine 2


def test_data_root_env_moves_defaults():
    cfg = procconfig.resolve_process_config({"KARTOFF_DATA_ROOT": r"E:\k"})
    assert cfg.out == os.path.join(r"E:\k", "asset_chips")
