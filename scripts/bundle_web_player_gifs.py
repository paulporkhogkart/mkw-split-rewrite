"""Bundle per-player popup GIFs into web/public/players/ from assets/player_figures.json.
  <player>.gif       = the 'online' (posted) source gif, trimmed to its picked frame.
  <player>__fire.gif = the 'onpace' source gif, trimmed to its picked frame.

Each gif is trimmed to END on the manifest's picked frame and every retained frame is
cropped to the UNION of the retained frames' alpha bboxes (so no lead-in frame clips, even
where the figure is larger earlier than at the final pose), then written as a PLAY-ONCE gif
(no NETSCAPE loop block), so the browser animates the lead-in once and rests on the framed
pose. CoursePopup re-arms playback per open. (The card portraits crop to the single picked
frame's bbox; the popup uses the union so a moving lead-in is never clipped.)

Cropping forces a re-encode (unlike the old byte-level loop strip), so palettes are
re-quantized; one shared 255-colour palette across the kept frames keeps colours stable
(no inter-frame flicker) with index 255 reserved for transparency.

Run:  python scripts/bundle_web_player_gifs.py
"""
import os, json
from PIL import Image

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "assets", "player_gifs")
OUT = os.path.join(ROOT, "web", "public", "players")
MANIFEST = os.path.join(ROOT, "assets", "player_figures.json")

TRANSPARENT = 255   # palette index reserved for transparency; frames quantize into 0..254
NETSCAPE = b"\x21\xFF\x0BNETSCAPE2.0"   # app-ext: 0x21 0xFF, len 11, "NETSCAPE2.0"


def _union_bbox(frames):
    """Union of every frame's alpha bbox (skipping fully-transparent frames), or None."""
    box = None
    for f in frames:
        bb = f.getchannel("A").getbbox()
        if bb is None:
            continue
        box = bb if box is None else (min(box[0], bb[0]), min(box[1], bb[1]),
                                      max(box[2], bb[2]), max(box[3], bb[3]))
    return box


def trim_and_crop(src_path, frame_index):
    """Frames 0..frame_index of a gif, each composited then cropped to the UNION of every
    retained frame's alpha bbox - so no frame in the lead-in clips (the figure can be larger
    earlier than at the final pose). -> (frames: list[RGBA], durations: list[int], bbox). The
    index is clamped into range. The end pose then sits inside the union window with margin,
    rather than filling it as an end-frame crop would."""
    im = Image.open(src_path)
    n = getattr(im, "n_frames", 1)
    end = max(0, min(int(frame_index), n - 1))
    raw, durations = [], []
    for i in range(end + 1):
        im.seek(i)
        raw.append(im.convert("RGBA"))
        durations.append(im.info.get("duration", 100))
    bbox = _union_bbox(raw) or (0, 0, im.width, im.height)
    frames = [f.crop(bbox) for f in raw]
    return frames, durations, bbox


def _to_paletted(frames):
    """RGBA frames -> P-mode frames sharing one 255-colour palette (built from every kept
    frame, so colours stay stable frame-to-frame) with index 255 painted in wherever a
    frame is transparent. GIF alpha is 1-bit, so the cut is thresholded at 128."""
    w = frames[0].width
    montage = Image.new("RGB", (w, sum(f.height for f in frames)))
    y = 0
    for f in frames:
        montage.paste(f.convert("RGB"), (0, y)); y += f.height
    pal = montage.quantize(colors=TRANSPARENT, method=Image.MEDIANCUT)   # palette at indices 0..254
    out = []
    for f in frames:
        p = f.convert("RGB").quantize(palette=pal, dither=Image.NONE)
        clear = f.getchannel("A").point(lambda a: 255 if a < 128 else 0).convert("1")
        p.paste(TRANSPARENT, mask=clear)
        out.append(p)
    return out


def write_play_once(frames, durations, out_path):
    """Encode RGBA frames as a transparent gif that plays once and rests on the last frame:
    disposal=2 so each transparent frame clears the one before it, and the NETSCAPE loop
    block is stripped after save so the browser plays it once. (Pillow propagates the source
    gif's loop=0 through the frame info, so it must be removed at the byte level - same
    mechanism the old bundler used.)"""
    ps = _to_paletted(frames)
    ps[0].save(out_path, save_all=True, append_images=ps[1:], duration=durations,
               disposal=2, transparency=TRANSPARENT, optimize=False)
    data = open(out_path, "rb").read()
    i = data.find(NETSCAPE)
    if i >= 0:                       # marker(14) + 0x03 0x01 + loop(2) + 0x00 = 19 bytes
        open(out_path, "wb").write(data[:i] + data[i + 19:])


def bundle_one(src_path, out_path, frame_index):
    frames, durations, _ = trim_and_crop(src_path, frame_index)
    write_play_once(frames, durations, out_path)


def main():
    os.makedirs(OUT, exist_ok=True)
    man = json.load(open(MANIFEST, encoding="utf-8"))
    for name, st in man.items():
        online_gif, online_idx = st["online"]
        onpace_gif, onpace_idx = st.get("onpace", st["online"])
        bundle_one(os.path.join(SRC, online_gif), os.path.join(OUT, f"{name}.gif"), online_idx)
        bundle_one(os.path.join(SRC, onpace_gif), os.path.join(OUT, f"{name}__fire.gif"), onpace_idx)
        print(f"bundled {name}: {online_gif}#{online_idx} -> {name}.gif, "
              f"{onpace_gif}#{onpace_idx} -> {name}__fire.gif (play-once, cropped)")


if __name__ == "__main__":
    main()
