"""Extract per-player figure frames from source gifs into bundled PNGs.

Three states per player -> src/assets/players/<name>__{on,off,onpace}.png:
  online  ("on")     - shown on the live card
  offline ("off")    - shown dimmed when the player is offline (greyscale in CSS)
  onpace  ("onpace") - shown while the card is "on fire" (PB pace); optional

Frames are chosen one of two ways:
  * assets/player_figures.json (a manifest written by scripts/pick_player_figures.py)
    pins an explicit (gif, frame) per state. Preferred when present.
  * otherwise the legacy heuristic: online = a late (~88%) frame, offline = the
    first frame >10% opaque, of the gif named in MAP. (onpace has no heuristic -
    no manifest entry means no __onpace.png, and the card falls back to online.)

Both: alpha preserved, cropped to the figure bbox, resized to <=260px tall.

Run: python scripts/gen_player_figures.py
"""
import os
import json
from PIL import Image

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "assets", "player_gifs")
OUT = os.path.join(ROOT, "src", "assets", "players")
MANIFEST = os.path.join(ROOT, "assets", "player_figures.json")

# state -> output filename suffix
SUFFIX = {"online": "on", "offline": "off", "onpace": "onpace"}

# player (lowercased) -> (online_gif, offline_gif) heuristic fallback.
# Alex has no art of his own -> borrows Adymer's.
MAP = {
    "paul":   ("paulPosted.gif",   "paulPosted.gif"),
    "aliias": ("aliiasPosted.gif", "aliiasBird.gif"),
    "luke":   ("lukePosted.gif",   "lukeThumbsUp.gif"),
    "adymer": ("adymerPosted.gif", "adymerPosted.gif"),
    "alex":   ("adymerPosted.gif", "adymerPosted.gif"),
}


def n_frames(path):
    return getattr(Image.open(path), "n_frames", 1)


def heuristic_index(path, end):
    """Legacy frame pick: late frame for online (end=True), first opaque for offline."""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    if end:
        return int(n * 0.88)
    for i in range(n):
        im.seek(i)
        if im.convert("RGBA").getchannel("A").histogram()[255] / (im.width * im.height) > 0.10:
            return i
    return 0


def extract(path, idx, h=260):
    """One gif frame -> RGBA image, cropped to the alpha bbox, <=h tall."""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    im.seek(max(0, min(int(idx), n - 1)))
    fr = im.convert("RGBA")
    bb = fr.getchannel("A").getbbox()
    if bb:
        fr = fr.crop(bb)
    if fr.height > h:
        fr = fr.resize((round(fr.width * h / fr.height), h), Image.LANCZOS)
    return fr


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def resolve(name, manifest):
    """-> { suffix: (gif, frame) } for the states this player has (manifest wins)."""
    sel = manifest.get(name, {})
    on_gif, off_gif = MAP[name]
    plan = {}
    plan["on"] = tuple(sel["online"]) if "online" in sel \
        else (on_gif, heuristic_index(os.path.join(SRC, on_gif), True))
    plan["off"] = tuple(sel["offline"]) if "offline" in sel \
        else (off_gif, heuristic_index(os.path.join(SRC, off_gif), False))
    if "onpace" in sel:
        plan["onpace"] = tuple(sel["onpace"])
    return plan


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = load_manifest()
    for name in MAP:
        plan = resolve(name, manifest)
        for suffix, (gif, idx) in plan.items():
            src = os.path.join(SRC, gif)
            if not os.path.exists(src):
                print("SKIP", f"{name}__{suffix}", "- missing gif", gif)
                continue
            out = os.path.join(OUT, f"{name}__{suffix}.png")
            extract(src, idx).save(out, "PNG", optimize=True)
            print("wrote", os.path.relpath(out, ROOT), f"({gif} #{idx})")
        # No on-pace pick -> remove any stale __onpace.png so the card falls back to online.
        if "onpace" not in plan:
            stale = os.path.join(OUT, f"{name}__onpace.png")
            if os.path.exists(stale):
                os.remove(stale)
                print("removed stale", os.path.relpath(stale, ROOT))


if __name__ == "__main__":
    main()
