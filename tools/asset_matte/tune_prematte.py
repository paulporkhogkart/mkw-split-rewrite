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
