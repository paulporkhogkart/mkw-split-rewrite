# Nameplate Removal via Pre-Matte Un-darkening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the select-screen nametag plate from the transparent cutouts by fully un-darkening the plate's serration in the raw frame **before** matting, so birefnet detaches the plate itself — one path for characters and karts.

**Architecture:** Insert a `pre_darken` step between `extract_loop` and `matte_loop`. It recovers (full `CSUB=1.0`) the semi-transparent serration in the plate footprint and leaves the yellow text + bright badge as opaque UI; birefnet then drops the plate-over-background + text and keeps the recovered character. Retires the post-matte `undark.py` (drop_nameplate/undark_rgba). Reuses `nametag_core`, the committed `assets/` templates, and `build_templates`.

**Tech Stack:** Python 3 (build python `python` = Python314, cv2/numpy; GPU matte venv `temp/asset-venv-gpu/Scripts/python.exe`, cv2/numpy/PIL/rembg), OpenCV, NumPy. Tests run under build python via `pytest`.

## Global Constraints

- **Import convention (binding):** `tools/asset_matte` is NOT a package; `tests/conftest.py` already adds it to `sys.path`. Use FLAT imports everywhere: `import nametag_core`, `import pre_darken` — never `tools.asset_matte.pre_darken`. Scripts run directly get their own dir on `sys.path[0]`, so flat imports work there too.
- **Method values (pre-matte defaults):** `CSUB=1.0` (FULL recovery — the fix; post-matte's 0.69 only lightens), `TFLOOR=0.05`, `T_OPAQUE=0.20`, `YELLOW_S=60`, `BRIGHT_V=200`. These are the validated starting points; the tuner can override them, but the committed defaults must be these.
- **Production crop / size (unchanged):** `nametag_core.PROD_CROP_4K=(2100,36,3720,1806)`, `OUT_W,OUT_H=988,1080`, `NAMEPLATE_HERO_ROI=(1050,18,1860,903)`; ROIs `CHAR_ROI=(2378,1604,1178,226)`, `PLATE_ROI=(2360,1602,1378,226)`.
- **Strip-only rule (binding):** un-darken ONLY where `mask>0.05` AND `t≥T_OPAQUE` (semi-transparent) AND NOT yellow text (`HSV S>YELLOW_S`) AND NOT bright badge (`HSV V>BRIGHT_V`). Text/badge pass through unchanged so birefnet drops them as UI.
- `nametag_core.py`, `build_templates.py`, committed `tools/asset_matte/assets/` are unchanged. `matte_loop.py` is unchanged (it mattes whatever loopframes it's given and encodes the final webp/apng).
- All asset outputs/scratch live under gitignored `temp/`. Run all commands from repo root `C:/development/mkw-split-rewrite`.

---

## File Structure

- `tools/asset_matte/pre_darken.py` (NEW) — `load_template(is_char) -> (t,C,mask)` (moved verbatim from `undark.py`), `pre_darken(raw_bgr, t, C, mask, ...) -> bgr` (the recovery), `process(base, names, is_char)` (batch: raw loopframes → `_pre` loopframes), `main()`.
- `tools/asset_matte/tune_prematte.py` (NEW) — interactive cv2-trackbar tuner (GPU venv): re-darken + re-matte representative frames on demand, print params on quit.
- `tools/asset_matte/undark.py` (DELETE) + `tests/test_undark.py` (DELETE) — retired (post-matte plate removal superseded).
- `tests/test_pre_darken.py` (NEW) — synthetic recovery tests.

---

### Task 1: `pre_darken.py` — template loader + the recovery function

**Files:**
- Create: `tools/asset_matte/pre_darken.py`
- Test: `tests/test_pre_darken.py`

**Interfaces:**
- Consumes: `nametag_core` (`prod_crop`, `place_in_canvas`, `solve_tc`, `CHAR_ROI`, `PLATE_ROI`); committed `assets/{char,kart}_{P,A}.png`, `nametag_{char,kart}_mask4k.png`.
- Produces:
  - `load_template(is_char: bool) -> (t HxW float64, C HxW float64, mask HxW float64[0,1])`
  - `pre_darken(raw_bgr HxWx3 uint8, t, C, mask, CSUB=1.0, TFLOOR=0.05, YELLOW_S=60, BRIGHT_V=200) -> HxWx3 uint8`
  - module constant `T_OPAQUE = 0.20`

- [ ] **Step 1: Write the failing tests** — `tests/test_pre_darken.py`:

```python
import numpy as np
import pre_darken as pd        # FLAT import — conftest adds tools/asset_matte to sys.path


def test_full_recovery_returns_the_scene_behind_the_plate():
    # The plate maps the scene as O = t*scene + C. Full recovery (CSUB=1.0) returns the scene
    # inside the semi-transparent serration; outside the plate the frame is untouched.
    h, w = 64, 64
    t = np.full((h, w), 0.5); C = np.full((h, w), 70.0)
    mask = np.zeros((h, w)); mask[20:44, 10:54] = 1.0
    scene = np.array([120.0, 130.0, 140.0])
    O = np.clip(t[..., None] * scene + C[..., None], 0, 255)
    frame = O.astype(np.uint8)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0, TFLOOR=0.05, YELLOW_S=60, BRIGHT_V=200)
    assert np.allclose(out[30, 30], scene, atol=4)        # serration recovered to the scene
    assert np.array_equal(out[5, 5], frame[5, 5])         # outside the plate: untouched


def test_yellow_text_is_left_as_ui():
    h, w = 32, 32
    t = np.full((h, w), 0.5); C = np.full((h, w), 70.0); mask = np.ones((h, w))
    frame = np.zeros((h, w, 3), np.uint8); frame[:] = (10, 200, 220)   # saturated yellow (BGR)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0, YELLOW_S=60, BRIGHT_V=255)  # only the yellow rule active
    assert np.array_equal(out, frame)                     # passed through (birefnet will drop it)


def test_bright_badge_is_left_as_ui():
    h, w = 32, 32
    t = np.full((h, w), 0.5); C = np.full((h, w), 70.0); mask = np.ones((h, w))
    frame = np.full((h, w, 3), 235, np.uint8)             # bright, low-saturation (badge-like)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0, YELLOW_S=255, BRIGHT_V=200)  # only the bright rule active
    assert np.array_equal(out, frame)


def test_opaque_plate_core_not_recovered():
    # Where t < T_OPAQUE (opaque glyph), the serration rule excludes it -> passed through.
    h, w = 32, 32
    t = np.full((h, w), 0.10); C = np.full((h, w), 70.0); mask = np.ones((h, w))   # t below T_OPAQUE
    frame = np.full((h, w, 3), 100, np.uint8)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0)
    assert np.array_equal(out, frame)
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_pre_darken.py -q`
Expected: FAIL (`ModuleNotFoundError: pre_darken`).

- [ ] **Step 3: Implement `tools/asset_matte/pre_darken.py`** (function bodies + `load_template` moved verbatim from `undark.py`):

```python
"""Pre-matte nametag removal: fully un-darken the plate's serration in the RAW frame so birefnet
detaches the plate itself (the serration-over-bg becomes true background -> dropped; the
serration-over-character becomes the character -> kept; the yellow text + bright badge are left as
opaque UI -> dropped). One path for characters and karts. See the 2026-06-28 pre-matte design spec.

Pre-darken is pure cv2/numpy (build python). Run the matte separately (matte_loop.py, GPU venv)."""
import glob, os, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # flat `import nametag_core`
import nametag_core as nc

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
T_OPAQUE = 0.20    # transmission below this = opaque plate content (text/badge) -> left as UI


def load_template(is_char):
    """Per-screen (t, C, mask) at the production crop, from the committed templates."""
    roi = nc.CHAR_ROI if is_char else nc.PLATE_ROI
    pre = "char" if is_char else "kart"
    P = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_P.png"), roi)).astype(np.float64)
    A = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_A.png"), roi)).astype(np.float64)
    t, C = nc.solve_tc(P, A)
    mask = nc.prod_crop(cv2.imread(f"{ASSETS}/nametag_{pre}_mask4k.png", cv2.IMREAD_GRAYSCALE)
                        .astype(np.float64) / 255.0)
    return t, C, mask


def pre_darken(raw_bgr, t, C, mask, CSUB=1.0, TFLOOR=0.05, YELLOW_S=60, BRIGHT_V=200):
    """Un-darken ONLY the semi-transparent serration (full recovery); leave text/badge as UI."""
    O = raw_bgr.astype(np.float64)
    hsv = cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2HSV)
    serr = ((mask > 0.05) & (t >= T_OPAQUE)
            & ~(hsv[..., 1] > YELLOW_S) & ~(hsv[..., 2] > BRIGHT_V)).astype(np.float64)
    S = np.clip((O - CSUB * C[..., None]) / np.clip(t, TFLOOR, 1.6)[..., None], 0, 255)
    out = np.clip(O * (1 - serr[..., None]) + S * serr[..., None], 0, 255)
    return out.astype(np.uint8)
```

- [ ] **Step 4: Run tests, expect pass**

Run: `python -m pytest tests/test_pre_darken.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/pre_darken.py tests/test_pre_darken.py
git commit -m "feat(asset-matte): pre-darken recovery + per-screen template loader"
```

---

### Task 2: `process` driver + `main`; retire `undark.py`

**Files:**
- Modify: `tools/asset_matte/pre_darken.py` (add `process` + `main`)
- Delete: `tools/asset_matte/undark.py`, `tests/test_undark.py`

**Interfaces:**
- Consumes: `pre_darken`, `load_template` (Task 1).
- Produces: `process(base, names, is_char)` writes `<base>/loopframes/<name>_pre/NNN.png` for each name; CLI `python tools/asset_matte/pre_darken.py <base> [--kart] <name...>`.

- [ ] **Step 1: Add `process` + `main` to `tools/asset_matte/pre_darken.py`** (append):

```python
def process(base, names, is_char):
    """Pre-darken every raw loopframe of each name -> <base>/loopframes/<name>_pre/."""
    t, C, mask = load_template(is_char)
    for name in names:
        src = f"{base}/loopframes/{name}"
        dst = f"{base}/loopframes/{name}_pre"; os.makedirs(dst, exist_ok=True)
        files = sorted(glob.glob(f"{src}/*.png"))
        for f in files:
            raw = cv2.imread(f)
            if raw is None:
                continue
            cv2.imwrite(f"{dst}/{os.path.basename(f)}", pre_darken(raw, t, C, mask))
        print(f"{name}: {len(files)} frames pre-darkened ({'char' if is_char else 'kart'}) -> {dst}", flush=True)


def main():
    a = sys.argv[1:]
    is_char = "--kart" not in a
    a = [x for x in a if x != "--kart"]
    process(a[0], a[1:], is_char)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Delete the retired post-matte module + its test**

```bash
git rm tools/asset_matte/undark.py tests/test_undark.py
```

- [ ] **Step 3: Verify nothing imports `undark` and the suite still collects**

Run: `grep -rn "import undark" tools/ tests/ ; python -m pytest tests/ -q`
Expected: the grep prints nothing; suite PASSES (prior count minus the 4 deleted `test_undark` tests, plus Task 1's 4 = net same; confirm no collection error). Confirm `python -c "import sys; sys.path.insert(0,'tools/asset_matte'); import pre_darken; print(pre_darken.T_OPAQUE)"` prints `0.2`.

- [ ] **Step 4: Commit**

```bash
git add tools/asset_matte/pre_darken.py
git commit -m "feat(asset-matte): pre_darken batch driver + CLI; retire post-matte undark.py"
```

---

### Task 3: `tune_prematte.py` — the pre-matte trackbar tuner

**Files:**
- Create: `tools/asset_matte/tune_prematte.py`

**Interfaces:**
- Consumes: `pre_darken`, `load_template` (Task 1); `rembg` (GPU venv) for the live matte; representative raw loopframes under `<base>/loopframes/<name>/`.
- Produces: an interactive window; prints the chosen `CSUB/TFLOOR/YELLOW_S/BRIGHT_V` on quit. No unit test (interactive + GPU; validated by the user's eye-test, consistent with every prior asset tool).

**Context:** Pre-matte previews require a real birefnet matte, so the preview is **on demand** (press SPACE to re-darken + re-matte the shown frames), not on every trackbar tick — a true live re-render would re-matte dozens of times per drag. Defaults are the pre-matte values from Global Constraints. Run in the GPU venv.

- [ ] **Step 1: Implement `tools/asset_matte/tune_prematte.py`:**

```python
"""Pre-matte tuner: dial CSUB / TFLOOR / YELLOW_S / BRIGHT_V, then press SPACE to re-darken +
re-matte the representative frames and see the result over a checkerboard. 'q' quits and prints
the chosen params. Run in the GPU venv (needs rembg):
  temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/tune_prematte.py <base> mario__base \
      donkey_kong__base koopa_troopa__base --kart <kart_name>
The names before --kart use the char template; names after use the kart template.
"""
import glob, os, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_darken as pd

try:
    import nvidia
    for d in glob.glob(os.path.join(os.path.dirname(nvidia.__file__), "*", "bin")):
        try: os.add_dll_directory(d)
        except Exception: pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    import onnxruntime; onnxruntime.preload_dlls()
except Exception:
    pass
from rembg import remove, new_session
SESSION = new_session("birefnet-general-lite", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])


def _checker(h, w, s=14, a=210, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    return np.where(((xx // s + yy // s) % 2 == 0), a, b).astype(np.uint8)[..., None].repeat(3, 2)


def _matte_over_checker(bgr):
    from PIL import Image
    rgba = np.array(remove(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)),
                           session=SESSION, post_process_mask=True))   # RGBA
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32)
    comp = rgb * a + _checker(*rgba.shape[:2]).astype(np.float32) * (1 - a)
    return cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR)


def main():
    args = sys.argv[1:]
    base = args[0]
    rest = args[1:]
    cut = rest.index("--kart") if "--kart" in rest else len(rest)
    char_names, kart_names = rest[:cut], rest[cut + 1:]
    items = [(n, True) for n in char_names] + [(n, False) for n in kart_names]
    # load the middle raw frame of each
    frames = []
    for name, is_char in items:
        fs = sorted(glob.glob(f"{base}/loopframes/{name}/*.png"))
        if fs:
            frames.append((name, is_char, cv2.imread(fs[len(fs) // 2])))
    templates = {True: pd.load_template(True), False: pd.load_template(False)}

    cv2.namedWindow("tune", cv2.WINDOW_NORMAL)
    cv2.createTrackbar("CSUBx100", "tune", 100, 130, lambda v: None)   # 1.00
    cv2.createTrackbar("TFLOORx100", "tune", 5, 50, lambda v: None)
    cv2.createTrackbar("YELLOW_S", "tune", 60, 255, lambda v: None)
    cv2.createTrackbar("BRIGHT_V", "tune", 200, 255, lambda v: None)

    def render():
        g = lambda k: cv2.getTrackbarPos(k, "tune")
        CSUB, TFLOOR = g("CSUBx100") / 100.0, max(0.01, g("TFLOORx100") / 100.0)
        YS, BV = g("YELLOW_S"), g("BRIGHT_V")
        tiles = []
        for name, is_char, raw in frames:
            t, C, mask = templates[is_char]
            pre = pd.pre_darken(raw, t, C, mask, CSUB=CSUB, TFLOOR=TFLOOR, YELLOW_S=YS, BRIGHT_V=BV)
            out = _matte_over_checker(pre)
            cv2.putText(out, name, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 230, 255), 2)
            tiles.append(cv2.resize(out, (out.shape[1] // 2, out.shape[0] // 2)))
        h = max(t.shape[0] for t in tiles)
        tiles = [np.pad(t, ((0, h - t.shape[0]), (0, 0), (0, 0))) for t in tiles]
        cv2.imshow("tune", np.hstack(tiles))
        return CSUB, TFLOOR, YS, BV

    params = render()
    print("SPACE = re-render (re-matte), q = quit + print params", flush=True)
    while True:
        k = cv2.waitKey(50) & 0xFF
        if k == ord(" "):
            params = render()
        elif k == ord("q"):
            break
    cv2.destroyAllWindows()
    print(f"CSUB={params[0]:.2f} TFLOOR={params[1]:.2f} YELLOW_S={params[2]} BRIGHT_V={params[3]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check it imports under the GPU venv** (don't open the window in CI — just confirm import + session build):

Run: `temp/asset-venv-gpu/Scripts/python.exe -c "import sys; sys.path.insert(0,'tools/asset_matte'); import tune_prematte; print('ok')"`
Expected: prints `ok` (rembg session builds; no window).

- [ ] **Step 3: Commit**

```bash
git add tools/asset_matte/tune_prematte.py
git commit -m "feat(asset-matte): pre-matte trackbar tuner (full-recovery defaults, SPACE re-matte)"
```

---

### Task 4: End-to-end run on the 6 idle clips + eyeball (CONTROLLER-driven)

**Files:** none (pipeline run). Produces `temp/asset_matte_run3/matte/<name>_pre_frames/` + loop files.

- [ ] **Step 1: Extract raw loops** (build python; new run dir to keep it clean):

```bash
PYTHONPATH=. python tools/asset_matte/extract_loop.py temp/asset_matte_run3 \
  captures_sdr/en_uk/clips/baby_mario__base.mkv captures_sdr/en_uk/clips/dolphin__base.mkv \
  captures_sdr/en_uk/clips/donkey_kong__base.mkv captures_sdr/en_uk/clips/koopa_troopa__base.mkv \
  captures_sdr/en_uk/clips/mario__base.mkv captures_sdr/en_uk/clips/mario__touring.mkv
```
Expected: six `... crop=988x1080 ...` lines.

- [ ] **Step 2: Pre-darken** (build python; characters → char template):

```bash
python tools/asset_matte/pre_darken.py temp/asset_matte_run3 \
  baby_mario__base dolphin__base donkey_kong__base koopa_troopa__base mario__base mario__touring
```
Expected: six `... frames pre-darkened (char) -> ..._pre` lines.

- [ ] **Step 3: Matte the pre-darkened frames** (GPU venv):

```bash
temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/matte_loop.py temp/asset_matte_run3 \
  birefnet-general-lite - baby_mario__base_pre dolphin__base_pre donkey_kong__base_pre \
  koopa_troopa__base_pre mario__base_pre mario__touring_pre
```
Expected: `ALL DONE`; `matte/<name>_pre_frames/` + `_pre_loop.webp` per clip.

- [ ] **Step 4: Build a before/after montage and eyeball** (GPU venv has PIL):

```bash
temp/asset-venv-gpu/Scripts/python.exe - <<'PY'
import glob, numpy as np
from PIL import Image
B="temp/asset_matte_run3/matte"
def chk(h,w,s=18,a=210,b=150):
    yy,xx=np.mgrid[0:h,0:w]
    return Image.fromarray(np.where(((xx//s+yy//s)%2==0),a,b).astype(np.uint8)[...,None].repeat(3,2),"RGB").convert("RGBA")
def comp(p):
    im=Image.open(p).convert("RGBA"); return np.array(Image.alpha_composite(chk(im.height,im.width),im).convert("RGB"))
rows=[]
for n in ["mario__base","donkey_kong__base","koopa_troopa__base","baby_mario__base","dolphin__base","mario__touring"]:
    fs=sorted(glob.glob(f"{B}/{n}_pre_frames/*.png"))
    if fs: rows.append(comp(fs[len(fs)//2]))
w=max(r.shape[1] for r in rows); rows=[np.pad(r,((0,0),(0,w-r.shape[1]),(0,0)),constant_values=255) for r in rows]
Image.fromarray(np.vstack(rows)).save("temp/asset_matte_run3/prematte_result.png")
print("wrote temp/asset_matte_run3/prematte_result.png")
PY
```
Then Read `temp/asset_matte_run3/prematte_result.png`. **Acceptance:** the nameplate is gone for every character (no plate, no text, no serration), bodies intact, DK in particular clean. If a body is damaged or a plate residual remains, STOP and surface to the user — do NOT re-tune in code; that's what `tune_prematte.py` is for (the user dials, then re-run Steps 2-3 with their params).

- [ ] **Step 5: Present** the `prematte_result.png` and the `_pre_loop.webp` paths to the user for the animated eye-test. Do not commit `temp/` (gitignored).

---

## Self-Review

- **Spec coverage:** pre_darken recovery + strip-only rule + full-recovery CSUB=1.0 (Task 1); pipeline `extract_loop → pre_darken → matte_loop` + retire undark (Tasks 2, 4); per-screen template (Task 1 `load_template`, Task 2 `--kart`); the tuner with full-recovery defaults (Task 3); reuse of nametag_core/assets/build_templates (unchanged, not re-touched); testing = synthetic unit tests (Task 1) + user eye-test (Tasks 3-4). The kart **capture** path is out of scope (spec) — not in the plan. The deeper-overlap / B-Dasher-dip confirmation is the user's eye-test (Tasks 3-4), as the spec states.
- **Placeholder scan:** none — all steps carry concrete code/commands and expected output.
- **Type consistency:** `load_template -> (t,C,mask)` consumed by `pre_darken`/`process`/the tuner with matching arity; `pre_darken(raw,t,C,mask,CSUB,TFLOOR,YELLOW_S,BRIGHT_V)` signature identical across Task 1 tests, the driver, and the tuner; asset filenames match `build_templates`' committed outputs; flat imports throughout.

## Follow-up (not in this plan)
- Record a kart that dips deep into the plate (B-Dasher class) and confirm no fringe in the tuner — the decisive kart case.
- Kart-combo capture path (spawn-in-window exclusion + wheel-robust period from `silhouette_loop`).
- Optional raw-alpha-gated refine if any pre-darkened frame introduces a floor blob (none seen on DK/koopa/rob_hog).
