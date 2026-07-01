"""Generate a standalone HTML viewer for matted chip segments (spawn-in / idle-loop / flourish).

The batch (process_all.py) writes <base>__spawn_frames/, <base>__idle_frames/, <base>__flourish_frames/
per combo. This builds one `index.html` next to them: a JS frame-player with the full select-screen
state machine -- spawn-in plays once into the looping idle, Flourish (queued or immediate) plays the
flourish then optionally continues idling -- plus per-frame scrub/step/speed for seam inspection, a
wheel-zoom-at-cursor + drag-pan, and a background toggle. char/kart selectors cycle the whole set.
No server needed (open the file). Build python (stdlib only).

  python tools/asset_matte/make_viewer.py --matte temp/asset_chips_dark_full_seg/matte --title "dark baby_daisy (full)"
"""
import argparse
import glob
import json
import os

_CSS = """
:root { color-scheme: dark; --w: 300px; }
* { box-sizing: border-box; }
body { margin:0; height:100vh; display:flex; flex-direction:column;
       background:#0e0e10; color:#e6e6e6; font:13px/1.4 ui-sans-serif,system-ui,sans-serif; }
.bar { padding:8px 13px; background:#17171a; border-bottom:1px solid #2a2a2e;
       display:flex; gap:9px; align-items:center;
       flex-wrap:nowrap; white-space:nowrap; overflow-x:auto; flex-shrink:0; }
.bar.b2 { background:#131316; }
button, select { font:inherit; background:#2a2a30; color:#e6e6e6; border:1px solid #3a3a42;
         border-radius:6px; padding:6px 10px; cursor:pointer; }
button:hover:not(:disabled) { background:#34343c; }
button:disabled { opacity:.4; cursor:default; }
button.on { background:#2e5b3a; border-color:#5ed28a; }
#btnSpawn{ border-color:#5b8fc7; } #btnFlour{ border-color:#c75b8f; } #btnFlourNow{ border-color:#b06a2e; }
label.f { display:flex; gap:5px; align-items:center; color:#9a9aa2; }
#veh { font-size:15px; font-weight:700; color:#e9b873; }
#state { color:#5ed28a; }
#frameLbl { font:13px ui-monospace,monospace; color:#e9b873; min-width:190px; }
#zoomLbl { font:13px ui-monospace,monospace; color:#9a9aa2; min-width:46px; text-align:right; }
.seam { color:#5ed28a; font-weight:700; }
.spacer { flex:1; }
.dim { color:#7a7a82; }
kbd { background:#26262c; border:1px solid #3a3a42; border-bottom-width:2px; border-radius:4px;
      padding:0 5px; font:12px ui-monospace,monospace; color:#b9b9c2; }
#scrub { flex:1; min-width:220px; }
#stage { flex:1; min-height:0; position:relative; overflow:hidden; cursor:grab; }
#wrap { position:absolute; inset:0; transform-origin:0 0; will-change:transform; }
#wrap img { width:100%; height:100%; object-fit:contain; display:block;
            user-select:none; -webkit-user-drag:none; }
.bg-checker { background-color:#c9c9cf;
  background-image:
    linear-gradient(45deg,#9a9aa2 25%,transparent 25%), linear-gradient(-45deg,#9a9aa2 25%,transparent 25%),
    linear-gradient(45deg,transparent 75%,#9a9aa2 75%), linear-gradient(-45deg,transparent 75%,#9a9aa2 75%);
  background-size:26px 26px; background-position:0 0,0 13px,13px -13px,-13px 0; }
.bg-dark { background:#15151a; } .bg-light { background:#ececf0; }
.bg-magenta { background:#c026d3; } .bg-green { background:#00c000; }
"""

