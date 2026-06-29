"""Blank-plate matte calibrator (browser, GPU venv) — the proven pipeline to play with.

Transform from the BLANK plate (no phantom/contamination); per-clip text mask per subject; inpaint the
interior notches; then SHAPE-BOUNDED DONOR bumper fill at the dip — measure the chassis dip dy by NCC,
shift the clean settled reference's SHAPE down by dy, and fill only text ∩ shifted-shape ∩ not-visible
(connectivity-gated to the car). The fill is bounded by the car's true shape, so it's exactly zero at
settled frames and grows only as the bumper actually dips behind the text — no manual offset/threshold.
Sliders: KEY_THR/CSUB/TFLOOR/FILL_K + SCORE_GATE (skip frames the chassis NCC can't locate). 'show
fill' tints inpaint=green, donor=magenta.

  temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/tune_blankplate.py temp/asset_matte_run4 \
      donkey_kong__base__roadster_royale mario__base__hot_rod mario__base__b_dasher
"""
import base64, glob, json, os, sys, threading
import numpy as np, cv2
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_darken as pd
import nametag_core as nc

try:
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
INFER_LOCK = threading.Lock()
PORT = 8800
BLANK_NPY = r"C:\development\mkw-split-rewrite\temp\notch_poc\blank_plate_masked.npy"

_t_d, _C_d, A, MASK = pd.load_template(False)
IN_PLATE = MASK > 0.05
Hh, Ww = MASK.shape[:2]
_, _X = np.indices((Hh, Ww))
BLANK = np.load(BLANK_NPY).astype(np.float32)
T_B, C_B = nc.solve_tc(BLANK, A)
BADGE = (T_B < pd.T_OPAQUE) & IN_PLATE
_dop = (_t_d < pd.T_OPAQUE) & IN_PLATE
_pxs = np.where(IN_PLATE.any(0))[0]
PX0, PX1 = _pxs.min(), _pxs.max()
BX0 = int(PX0 + 0.80 * (PX1 - PX0))
_tys = np.where((_dop & (_X < BX0)).any(1))[0]
_ty0, _ty1 = max(0, _tys.min() - 8), _tys.max() + 8
TEXT_BAND = np.zeros_like(IN_PLATE); TEXT_BAND[_ty0:_ty1 + 1, PX0:BX0] = True; TEXT_BAND &= IN_PLATE
PLATE_TOP = int(np.where(IN_PLATE.any(1))[0].min())
# per-frame ECC align crop: text-free, kart-only bumper-top band (left of the text, below the chin)
ER0, ER1 = PLATE_TOP - 10, _ty0 - 2
EC0, EC1 = max(0, PX0 - 25), PX0 + int(0.55 * (PX1 - PX0))

SUBJECTS = []                                   # [{name, paths, text, M, fl_alpha, fl_draw, fl_gray}]


def _png_b64(bgr):
    ok, buf = cv2.imencode(".png", bgr)
    return base64.b64encode(buf).decode()


def _matte_rgba(bgr):
    with INFER_LOCK:
        return np.array(remove(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)),
                               session=SESSION, post_process_mask=True))


def _checker(h, w, s=14, a=210, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    return np.where(((xx // s + yy // s) % 2 == 0), a, b).astype(np.uint8)[..., None].repeat(3, 2)


def _comp_b64(rgba):
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    comp = rgba[..., :3].astype(np.float32) * a + _checker(*rgba.shape[:2]).astype(np.float32) * (1 - a)
    return _png_b64(cv2.cvtColor(comp.astype(np.uint8), cv2.COLOR_RGB2BGR))


def _text_mask(P_clip):
    t, _ = nc.solve_tc(P_clip, A)
    return cv2.dilate(((t < pd.T_OPAQUE) & TEXT_BAND).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)


def _predark(idx, raw, p):
    """Blank-transform pre-darken + inpaint interior notches. Returns (out_bgr, subject, fill_mask)."""
    O = raw.astype(np.float64)
    S = np.clip((O - p["CSUB"] * C_B[..., None]) / np.clip(T_B, p["TFLOOR"], 1.6)[..., None], 0, 255)
    opaque = (BADGE | SUBJECTS[idx]["text"]) & IN_PLATE
    subject = IN_PLATE & (np.abs(S - A).max(2) >= p["KEY_THR"]) & ~opaque
    out = O.copy(); out[IN_PLATE] = A[IN_PLATE]; out[subject] = S[subject]
    out = np.clip(out, 0, 255).astype(np.uint8)
    K = int(p["FILL_K"]) | 1
    closed = cv2.morphologyEx(subject.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (K, K))) > 0
    holes = IN_PLATE & closed & ~subject
    n, lab, st, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), 8)
    keep = np.zeros_like(holes)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] <= 2000:
            keep |= (lab == i)
    return cv2.inpaint(out, keep.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA), subject, keep


