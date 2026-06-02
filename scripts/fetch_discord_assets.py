"""Download MKW course icons + penguin/splash, normalize each to a 512x512 PNG
(Discord Art Assets require >=512x512), named by Discord asset key (slug), into
out/ ready to drag into the Discord Developer Portal -> Art Assets.

Usage: python scripts/fetch_discord_assets.py [--out out_dir]
"""
import argparse, re, urllib.request
from pathlib import Path

import cv2
import numpy as np


def slugify(name: str) -> str:
    # Strip apostrophes first ("Wario's" -> "warios"), then turn remaining
    # non-alphanumeric runs into single underscores.
    s = name.lower().replace("'", "").replace("’", "")
    return re.sub(r"_+$", "", re.sub(r"^_+", "", re.sub(r"[^a-z0-9]+", "_", s)))


# (url, slug) — slugs match images/courses/*.png stems and slugify(display name).
COURSE_ASSETS = [
    ("https://mario.wiki.gallery/images/thumb/8/85/MKWd_Mario_Bros_Circuit_Icon.png/120px-MKWd_Mario_Bros_Circuit_Icon.png", "mario_bros_circuit"),
    ("https://mario.wiki.gallery/images/thumb/e/ef/MKWd_Crown_City_Icon.png/120px-MKWd_Crown_City_Icon.png", "crown_city"),
    ("https://mario.wiki.gallery/images/thumb/d/db/MKWd_Whistlestop_Summit_Icon.png/120px-MKWd_Whistlestop_Summit_Icon.png", "whistlestop_summit"),
    ("https://mario.wiki.gallery/images/thumb/8/89/MKWd_DK_Spaceport_Icon.png/120px-MKWd_DK_Spaceport_Icon.png", "dk_spaceport"),
    ("https://mario.wiki.gallery/images/thumb/6/6a/MKWd_Desert_Hills_Icon.png/120px-MKWd_Desert_Hills_Icon.png", "desert_hills"),
    ("https://mario.wiki.gallery/images/thumb/3/32/MKWd_Shy_Guy_Bazaar_Icon.png/120px-MKWd_Shy_Guy_Bazaar_Icon.png", "shy_guy_bazaar"),
    ("https://mario.wiki.gallery/images/thumb/3/34/MKWd_Wario_Stadium_Icon.png/120px-MKWd_Wario_Stadium_Icon.png", "wario_stadium"),
    ("https://mario.wiki.gallery/images/thumb/c/c4/MKWd_Airship_Fortress_Icon.png/120px-MKWd_Airship_Fortress_Icon.png", "airship_fortress"),
    ("https://mario.wiki.gallery/images/thumb/e/e1/MKWd_DK_Pass_Icon.png/120px-MKWd_DK_Pass_Icon.png", "dk_pass"),
    ("https://mario.wiki.gallery/images/thumb/c/c4/MKWd_Starview_Peak_Icon.png/120px-MKWd_Starview_Peak_Icon.png", "starview_peak"),
    ("https://mario.wiki.gallery/images/thumb/8/83/MKWd_Sky-High_Sundae_Icon.png/120px-MKWd_Sky-High_Sundae_Icon.png", "sky_high_sundae"),
    ("https://mario.wiki.gallery/images/thumb/b/b4/MKWd_Wario_Shipyard_Icon.png/120px-MKWd_Wario_Shipyard_Icon.png", "warios_galleon"),
    ("https://mario.wiki.gallery/images/thumb/c/c2/MKWd_Koopa_Troopa_Beach_Icon.png/120px-MKWd_Koopa_Troopa_Beach_Icon.png", "koopa_troopa_beach"),
    ("https://mario.wiki.gallery/images/thumb/5/5d/MKWd_Faraway_Oasis_Icon.png/120px-MKWd_Faraway_Oasis_Icon.png", "faraway_oasis"),
    ("https://mario.wiki.gallery/images/thumb/e/e3/Peach-Beach-MarioKartWorld.jpg/120px-Peach-Beach-MarioKartWorld.jpg", "peach_beach"),
    ("https://mario.wiki.gallery/images/thumb/d/d8/Salty_Salty_Speedway_Mario_Kart_World.jpg/120px-Salty_Salty_Speedway_Mario_Kart_World.jpg", "salty_salty_speedway"),
    ("https://mario.wiki.gallery/images/thumb/6/67/Dino_Dino_Jungle_Mario_Kart_World.png/120px-Dino_Dino_Jungle_Mario_Kart_World.png", "dino_dino_jungle"),
    ("https://mario.wiki.gallery/images/thumb/5/5e/MKWorld_Question_Ruins_icon.png/120px-MKWorld_Question_Ruins_icon.png", "great_block_ruins"),
    ("https://mario.wiki.gallery/images/thumb/0/06/MKWorld_Cheep_Cheep_Falls_icon.png/120px-MKWorld_Cheep_Cheep_Falls_icon.png", "cheep_cheep_falls"),
    ("https://mario.wiki.gallery/images/thumb/7/70/MKWorld_Dandelion_Depths_icon.png/120px-MKWorld_Dandelion_Depths_icon.png", "dandelion_depths"),
    ("https://mario.wiki.gallery/images/thumb/0/08/MKWorld_Boo_Cinema_icon.png/120px-MKWorld_Boo_Cinema_icon.png", "boo_cinema"),
    ("https://mario.wiki.gallery/images/thumb/0/01/MKWorld_Dry_Bones_Burnout_icon.png/120px-MKWorld_Dry_Bones_Burnout_icon.png", "dry_bones_burnout"),
    ("https://mario.wiki.gallery/images/thumb/4/42/MKWorld_Moo_Moo_Meadows_icon.png/120px-MKWorld_Moo_Moo_Meadows_icon.png", "moo_moo_meadows"),
    ("https://mario.wiki.gallery/images/thumb/b/b1/MKWorld_Choco_Mountain_icon.png/120px-MKWorld_Choco_Mountain_icon.png", "choco_mountain"),
    ("https://mario.wiki.gallery/images/thumb/1/1e/MKWorld_Toads_Factory_icon.png/120px-MKWorld_Toads_Factory_icon.png", "toads_factory"),
    ("https://mario.wiki.gallery/images/thumb/8/86/MKWorld_Bowsers_Castle_icon.png/120px-MKWorld_Bowsers_Castle_icon.png", "bowsers_castle"),
    ("https://mario.wiki.gallery/images/thumb/5/5a/MKWorld_Acorn_Heights_Icon.jpg/120px-MKWorld_Acorn_Heights_Icon.jpg", "acorn_heights"),
    ("https://mario.wiki.gallery/images/thumb/f/f0/MKWorld_Mario_Circuit_icon.png/120px-MKWorld_Mario_Circuit_icon.png", "mario_circuit"),
    ("https://mario.wiki.gallery/images/thumb/7/7e/MKWorld_Peach_Stadium_icon_2.png/120px-MKWorld_Peach_Stadium_icon_2.png", "peach_stadium"),
    ("https://mario.wiki.gallery/images/thumb/8/88/MKWorld_Rainbow_Road_icon.png/120px-MKWorld_Rainbow_Road_icon.png", "rainbow_road"),
]
SPLASH_URL = "https://image-assets.m.nintendo.com/985d81ae-7d37-4bd1-8732-f247f47f8821"
PENGUIN_SRC = Path(__file__).resolve().parent.parent / "src-tauri" / "icons" / "128x128@2x.png"