_JS = r"""
const COMBOS = window.COMBOS;
const $ = id => document.getElementById(id);
const pad = x => String(x).padStart(3,"0");
const BGS = ["bg-checker","bg-dark","bg-light","bg-magenta","bg-green"];
const MS0 = 1000/60;
const S = { SPAWN:"spawning in…", IDLE:"idle (looping)", QUEUED:"flourish queued — finishing the loop…",
            FLOUR:"flourish (playing)", FROZEN:"flourish — frozen on last frame", SCRUB:"paused — scrubbing" };

let cur=null, tl=[], segs=[], total=0, state=null, raf=null, paused=false, flourPending=false;
let curFrame=0, bg=0, speed=1, continueIdle=false;
let scale=1, tx=0, ty=0, dragging=false, lastX=0, lastY=0;
const img=$("img"), stage=$("stage"), wrap=$("wrap");

const chars=[...new Set(COMBOS.map(c=>c.char))];
const kartsFor=ch=>COMBOS.filter(c=>c.char===ch).map(c=>c.kart);
const findCombo=(ch,k)=>COMBOS.find(c=>c.char===ch&&c.kart===k);
function fillChars(){ $("charSel").innerHTML=chars.map(c=>`<option>${c}</option>`).join(""); }
function fillKarts(ch){ $("kartSel").innerHTML=kartsFor(ch).map(k=>`<option>${k}</option>`).join(""); }

const segByKind = k => segs.find(s=>s.kind===k);
const range=(a,b)=>{ const r=[]; for(let i=a;i<b;i++) r.push(i); return r; };
const segRange = k => { const s=segByKind(k); return s?range(s.off,s.off+s.n):[]; };
function segOf(g){ for(const s of segs) if(g>=s.off && g<s.off+s.n) return s.kind; return ""; }

function buildTimeline(c){
  tl=[]; segs=[]; let off=0;
  for(const k of ["spawn","idle","flourish"]){
    if(!c[k]) continue;
    for(let i=0;i<c[k].n;i++) tl.push(`${c[k].dir}/${pad(i)}.png`);
    segs.push({kind:k, off, n:c[k].n}); off+=c[k].n;
  }
  total=off;
}
function preload(){ tl.forEach(s=>{ const im=new Image(); im.src=s; }); }

function applyZoom(){ wrap.style.transform=`translate(${tx}px,${ty}px) scale(${scale})`; $("zoomLbl").textContent=scale.toFixed(1)+"x"; }
function resetZoom(){ scale=1; tx=0; ty=0; applyZoom(); }
function zoomAt(cx,cy,f){ const ns=Math.min(40,Math.max(0.2,scale*f));
  tx=cx-(cx-tx)*(ns/scale); ty=cy-(cy-ty)*(ns/scale); scale=ns; applyZoom(); }

function frame(g){ curFrame=g; img.src=tl[g]; $("scrub").value=g;
  const k=segOf(g), s=segByKind(k), seam=(k==="idle" && (g===s.off || g===s.off+s.n-1));
  $("frameLbl").innerHTML=`${k} ${g-(s?s.off:0)}/${(s?s.n-1:0)} · g${g}/${total-1}`
    +(seam?` <span class=seam>&#8629; loop seam</span>`:``); }
function stopRaf(){ if(raf){cancelAnimationFrame(raf);raf=null;} }
function setState(s){ state=s; $("state").textContent=s;
  $("btnFlour").disabled = !segByKind("flourish") || s!==S.IDLE;
  $("btnFlourNow").disabled = !segByKind("flourish");
  $("btnSpawn").disabled = !segByKind("spawn"); }
function setPlaying(){ paused=false; $("btnPause").textContent="⏸ Pause"; }

function play(frames, loop, onDone){
  stopRaf(); setPlaying(); let i=0, prev=performance.now(), acc=0;
  (function tick(now){ acc+=now-prev; prev=now; const MS=MS0/speed;
    while(acc>=MS){ acc-=MS;
      if(i>=frames.length){ if(loop) i=0; else { frame(frames[frames.length-1]); raf=null; if(onDone)onDone(); return; } }
      frame(frames[i++]); }
    raf=requestAnimationFrame(tick); })(performance.now());
}
function startIdle(){
  const frames=segRange("idle"); flourPending=false; stopRaf(); setPlaying();
  let i=0, prev=performance.now(), acc=0;
  (function tick(now){ acc+=now-prev; prev=now; const MS=MS0/speed;
    while(acc>=MS){ acc-=MS; frame(frames[i]);
      if(flourPending && i===frames.length-1){ raf=null; doFlourNow(); return; }
      i=(i+1)%frames.length; }
    raf=requestAnimationFrame(tick); })(performance.now());
  setState(S.IDLE);
}
function doFlour(){ if(state!==S.IDLE||!segByKind("flourish")) return; flourPending=true; setState(S.QUEUED); $("btnFlour").disabled=true; }
function doFlourNow(){ if(!segByKind("flourish")) return; flourPending=false; setState(S.FLOUR);
  play(segRange("flourish"), false, ()=>{ continueIdle?startIdle():setState(S.FROZEN); }); }
function replaySpawn(){ if(segByKind("spawn")){ setState(S.SPAWN); play(segRange("spawn"), false, startIdle); } else startIdle(); }
function pause(){ stopRaf(); paused=true; $("btnPause").textContent="▶ Play"; setState(S.SCRUB); }
function step(d){ if(!paused)pause(); frame(Math.max(0,Math.min(total-1,curFrame+d))); }

function loadCombo(c){ cur=c; $("veh").textContent=`${c.char} · ${c.kart}`;
  buildTimeline(c); preload(); $("scrub").max=total-1; curFrame=0; applyZoom(); replaySpawn(); }
function onPick(){ const c=findCombo($("charSel").value,$("kartSel").value); if(c)loadCombo(c); }
function syncTo(c){ $("charSel").value=c.char; fillKarts(c.char); $("kartSel").value=c.kart; }
function jumpCombo(d){ const i=COMBOS.indexOf(cur), n=COMBOS[(i+d+COMBOS.length)%COMBOS.length]; syncTo(n); loadCombo(n); }

$("charSel").onchange=()=>{ fillKarts($("charSel").value); onPick(); };
$("kartSel").onchange=onPick;
$("btnSpawn").onclick=replaySpawn;
$("btnFlour").onclick=doFlour;
$("btnFlourNow").onclick=doFlourNow;
$("btnIdle").onclick=startIdle;
$("btnCont").onclick=()=>{ continueIdle=!continueIdle;
  $("btnCont").textContent="↻ continue idle: "+(continueIdle?"on":"off"); $("btnCont").classList.toggle("on",continueIdle); };
$("btnRand").onclick=()=>{ let c; do{c=COMBOS[Math.floor(Math.random()*COMBOS.length)];}while(COMBOS.length>1&&c===cur); syncTo(c); loadCombo(c); };
$("btnPrevC").onclick=()=>jumpCombo(-1);
$("btnNextC").onclick=()=>jumpCombo(1);
$("btnBg").onclick=()=>{ bg=(bg+1)%BGS.length; stage.className=BGS[bg]; };
$("btnPause").onclick=()=>{ paused?startIdle():pause(); };
$("btnPrev").onclick=()=>step(-1);
$("btnNext").onclick=()=>step(1);
$("scrub").oninput=()=>{ pause(); frame(+$("scrub").value); };
$("speedSel").onchange=()=>{ speed=+$("speedSel").value; };
$("btnReset").onclick=resetZoom;
$("btnZin").onclick=()=>zoomAt(stage.clientWidth/2,stage.clientHeight/2,1.3);
$("btnZout").onclick=()=>zoomAt(stage.clientWidth/2,stage.clientHeight/2,1/1.3);

stage.onwheel=e=>{ e.preventDefault(); const r=stage.getBoundingClientRect();
  zoomAt(e.clientX-r.left, e.clientY-r.top, e.deltaY<0?1.15:1/1.15); };
stage.onmousedown=e=>{ dragging=true; lastX=e.clientX; lastY=e.clientY; stage.style.cursor="grabbing"; };
window.onmouseup=()=>{ dragging=false; stage.style.cursor="grab"; };
window.onmousemove=e=>{ if(!dragging)return; tx+=e.clientX-lastX; ty+=e.clientY-lastY; lastX=e.clientX; lastY=e.clientY; applyZoom(); };
stage.ondblclick=resetZoom;

document.onkeydown=e=>{
  if(e.target.tagName==="SELECT")return;
  const k=e.key.toLowerCase();
  if(e.key==="ArrowLeft"){step(-1);e.preventDefault();}
  else if(e.key==="ArrowRight"){step(1);e.preventDefault();}
  else if(e.key===" "){paused?startIdle():pause();e.preventDefault();}
  else if(e.key==="["){jumpCombo(-1);e.preventDefault();}
  else if(e.key==="]"){jumpCombo(1);e.preventDefault();}
  else if(k==="f"){doFlour();}
  else if(k==="n"){doFlourNow();}
  else if(k==="p"){replaySpawn();}
  else if(k==="i"){startIdle();}
  else if(k==="c"){$("btnCont").click();}
  else if(k==="b"){$("btnBg").click();}
  else if(e.key==="+"||e.key==="="){zoomAt(stage.clientWidth/2,stage.clientHeight/2,1.3);}
  else if(e.key==="-"||e.key==="_"){zoomAt(stage.clientWidth/2,stage.clientHeight/2,1/1.3);}
  else if(e.key==="0"){resetZoom();} };

fillChars(); fillKarts(chars[0]); loadCombo(COMBOS[0]); syncTo(COMBOS[0]);
"""


