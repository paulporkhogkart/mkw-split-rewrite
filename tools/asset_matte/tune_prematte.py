"""Pre-matte tuner — BROWSER based.

The GPU matte venv (`temp/asset-venv-gpu`) ships HEADLESS OpenCV, so a cv2 GUI window can't open
(`cvNamedWindow ... not implemented`). This serves a small web page instead: open the printed URL,
drag CSUB / TFLOOR / YELLOW_S / BRIGHT_V, hit Render to re-darken + re-matte the representative
frames over a checkerboard, and read off the values you like — they go straight into pre_darken.

  temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/tune_prematte.py temp/asset_matte_run3 \
      mario__base donkey_kong__base koopa_troopa__base [--kart <kart_name> ...]

Names before --kart use the char template; names after use the kart template. Re-mattes on Render
(not live) because each preview runs birefnet (~1 s/frame)."""
import base64, glob, json, os, sys
import numpy as np, cv2
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_darken as pd

try:                                            # make the GPU CUDA/cuDNN DLLs loadable (GPU venv)
    import nvidia
    for _d in glob.glob(os.path.join(os.path.dirname(nvidia.__file__), "*", "bin")):
        try: os.add_dll_directory(_d)
        except Exception: pass
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
    import onnxruntime; onnxruntime.preload_dlls()
except Exception:
    pass
from rembg import remove, new_session
from PIL import Image

SESSION = new_session("birefnet-general-lite", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
PORT = 8799
FRAMES = []                                     # (name, is_char, raw_bgr)
TEMPLATES = {}                                  # is_char -> (t, C, mask)


def _checker(h, w, s=14, a=210, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    return np.where(((xx // s + yy // s) % 2 == 0), a, b).astype(np.uint8)[..., None].repeat(3, 2)


def _render_b64(raw, is_char, p):
    t, C, mask = TEMPLATES[is_char]
    pre = pd.pre_darken(raw, t, C, mask, CSUB=p["CSUB"], TFLOOR=p["TFLOOR"],
                        YELLOW_S=p["YELLOW_S"], BRIGHT_V=p["BRIGHT_V"])
    rgba = np.array(remove(Image.fromarray(cv2.cvtColor(pre, cv2.COLOR_BGR2RGB)),
                           session=SESSION, post_process_mask=True))
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    comp = (rgba[..., :3].astype(np.float32) * a + _checker(*rgba.shape[:2]).astype(np.float32) * (1 - a))
    ok, buf = cv2.imencode(".png", cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR))
    return base64.b64encode(buf).decode()


HTML = """<!doctype html><meta charset=utf-8><title>pre-matte tuner</title>
<style>body{font:13px system-ui;margin:0;background:#1b1d21;color:#ccc}
.bar{position:sticky;top:0;background:#23262b;padding:10px 14px;border-bottom:1px solid #333;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
label{display:flex;flex-direction:column;font-variant-numeric:tabular-nums}
input[type=range]{width:150px}button{padding:7px 16px;font-weight:600;cursor:pointer}
.imgs{display:flex;flex-wrap:wrap;gap:10px;padding:12px}img{max-height:520px;background:#0a0a0a}
#vals{font-family:monospace;color:#6f6}</style>
<div class=bar>
 <label>CSUB <input id=CSUB type=range min=0.5 max=1.3 step=0.01 value=1.0 oninput=upd()></label>
 <label>TFLOOR <input id=TFLOOR type=range min=0.01 max=0.5 step=0.01 value=0.05 oninput=upd()></label>
 <label>YELLOW_S <input id=YELLOW_S type=range min=0 max=255 step=1 value=60 oninput=upd()></label>
 <label>BRIGHT_V <input id=BRIGHT_V type=range min=0 max=255 step=1 value=200 oninput=upd()></label>
 <button onclick=render()>Render</button>
 <span id=vals></span><span id=status></span>
</div>
<div class=imgs id=imgs></div>
<script>
const ids=['CSUB','TFLOOR','YELLOW_S','BRIGHT_V'];
function vals(){let o={};ids.forEach(i=>o[i]=document.getElementById(i).value);return o}
function upd(){let v=vals();document.getElementById('vals').textContent=
  `CSUB=${v.CSUB} TFLOOR=${v.TFLOOR} YELLOW_S=${v.YELLOW_S} BRIGHT_V=${v.BRIGHT_V}`}
async function render(){
  let v=vals(), q=new URLSearchParams(v).toString();
  document.getElementById('status').textContent=' …rendering';
  let r=await fetch('/render?'+q), j=await r.json();
  document.getElementById('imgs').innerHTML=j.imgs.map(x=>
    `<div><div>${x.name}</div><img src="data:image/png;base64,${x.data}"></div>`).join('');
  document.getElementById('status').textContent=' done';
}
upd(); render();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, "text/html", HTML.encode())
        elif u.path == "/render":
            q = parse_qs(u.query)
            p = {"CSUB": float(q["CSUB"][0]), "TFLOOR": float(q["TFLOOR"][0]),
                 "YELLOW_S": int(float(q["YELLOW_S"][0])), "BRIGHT_V": int(float(q["BRIGHT_V"][0]))}
            imgs = [{"name": n, "data": _render_b64(raw, ic, p)} for (n, ic, raw) in FRAMES]
            self._send(200, "application/json", json.dumps({"imgs": imgs}).encode())
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    args = sys.argv[1:]
    base, rest = args[0], args[1:]
    cut = rest.index("--kart") if "--kart" in rest else len(rest)
    items = [(n, True) for n in rest[:cut]] + [(n, False) for n in rest[cut + 1:]]
    for name, is_char in items:
        fs = sorted(glob.glob(f"{base}/loopframes/{name}/*.png"))
        if fs:
            FRAMES.append((name, is_char, cv2.imread(fs[len(fs) // 2])))
            TEMPLATES.setdefault(is_char, pd.load_template(is_char))
    print(f"open  http://127.0.0.1:{PORT}/   ({len(FRAMES)} frames)   Ctrl-C to stop", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