TARGET = 512  # Discord Art Assets require >= 512x512.


def _enlarge_url(url: str) -> str:
    """Bump a MediaWiki thumbnail URL to a high-res (1024px) source.
    MediaWiki clamps to the original size, so we never request an upscale."""
    return re.sub(r"/\d+px-", "/1024px-", url)


def _download_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _normalize(img: np.ndarray) -> np.ndarray:
    """Scale so the shorter side is TARGET px, preserving aspect ratio (rectangular -
    no cropping, no stretching). Discord requires both dimensions >= 512."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    h, w = img.shape[:2]
    scale = TARGET / min(h, w)
    nh, nw = max(TARGET, round(h * scale)), max(TARGET, round(w * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, (nw, nh), interpolation=interp)


def _fetch_512_png(url: str) -> bytes:
    """Download (high-res first, original as fallback), normalize to 512x512 PNG."""
    last_err = None
    for u in (_enlarge_url(url), url):
        try:
            raw = _download_bytes(u)
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            ok, buf = cv2.imencode(".png", _normalize(img))
            if ok:
                return buf.tobytes()
        except Exception as e:  # noqa: BLE001 - try the fallback URL
            last_err = e
    raise RuntimeError(f"failed to fetch/process {url}: {last_err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/discord-assets")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Clear stale outputs (e.g. from an older version of this script) so a re-run
    # never leaves undersized or duplicate-key files behind.
    for old in (*out.glob("*.png"), *out.glob("*.jpg"), *out.glob("*.jpeg")):
        old.unlink()

    for url, slug in COURSE_ASSETS:
        print(f"  {slug}")
        (out / f"{slug}.png").write_bytes(_fetch_512_png(url))

    (out / "splash.png").write_bytes(_fetch_512_png(SPLASH_URL))

    if PENGUIN_SRC.exists():
        peng = cv2.imread(str(PENGUIN_SRC), cv2.IMREAD_UNCHANGED)
        ok, buf = cv2.imencode(".png", _normalize(peng))
        if ok:
            (out / "penguin.png").write_bytes(buf.tobytes())

    print(f"Done -> {out} (all 512x512 PNGs; drag into the Discord portal Art Assets)")


if __name__ == "__main__":
    main()
