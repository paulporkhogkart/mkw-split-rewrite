"""The URL->slug asset map must cover exactly the 30 known course slugs,
and slugify(display name) must equal the slug for every known course."""
import numpy as np

from scripts.fetch_discord_assets import COURSE_ASSETS, slugify, _to_512
from mkw_tracker.detection.selection import KNOWN_COURSES


def test_asset_map_covers_all_known_course_slugs():
    map_slugs = {slug for (_url, slug) in COURSE_ASSETS}
    known_slugs = {slugify(name) for name in KNOWN_COURSES}
    assert map_slugs == known_slugs
    assert len(COURSE_ASSETS) == 30


def test_slugify_matches_known_courses():
    cases = {
        "Wario's Galleon": "warios_galleon",
        "Great ? Block Ruins": "great_block_ruins",
        "Mario Bros. Circuit": "mario_bros_circuit",
        "Sky-High Sundae": "sky_high_sundae",
        "DK Pass": "dk_pass",
        "Rainbow Road": "rainbow_road",
    }
    for name, slug in cases.items():
        assert slugify(name) == slug


def test_to_512_normalizes_to_square_512_bgra():
    # Landscape 3-channel input -> 512x512x4 (Discord requires >=512x512).
    out = _to_512(np.zeros((100, 200, 3), np.uint8))
    assert out.shape == (512, 512, 4)
    # Already-square small input is upscaled, not left small.
    out2 = _to_512(np.zeros((64, 64, 4), np.uint8))
    assert out2.shape == (512, 512, 4)