def _align_flourish(idx, frame_gray):
    """Locate the clean-flourish bumper in THIS frame: coarse NCC (also the gate) then ECC sub-pixel.
    Returns (dx, dy, score) where (dx,dy) maps the flourish into the current frame."""
    templ = SUBJECTS[idx]["fl_gray"][ER0:ER1, EC0:EC1]
    HP = 34
    win = frame_gray[ER0 - HP:ER1 + HP, EC0 - HP:EC1 + HP]
    res = cv2.matchTemplate(win, templ, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, mxl = cv2.minMaxLoc(res)
    dx, dy = float(mxl[0] - HP), float(mxl[1] - HP)
    warp = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
    try:
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-4)
        _cc, warp = cv2.findTransformECC(frame_gray[ER0:ER1, EC0:EC1].astype(np.float32),
                                         templ.astype(np.float32), warp, cv2.MOTION_TRANSLATION, crit, None, 5)
        dx, dy = float(warp[0, 2]), float(warp[1, 2])
    except cv2.error:
        pass
    return dx, dy, float(mx)


def _warp_flourish(idx, dx, dy):
    W = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
    al = cv2.warpAffine(SUBJECTS[idx]["fl_alpha"].astype(np.uint8), W, (Ww, Hh), flags=cv2.INTER_NEAREST) > 0
    bg = cv2.warpAffine(SUBJECTS[idx]["fl_draw"], W, (Ww, Hh), flags=cv2.INTER_LINEAR)
    return al, bg


