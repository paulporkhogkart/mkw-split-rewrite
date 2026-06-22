"""The popup-gif bundler trims each source gif to its picked frame and crops every
retained frame to that end-frame's alpha bbox (the same framing the card PNGs use),
then writes a play-once gif. See scripts/bundle_web_player_gifs.py."""
import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "public" / "players"
MANIFEST = ROOT / "assets" / "player_figures.json"


def _mod():
    spec = importlib.util.spec_from_file_location("bundle_web", ROOT / "scripts" / "bundle_web_player_gifs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _make_source_gif(path, rects):
    """A LOOPING transparent gif on a 20x20 canvas (loop=0, like the real source gifs, so
    the play-once assertion is meaningful); frame i is fully transparent but for an opaque
    square at rects[i] (so frame i's alpha bbox == that square)."""
    frames = []
    for r in rects:
        im = Image.new("P", (20, 20), 1)        # index 1 = transparent background
        im.putpalette([220, 40, 40] + [0, 0, 0] * 255)   # index 0 = opaque red
        ImageDraw.Draw(im).rectangle(r, fill=0)
        frames.append(im)
    frames[0].save(path, save_all=True, append_images=frames[1:], transparency=1,
                   disposal=2, duration=80, loop=0, optimize=False)


def test_trim_and_crop_uses_the_union_bbox_so_no_frame_clips(tmp_path):
    src = tmp_path / "src.gif"
    # Frame 1 is LARGER than the end frame (3); the crop is the UNION of every retained
    # frame's alpha bbox, so the bigger lead-in frame never clips. (End-frame-only cropping
    # would clip frame 1 to the smaller end-pose box (6,6,15,15).)
    _make_source_gif(src, [(8, 8, 11, 11), (2, 2, 17, 17), (4, 4, 10, 10), (6, 6, 14, 14), (1, 1, 18, 18)])
    frames, durations, bbox = _mod().trim_and_crop(str(src), 3)
    assert len(frames) == 4                       # frames 0..3 (the lead-in + the pose); frame 4 dropped
    assert len(durations) == 4
    assert bbox == (2, 2, 18, 18)                 # union of frames 0..3, not the end frame's (6,6,15,15)
    assert all(f.size == (16, 16) for f in frames)   # every retained frame cropped to the one union window
    assert frames[1].getchannel("A").getbbox() == (0, 0, 16, 16)   # the big frame survives un-clipped


def test_trim_and_crop_clamps_an_out_of_range_pick(tmp_path):
    src = tmp_path / "src.gif"
    _make_source_gif(src, [(2, 2, 6, 6), (3, 3, 9, 9)])
    frames, _, _ = _mod().trim_and_crop(str(src), 99)
    assert len(frames) == 2                        # clamped to the last frame


def test_write_play_once_is_play_once_at_the_union_size(tmp_path):
    src = tmp_path / "src.gif"
    # Distinct squares -> no frame coalescing, so the count is exact here.
    _make_source_gif(src, [(2, 2, 4, 4), (2, 2, 8, 8), (3, 3, 12, 12), (5, 5, 15, 15)])
    frames, durations, bbox = _mod().trim_and_crop(str(src), 3)
    out = tmp_path / "out.gif"
    _mod().write_play_once(frames, durations, str(out))
    data = out.read_bytes()
    assert b"NETSCAPE2.0" not in data               # no loop block -> the browser plays it once
    reopened = Image.open(str(out))
    assert reopened.n_frames == 4
    # Canvas == the union crop window (here (2,2,16,16) -> 14x14); the end pose sits inside it
    # with margin (the visible trade-off vs an end-frame crop).
    assert reopened.size == (bbox[2] - bbox[0], bbox[3] - bbox[1]) == (14, 14)


def test_write_play_once_preserves_transparency(tmp_path):
    """A non-rectangular figure leaves transparent pixels inside its bbox; the saved gif must
    keep them transparent (the popup strip background shows through the cut-out)."""
    src = tmp_path / "src.gif"
    frames = []
    for _ in range(3):
        im = Image.new("P", (20, 20), 1)            # index 1 = transparent
        im.putpalette([220, 40, 40] + [0, 0, 0] * 255)   # index 0 = opaque
        ImageDraw.Draw(im).ellipse((4, 4, 16, 16), fill=0)   # round figure -> transparent corners
        im.info["transparency"] = 1
        frames.append(im)
    frames[0].save(src, save_all=True, append_images=frames[1:], transparency=1,
                   disposal=2, duration=80, loop=0, optimize=False)
    fr, du, _ = _mod().trim_and_crop(str(src), 2)
    out = tmp_path / "out.gif"
    _mod().write_play_once(fr, du, str(out))
    im = Image.open(str(out)); im.seek(im.n_frames - 1)
    lo, hi = im.convert("RGBA").getchannel("A").getextrema()
    assert lo == 0 and hi == 255                     # both transparent corners and opaque figure


def test_write_play_once_coalesces_identical_frames_keeping_the_pose(tmp_path):
    """Consecutive identical frames are merged on save (Pillow sums their durations); the
    sequence still ends on the picked pose, so this is a harmless size win, not a drop."""
    src = tmp_path / "src.gif"
    pose = (5, 5, 15, 15)
    _make_source_gif(src, [(2, 2, 6, 6), pose, pose, pose])   # frames 1..3 identical
    frames, durations, _ = _mod().trim_and_crop(str(src), 3)
    out = tmp_path / "out.gif"
    _mod().write_play_once(frames, durations, str(out))
    reopened = Image.open(str(out))
    assert reopened.n_frames < 4                     # the 3 identical frames coalesced
    reopened.seek(reopened.n_frames - 1)
    assert reopened.convert("RGBA").getchannel("A").getbbox() is not None   # ends on a real frame (the pose)


def test_committed_web_gifs_are_play_once_trimmed_and_transparent():
    """Each committed popup gif is play-once (no loop block), transparent, and trimmed to at
    most its manifest pick + 1 frames (identical frames may coalesce below that). Keeps the
    committed assets in sync with assets/player_figures.json + the bundler. No LFS needed -
    reads only the committed web gifs."""
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, st in man.items():
        online_idx = st["online"][1]
        onpace_idx = st.get("onpace", st["online"])[1]
        for fname, idx in ((f"{name}.gif", online_idx), (f"{name}__fire.gif", onpace_idx)):
            p = WEB / fname
            assert p.exists(), fname
            assert b"NETSCAPE2.0" not in p.read_bytes(), f"{fname}: still loops"
            im = Image.open(str(p))
            assert 1 <= im.n_frames <= idx + 1, f"{fname}: {im.n_frames} frames, pick {idx}"
            im.seek(im.n_frames - 1)
            assert im.convert("RGBA").getchannel("A").getextrema()[0] == 0, f"{fname}: opaque bg"