def main():
    ap = argparse.ArgumentParser(description="Build an HTML player of matted spawn/idle/flourish segments.")
    ap.add_argument("--matte", required=True, help="dir with <base>__<seg>_frames/ sequences")
    ap.add_argument("--out", default=None, help="output html (default <matte>/index.html)")
    ap.add_argument("--title", default="chip viewer")
    a = ap.parse_args()

    matte = os.path.abspath(a.matte)
    out = os.path.abspath(a.out) if a.out else os.path.join(matte, "index.html")
    outdir = os.path.dirname(out)

    def count(d):
        return len(glob.glob(os.path.join(d, "*.png")))

    combos = []
    for idle_dir in sorted(glob.glob(os.path.join(matte, "*__idle_frames"))):
        base = os.path.basename(idle_dir)[:-len("__idle_frames")]
        entry = {}
        for seg in ("spawn", "idle", "flourish"):
            d = os.path.join(matte, f"{base}__{seg}_frames")
            n = count(d)
            if n:
                entry[seg] = {"dir": os.path.relpath(d, outdir).replace(os.sep, "/"), "n": n}
        if "idle" not in entry:
            continue
        parts = base.split("__")
        char, kart = ("__".join(parts[:2]), "__".join(parts[2:])) if len(parts) >= 3 else (base, "(standing)")
        entry.update({"name": base, "char": char, "kart": kart})
        combos.append(entry)
    if not combos:
        print(f"no <base>__idle_frames/ sequences found in {matte}", flush=True)
        return

    doc = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{a.title}</title><style>{_CSS}</style></head><body>
