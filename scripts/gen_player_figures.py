"""Extract per-player figure frames from source gifs into bundled PNGs.

Online frame = a late ('end', ~88%) frame of the player's online gif.
Offline frame = an early ('start', first frame >10% opaque) frame of the offline gif.
Both: alpha preserved, cropped to the figure's bounding box, resized to <=260px tall.
Greyscale for offline is applied in CSS at render time, not baked in here.

Run: python scripts/gen_player_figures.py
"""
import os
from PIL import Image

SRC = os.path.join(os.path.dirname(__file__), "..", "assets", "player_gifs")
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "assets", "players")

# player (lowercased) -> (online_gif, offline_gif). Alex has no art -> borrows Adymer's.
MAP = {
    "paul":   ("paulPosted.gif",   "paulPosted.gif"),
    "aliias": ("aliiasPosted.gif", "aliiasBird.gif"),
    "luke":   ("lukePosted.gif",   "lukeThumbsUp.gif"),
    "adymer": ("adymerPosted.gif", "adymerPosted.gif"),
    "alex":   ("adymerPosted.gif", "adymerPosted.gif"),
}

def frame(path, end, h=260):
    im = Image.open(path); n = getattr(im, "n_frames", 1)
    if end:
        idx = int(n * 0.88)
    else:
        idx = 0
        for i in range(n):
            im.seek(i)
            if im.convert("RGBA").getchannel("A").histogram()[255] / (im.width * im.height) > 0.10:
                idx = i; break
    im.seek(idx); fr = im.convert("RGBA")
    bb = fr.getchannel("A").getbbox()
    if bb: fr = fr.crop(bb)
    if fr.height > h: fr = fr.resize((round(fr.width * h / fr.height), h), Image.LANCZOS)
    return fr

def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (on_gif, off_gif) in MAP.items():
        for suffix, gif, end in (("on", on_gif, True), ("off", off_gif, False)):
            out = os.path.join(OUT, f"{name}__{suffix}.png")
            frame(os.path.join(SRC, gif), end).save(out, "PNG", optimize=True)
            print("wrote", os.path.relpath(out))

if __name__ == "__main__":
    main()
