"""Pre-matte tuner — BROWSER based, multi-subject, with per-subject frame scrubbing.

The GPU matte venv (`temp/asset-venv-gpu`) ships HEADLESS OpenCV, so a cv2 GUI window can't open.
This serves a web page: each subject gets a FRAME slider (scrub the spawn-in / idle to land on the
overlap frame — the raw preview updates instantly) plus a live PRE-DARKEN preview (the cv2 paint-to-bg
step that feeds birefnet, also instant) plus, on Render, its matte. The global
KEY_THR / CSUB / TFLOOR / YELLOW_S / BRIGHT_V sliders re-darken the CURRENT frame of every subject LIVE
(instant pre-darken) so you can dial one value across all of them; hit Render to re-matte.

KEY_THR is the subject-vs-background discriminator for the collision fix (the whole plate footprint is
painted to the clean background, then pixels whose recovered colour differs from it by >= KEY_THR are
stamped back as subject).

  temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/tune_prematte.py temp/asset_matte_run4 \
      --kart mario__base__hot_rod donkey_kong__base__roadster_royale mario__base__b_dasher

Names before --kart use the char template; names after use the kart template. Render re-mattes the
current frame of each (~1 s/frame), so it's button-driven; the pre-darken + raw previews are instant."""
import base64, glob, json, os, sys, threading
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
INFER_LOCK = threading.Lock()                   # serialise birefnet inference across request threads
PORT = 8799
SUBJECTS = []                                   # [{name, is_char, paths:[...]}]
TEMPLATES = {}                                  # is_char -> (t, C, A, mask)


