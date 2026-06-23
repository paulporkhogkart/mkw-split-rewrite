from mkw_tracker.database.config_repo import set_config, get_config
from mkw_tracker.database.tell_repo import migrate_tells_to_tree


def test_legacy_or_screen_migrates_to_one_group(memdb):
    # HOME-style: primary + alt overlay onto HOME's default ONE OR group. HOME's
    # default also carries white-theme variants for both prompt ROIs, so the migrated
    # group has 4 OR regions (home, home2, home-white, home2-white).
    set_config("tell_roi_HOME", [1110, 805, 1312, 877])
    set_config("tell_thresh_HOME", 55)
    set_config("tell_alt_HOME", ["images/screens/home2.png", [1361, 803, 1548, 875]])
    set_config("tell_alt_thresh_HOME", 55)
    migrate_tells_to_tree()
    tree = get_config("tell_tree_HOME")
    assert len(tree) == 1 and len(tree[0]) == 4
    assert get_config("tell_roi_HOME") is None          # legacy keys removed
    assert get_config("tell_alt_HOME") is None


def test_legacy_and_screen_migrates_to_two_groups(memdb):
    set_config("tell_roi_RACING", [78, 987, 96, 1015])
    set_config("tell_thresh_RACING", 173)
    set_config("tell_req_also_RACING", [["images/screens/racing-flag.png", [245, 991, 269, 1011]]])
    set_config("tell_and_thresh_RACING", [170])
    migrate_tells_to_tree()
    tree = get_config("tell_tree_RACING")
    assert len(tree) == 2 and len(tree[0]) == 1 and len(tree[1]) == 1


def test_screen_without_overrides_is_untouched(memdb):
    migrate_tells_to_tree()
    assert get_config("tell_tree_TITLE") is None         # no legacy keys → no blob


def test_migrated_primary_keeps_default_image_path(memdb):
    set_config("tell_roi_HOME", [1, 2, 3, 4])
    set_config("tell_thresh_HOME", 60)
    migrate_tells_to_tree()
    tree = get_config("tell_tree_HOME")
    assert tree[0][0]["image_path"] == "images/screens/home.png"   # preserved from default
    assert tree[0][0]["roi"] == [1, 2, 3, 4]                        # overridden
    assert tree[0][0]["thresh"] == 60


def test_reset_roi_override_stays_dark_loading(memdb):
    set_config("tell_roi_RESET", [0, 500, 500, 1080])
    migrate_tells_to_tree()
    tree = get_config("tell_tree_RESET")
    assert tree[0][0]["kind"] == "dark_loading"                     # NOT converted to template
    assert tree[0][0]["roi"] == [0, 500, 500, 1080]
    assert tree[0][0]["icon_roi"] == [1700, 920, 1870, 1030]        # preserved from default