<div class=bar>
  <label class=f>char <select id=charSel></select></label>
  <label class=f>kart <select id=kartSel></select></label>
  <button id=btnSpawn>&#9654; Spawn-in</button>
  <button id=btnFlour>&#10022; Flourish</button>
  <button id=btnFlourNow>&#9889; now</button>
  <button id=btnCont>&#8635; continue idle: off</button>
  <button id=btnIdle>&#8634; Idle</button>
  <button id=btnPrevC>&#9664;&#9664;</button><button id=btnNextC>&#9654;&#9654;</button>
  <button id=btnRand>&#127922;</button><button id=btnBg>&#9638; bg</button>
  <span id=veh>&mdash;</span> <span id=state>&mdash;</span>
  <span class=spacer></span>
  <span class=dim><kbd>space</kbd> play <kbd>&larr;</kbd><kbd>&rarr;</kbd> step <kbd>f</kbd>/<kbd>n</kbd> flourish
    <kbd>p</kbd> spawn <kbd>[</kbd><kbd>]</kbd> combo · wheel zoom, drag pan</span>
</div>
<div class="bar b2">
  <button id=btnPause>&#9208; Pause</button>
  <button id=btnPrev>&#9664;</button>
  <input id=scrub type=range min=0 max=1 value=0>
  <button id=btnNext>&#9654;</button>
  <span id=frameLbl>&mdash;</span>
  <label class=f>speed <select id=speedSel>
    <option value=0.25>0.25x</option><option value=0.5>0.5x</option>
    <option value=1 selected>1x</option><option value=2>2x</option></select></label>
  <button id=btnZout>&#8722;</button><button id=btnZin>+</button>
  <button id=btnReset>&#8634; fit</button><span id=zoomLbl>1.0x</span>
</div>
<div id=stage class=bg-checker><div id=wrap><img id=img alt=frame></div></div>
<script>window.COMBOS = {json.dumps(combos)};</script>
<script>{_JS}</script>
</body></html>"""

    os.makedirs(outdir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    segc = {k: sum(1 for c in combos if k in c) for k in ("spawn", "idle", "flourish")}
    print(f"wrote {out}  ({len(combos)} combos; segments {segc})", flush=True)


if __name__ == "__main__":
    main()