def _pick_default_donor(dpaths):
    """Cleanest flourish donor = the held pose JUST BEFORE the fade-out. The flourish settles into a
    held idle pose (text gone, kart level), then dissolves to empty. The dissolve is a sharp collapse
    in edge energy, so the held frame is the one right before the biggest consecutive drop. This is
    length-independent and ignores trailing empty / stray captured frames. Falls back to the last
    frame if there's no clear fade (e.g. the clip ends on the hold)."""
    if not dpaths:
        return -1
    if len(dpaths) == 1:
        return 0
    edges = np.array([cv2.Laplacian(cv2.cvtColor(cv2.imread(p), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
                      for p in dpaths])
    drops = edges[:-1] - edges[1:]
    i = int(np.argmax(drops))
    return i if drops[i] > 0.4 * float(edges.max()) else len(dpaths) - 1


def _set_donor(s, di):
    """Matte donor frame di into subject dict s (in place). Returns the donor bgr, or None."""
    if not s["dpaths"] or not (0 <= di < len(s["dpaths"])):
        s["fl_alpha"] = s["fl_draw"] = s["fl_gray"] = None; s["donor_i"] = -1
        return None
    draw = cv2.imread(s["dpaths"][di])
    s["fl_draw"] = draw
    s["fl_gray"] = cv2.cvtColor(draw, cv2.COLOR_BGR2GRAY)
    s["fl_alpha"] = _matte_rgba(draw)[..., 3] > 128
    s["donor_i"] = di
    return draw


def _pre_core(idx, raw, p):
    """Pre-darken + inpaint, then (if DONOR) FLOURISH bumper fill. Returns (out, keep, fill).

    The flourish frame has the plate gone entirely, so it carries the car's COMPLETE bumper shape +
    real colour (the settled idle frame can't — its own text occludes the bumper bottom). Per frame we
    align that clean bumper to the current dip (coarse NCC → ECC sub-pixel), then fill exactly
    text ∩ flourish-shape ∩ not-already-visible (connectivity-gated to the car) and feather the
    boundary. At a settled frame the bumper isn't behind the text, so the fill is ~empty (no tail);
    it grows only as the bumper actually dips. SCORE_GATE skips frames the NCC can't locate the bumper
    in (wrong/absent kart). Falls back to plain inpaint when the subject has no flourish donor.
    """
    out, subject, keep = _predark(idx, raw, p)
    fill = np.zeros_like(subject)
    if p["DONOR"] and SUBJECTS[idx].get("fl_alpha") is not None and subject.any():
        dx, dy, score = _align_flourish(idx, cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY))
        if score >= p["SCORE_GATE"]:
            al, bg = _warp_flourish(idx, dx, dy)
            f = SUBJECTS[idx]["M"] & al & ~subject & IN_PLATE      # text ∩ flourish-shape ∩ not-visible
            _, lab = cv2.connectedComponents((subject | f).astype(np.uint8), 8)
            f = f & np.isin(lab, np.unique(lab[subject]))          # keep only fill touching the car
            if f.any():
                ff = float(p["FEATHER"])
                if ff >= 1:
                    a = np.clip(cv2.distanceTransform(f.astype(np.uint8), cv2.DIST_L2, 3) / ff, 0, 1)[..., None]
                    out = np.clip(out.astype(np.float32) * (1 - a) + bg.astype(np.float32) * a, 0, 255).astype(np.uint8)
                else:
                    out = out.copy(); out[f] = bg[f]
                fill = f
    return out, keep, fill


def _tint(img, keep, fill, hl):
    if hl:
        img = img.copy()
        if keep.any():
            img[keep] = (0.45 * img[keep] + 0.55 * np.array([70, 235, 70])).astype(np.uint8)
        if fill.any():
            img[fill] = (0.45 * img[fill] + 0.55 * np.array([235, 70, 235])).astype(np.uint8)
    return img


def _pre_disp(idx, raw, p):
    out, keep, fill = _pre_core(idx, raw, p)
    return _tint(out, keep, fill, p.get("HL"))


def _render_subject(idx, raw, p):
    out, keep, fill = _pre_core(idx, raw, p)
    return {"pre": _png_b64(_tint(out, keep, fill, p.get("HL"))), "matte": _comp_b64(_matte_rgba(out))}


def _parse(q):
    g = lambda k, d: q.get(k, [d])[0]
    return {"KEY_THR": int(float(q["KEY_THR"][0])), "CSUB": float(q["CSUB"][0]),
            "TFLOOR": float(q["TFLOOR"][0]), "FILL_K": int(float(q["FILL_K"][0])),
            "SCORE_GATE": float(g("SCORE_GATE", "0.45")), "FEATHER": float(g("FEATHER", "0")),
            "DONOR": g("DONOR", "1") in ("1", "true", "on"), "HL": g("HL", "1") in ("1", "true", "on")}


HTML = """<!doctype html><meta charset=utf-8><title>blank-plate matte tuner</title>
<style>body{font:13px system-ui;margin:0;background:#1b1d21;color:#ccc}
.bar{position:sticky;top:0;z-index:9;background:#23262b;padding:10px 14px;border-bottom:1px solid #333;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
label{display:flex;flex-direction:column;font-variant-numeric:tabular-nums;font-size:11px}
input[type=range]{width:120px}button{padding:7px 16px;font-weight:600;cursor:pointer}
#vals{font-family:monospace;color:#6f6;font-size:11px}
.cols{display:flex;gap:12px;padding:12px;align-items:flex-start}
.col{flex:1;min-width:0}.col h3{margin:0 0 4px;font-size:12px;color:#6cf;font-weight:600}
.col .fr{font-family:monospace;color:#999;font-size:11px}
img{width:100%;background:#0a0a0a;display:block;margin-top:4px}
.tag{font-size:10px;color:#888;margin-top:6px}.tag b{color:#6cf}
.ck{flex-direction:row;gap:5px;align-items:center}</style>
<div class=bar>
 <label>KEY_THR<input id=KEY_THR type=range min=0 max=160 step=1 value=120 oninput=upd()></label>
 <label>CSUB<input id=CSUB type=range min=0.3 max=1.3 step=0.01 value=0.5 oninput=upd()></label>
 <label>TFLOOR<input id=TFLOOR type=range min=0.01 max=0.5 step=0.01 value=0.01 oninput=upd()></label>
 <label>FILL_K<input id=FILL_K type=range min=5 max=51 step=2 value=15 oninput=upd()></label>
 <label>SCORE_GATE<input id=SCORE_GATE type=range min=0 max=0.95 step=0.05 value=0.45 oninput=upd()></label>
 <label>FEATHER<input id=FEATHER type=range min=0 max=12 step=1 value=0 oninput=upd()></label>
 <label class=ck><input id=DONOR type=checkbox checked onchange=upd()>flourish fill</label>
 <label class=ck><input id=HL type=checkbox checked onchange=upd()>show fill</label>
 <button onclick=render()>Render all</button>
 <span id=vals></span><span id=status></span>
</div>
<div class=cols id=cols></div>
<script>
const SUBJECTS=__SUBJECTS__;
const pids=['KEY_THR','CSUB','TFLOOR','FILL_K','SCORE_GATE','FEATHER'];
function pvals(){let o={};pids.forEach(i=>o[i]=document.getElementById(i).value);return o}
function cks(q){q.set('DONOR',document.getElementById('DONOR').checked?1:0);q.set('HL',document.getElementById('HL').checked?1:0);return q}
let preTimer=null;
function upd(){let v=pvals();document.getElementById('vals').textContent=
  `KEY_THR=${v.KEY_THR} CSUB=${v.CSUB} TFLOOR=${v.TFLOOR} FILL_K=${v.FILL_K} GATE=${v.SCORE_GATE} FEATHER=${v.FEATHER}`;
  clearTimeout(preTimer);preTimer=setTimeout(()=>SUBJECTS.forEach((_,i)=>prefetch(i)),140)}
function preq(i){let q=cks(new URLSearchParams(pvals()));q.set('s',i);q.set('f',document.getElementById('f'+i).value);return q}
async function prefetch(i){let r=await fetch('/pre?'+preq(i));
  document.getElementById('pre'+i).src='data:image/png;base64,'+(await r.json()).data}
function fidx(){return SUBJECTS.map((_,i)=>+document.getElementById('f'+i).value)}
document.getElementById('cols').innerHTML=SUBJECTS.map((s,i)=>{
  const d=Math.floor(s.n*0.30);
  return `<div class=col><h3>${s.name}</h3>
   <input id=f${i} type=range min=0 max=${s.n-1} value=${d} oninput=scrub(${i})>
   <span class=fr id=fr${i}></span>
   <div class=tag>raw (scrub to the spawn-in / dip frame)</div><img id=raw${i}>
   <div class=tag><b>pre-darken</b> — green=inpaint, magenta=flourish fill (live)</div><img id=pre${i}>
   <div class=tag>matte</div><img id=matte${i}>
   <div class=tag>flourish donor: <b id=dl${i}></b> ${s.nd?'— scrub to a clean, text-free, level frame':'(none)'}</div>
   <input id=d${i} type=range min=0 max=${Math.max(0,s.nd-1)} value=${Math.max(0,s.di)} ${s.nd?'':'disabled'} oninput=donor(${i})>
   <img id=dimg${i} style=width:45%>
   </div>`}).join('');
async function scrub(i){
  document.getElementById('fr'+i).textContent='frame '+document.getElementById('f'+i).value;
  let r=await fetch(`/frame?s=${i}&f=${document.getElementById('f'+i).value}`);
  document.getElementById('raw'+i).src='data:image/png;base64,'+(await r.json()).data;
  prefetch(i);
}
async function donor(i){
  let d=+document.getElementById('d'+i).value;
  document.getElementById('dl'+i).textContent='d'+String(d+1).padStart(3,'0');
  document.getElementById('status').textContent=' …matting donor';
  let j=await (await fetch(`/donor?s=${i}&d=${d}`)).json();
  if(j.data) document.getElementById('dimg'+i).src='data:image/png;base64,'+j.data;
  document.getElementById('status').textContent=' donor set';
  prefetch(i);
}
async function render(){
  let q=cks(new URLSearchParams(pvals()));
  q.set('f',fidx().join(','));
  document.getElementById('status').textContent=' …rendering';
  let j=await (await fetch('/render?'+q)).json();
  j.results.forEach((r,i)=>{
    document.getElementById('pre'+i).src='data:image/png;base64,'+r.pre;
    document.getElementById('matte'+i).src='data:image/png;base64,'+r.matte;
  });
  document.getElementById('status').textContent=' done';
}
upd();
SUBJECTS.forEach((s,i)=>{
  document.getElementById('dl'+i).textContent = s.di>=0 ? 'd'+String(s.di+1).padStart(3,'0') : '—';
  fetch('/donorimg?s='+i).then(r=>r.json()).then(j=>{if(j.data)document.getElementById('dimg'+i).src='data:image/png;base64,'+j.data});
  scrub(i);
});
render();
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
            meta = json.dumps([{"name": s["name"], "n": len(s["paths"]),
                                "nd": len(s["dpaths"]), "di": s["donor_i"]} for s in SUBJECTS])
            self._send(200, "text/html", HTML.replace("__SUBJECTS__", meta).encode())
        elif u.path == "/frame":
            s, f = int(q["s"][0]), int(q["f"][0])
            self._send(200, "application/json", json.dumps({"data": _png_b64(cv2.imread(SUBJECTS[s]["paths"][f]))}).encode())
        elif u.path == "/pre":
            s, f = int(q["s"][0]), int(q["f"][0])
            pre = _pre_disp(s, cv2.imread(SUBJECTS[s]["paths"][f]), _parse(q))
            self._send(200, "application/json", json.dumps({"data": _png_b64(pre)}).encode())
        elif u.path == "/render":
            p = _parse(q)
            fidx = [int(x) for x in q["f"][0].split(",")]
            results = [_render_subject(i, cv2.imread(s["paths"][fidx[i]]), p) for i, s in enumerate(SUBJECTS)]
            self._send(200, "application/json", json.dumps({"results": results}).encode())
        elif u.path == "/donor":                                # re-matte a chosen flourish donor frame
            si, di = int(q["s"][0]), int(q["d"][0])
            draw = _set_donor(SUBJECTS[si], di)
            self._send(200, "application/json", json.dumps(
                {"idx": SUBJECTS[si]["donor_i"], "data": _png_b64(draw) if draw is not None else ""}).encode())
        elif u.path == "/donorimg":                             # current donor frame (no re-matte)
            d = SUBJECTS[int(q["s"][0])]["fl_draw"]
            self._send(200, "application/json", json.dumps({"data": _png_b64(d) if d is not None else ""}).encode())
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    base, names = sys.argv[1], sys.argv[2:]
    for name in names:
        paths = sorted(glob.glob(f"{base}/loopframes/{name}/*.png"))
        if not paths:
            print("  (no frames)", name, flush=True); continue
        P_clip = np.median(np.stack([cv2.imread(p).astype(np.float32) for p in paths[::3]]), axis=0)
        text = _text_mask(P_clip)
        dpaths = sorted(glob.glob(f"{base}/donors/{name}/*.png"))
        s = {"name": name, "paths": paths, "text": text,
             "M": cv2.dilate(text.astype(np.uint8), np.ones((3, 21), np.uint8)).astype(bool),
             "dpaths": dpaths, "fl_alpha": None, "fl_draw": None, "fl_gray": None, "donor_i": -1}
        _set_donor(s, _pick_default_donor(dpaths))             # held-before-fade guess (overridable)
        SUBJECTS.append(s)
        tag = f"flourish=d{s['donor_i'] + 1:03d} of {len(dpaths)}" if s["donor_i"] >= 0 else "NO DONORS — inpaint only"
        print(f"  {name}: {len(paths)} frames, text px={int(text.sum())}, {tag}", flush=True)
    print(f"BLANK badge px={int(BADGE.sum())}", flush=True)
    print(f"open  http://127.0.0.1:{PORT}/   ({len(SUBJECTS)} subjects)   Ctrl-C to stop", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