def _checker(h, w, s=14, a=210, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    return np.where(((xx // s + yy // s) % 2 == 0), a, b).astype(np.uint8)[..., None].repeat(3, 2)


def _png_b64(bgr):
    ok, buf = cv2.imencode(".png", bgr)
    return base64.b64encode(buf).decode()


def _matte_rgba(bgr):
    with INFER_LOCK:
        return np.array(remove(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)),
                               session=SESSION, post_process_mask=True))


def _comp_b64(rgba):
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    comp = rgba[..., :3].astype(np.float32) * a + _checker(*rgba.shape[:2]).astype(np.float32) * (1 - a)
    return _png_b64(cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR))


def _pre_only(idx, raw, p):                     # the cv2 paint-to-bg step (CPU, instant) — feeds birefnet
    t, C, A, mask = TEMPLATES[SUBJECTS[idx]["is_char"]]
    return pd.pre_darken(raw, t, C, A, mask, KEY_THR=p["KEY_THR"], CSUB=p["CSUB"], TFLOOR=p["TFLOOR"],
                         YELLOW_S=p["YELLOW_S"], BRIGHT_V=p["BRIGHT_V"])


def _render_subject(idx, raw, p):
    pre = _pre_only(idx, raw, p)
    return {"pre": _png_b64(pre), "matte": _comp_b64(_matte_rgba(pre))}


def _parse_params(q):
    return {"KEY_THR": int(float(q["KEY_THR"][0])), "CSUB": float(q["CSUB"][0]),
            "TFLOOR": float(q["TFLOOR"][0]),
            "YELLOW_S": int(float(q["YELLOW_S"][0])), "BRIGHT_V": int(float(q["BRIGHT_V"][0]))}


HTML = """<!doctype html><meta charset=utf-8><title>pre-matte tuner</title>
<style>body{font:13px system-ui;margin:0;background:#1b1d21;color:#ccc}
.bar{position:sticky;top:0;z-index:9;background:#23262b;padding:10px 14px;border-bottom:1px solid #333;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
label{display:flex;flex-direction:column;font-variant-numeric:tabular-nums;font-size:11px}
input[type=range]{width:130px}button{padding:7px 16px;font-weight:600;cursor:pointer}
#vals{font-family:monospace;color:#6f6}
.cols{display:flex;gap:12px;padding:12px;align-items:flex-start}
.col{flex:1;min-width:0}.col h3{margin:0 0 4px;font-size:12px;color:#6cf;font-weight:600}
.col .fr{font-family:monospace;color:#999;font-size:11px}
img{width:100%;background:#0a0a0a;display:block;margin-top:4px}
.tag{font-size:10px;color:#888;margin-top:6px}.tag b{color:#6cf}</style>
<div class=bar>
 <label>KEY_THR<input id=KEY_THR type=range min=0 max=120 step=1 value=60 oninput=upd()></label>
 <label>CSUB<input id=CSUB type=range min=0.5 max=1.3 step=0.01 value=0.75 oninput=upd()></label>
 <label>TFLOOR<input id=TFLOOR type=range min=0.01 max=0.5 step=0.01 value=0.01 oninput=upd()></label>
 <label>YELLOW_S<input id=YELLOW_S type=range min=0 max=255 step=1 value=250 oninput=upd()></label>
 <label>BRIGHT_V<input id=BRIGHT_V type=range min=0 max=255 step=1 value=255 oninput=upd()></label>
 <button onclick=render()>Render all</button>
 <span id=vals></span><span id=status></span>
</div>
<div class=cols id=cols></div>
<script>
const SUBJECTS=__SUBJECTS__;          // [{name,n}]
const pids=['KEY_THR','CSUB','TFLOOR','YELLOW_S','BRIGHT_V'];
function pvals(){let o={};pids.forEach(i=>o[i]=document.getElementById(i).value);return o}
let preTimer=null;
function upd(){let v=pvals();document.getElementById('vals').textContent=
  `KEY_THR=${v.KEY_THR} CSUB=${v.CSUB} TFLOOR=${v.TFLOOR} YELLOW_S=${v.YELLOW_S} BRIGHT_V=${v.BRIGHT_V}`;
  clearTimeout(preTimer);preTimer=setTimeout(()=>SUBJECTS.forEach((_,i)=>prefetch(i)),120)}
function preq(i){let q=new URLSearchParams(pvals());q.set('s',i);q.set('f',document.getElementById('f'+i).value);return q}
async function prefetch(i){let r=await fetch('/pre?'+preq(i));
  document.getElementById('pre'+i).src='data:image/png;base64,'+(await r.json()).data}
function fidx(){return SUBJECTS.map((_,i)=>+document.getElementById('f'+i).value)}
document.getElementById('cols').innerHTML=SUBJECTS.map((s,i)=>{
  const d=Math.floor(s.n*0.30);                 // default toward the spawn-in landing
  return `<div class=col><h3>${s.name}</h3>
   <input id=f${i} type=range min=0 max=${s.n-1} value=${d} oninput=scrub(${i})>
   <span class=fr id=fr${i}></span>
   <div class=tag>raw (scrub to the spawn-in / overlap frame)</div><img id=raw${i}>
   <div class=tag><b>pre-darken</b> — paint-to-bg fed to birefnet (live)</div><img id=pre${i}>
   <div class=tag>matte</div><img id=matte${i}>
   </div>`}).join('');
async function scrub(i){
  document.getElementById('fr'+i).textContent='frame '+document.getElementById('f'+i).value;
  let r=await fetch(`/frame?s=${i}&f=${document.getElementById('f'+i).value}`);
  document.getElementById('raw'+i).src='data:image/png;base64,'+(await r.json()).data;
  prefetch(i);
}
async function render(){
  let v=pvals(), q=new URLSearchParams(v);
  q.set('f',fidx().join(','));
  document.getElementById('status').textContent=' …rendering';
  let j=await (await fetch('/render?'+q)).json();
  j.results.forEach((r,i)=>{
    document.getElementById('pre'+i).src='data:image/png;base64,'+r.pre;
    document.getElementById('matte'+i).src='data:image/png;base64,'+r.matte;
  });
  document.getElementById('status').textContent=' done';
}
upd(); SUBJECTS.forEach((_,i)=>scrub(i)); render();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            self._route()
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, "text/plain", b"error")
            except Exception:
                pass

    def _route(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            meta = json.dumps([{"name": s["name"], "n": len(s["paths"])} for s in SUBJECTS])
            self._send(200, "text/html", HTML.replace("__SUBJECTS__", meta).encode())
        elif u.path == "/frame":
            s, f = int(q["s"][0]), int(q["f"][0])
            raw = cv2.imread(SUBJECTS[s]["paths"][f])
            self._send(200, "application/json", json.dumps({"data": _png_b64(raw)}).encode())
        elif u.path == "/pre":
            s, f = int(q["s"][0]), int(q["f"][0])
            pre = _pre_only(s, cv2.imread(SUBJECTS[s]["paths"][f]), _parse_params(q))
            self._send(200, "application/json", json.dumps({"data": _png_b64(pre)}).encode())
        elif u.path == "/render":
            p = _parse_params(q)
            fidx = [int(x) for x in q["f"][0].split(",")]
            results = [_render_subject(i, cv2.imread(s["paths"][fidx[i]]), p)
                       for i, s in enumerate(SUBJECTS)]
            self._send(200, "application/json", json.dumps({"results": results}).encode())
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
        paths = sorted(glob.glob(f"{base}/loopframes/{name}/*.png"))
        if not paths:
            continue
        SUBJECTS.append({"name": name, "is_char": is_char, "paths": paths})
        TEMPLATES.setdefault(is_char, pd.load_template(is_char))
        print(f"  {name}: {len(paths)} frames", flush=True)
    print(f"open  http://127.0.0.1:{PORT}/   ({len(SUBJECTS)} subjects)   Ctrl-C to stop", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
