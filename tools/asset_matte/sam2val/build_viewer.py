"""Build a self-contained index.html scrubber over view/<subject>__<seg>/. Subject dropdown,
biref<->sam2 toggle, frame slider (swaps the <img> src), and the anchor-diff image. Stdlib only.

  python tools/asset_matte/sam2val/build_viewer.py
"""
import glob
import json
import os

OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")


def main():
    view = os.path.join(OUT, "view")
    segs = {}
    for d in sorted(glob.glob(os.path.join(view, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        n = len(glob.glob(os.path.join(d, "biref", "*.png")))
        if n:
            segs[name] = n
    data = json.dumps(segs)
    html = """<!doctype html><meta charset=utf-8><title>SAM2 vs birefnet anchor</title>
<style>
 body{margin:0;background:#1a1a1a;color:#ddd;font:13px system-ui}
 header{padding:8px 12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 select,button{font:13px system-ui;padding:3px 8px}
 button.on{background:#3a7;color:#000}
 #stage{display:flex;gap:12px;padding:0 12px 12px;align-items:flex-start}
 #stage img{background:#000;max-height:78vh;image-rendering:pixelated}
 #anchors{max-height:78vh}
 .col{display:flex;flex-direction:column;gap:4px}
 label{opacity:.7}
</style>
<header>
 <select id=subj></select>
 <button id=tgl class=on>anchor: SAM2</button>
 <input id=frame type=range min=0 value=0 style=width:340px>
 <span id=fnum></span>
 <label>(toggle compares the two mattes on the same frame; right = anchor diff)</label>
</header>
<div id=stage>
 <div class=col><label id=mlabel></label><img id=matte></div>
 <div class=col><label>anchors: red=birefnet-only  green=SAM2-only  yellow=both</label><img id=anchors></div>
</div>
<script>
const SEGS=__DATA__;
const names=Object.keys(SEGS);
const subj=document.getElementById('subj'), tgl=document.getElementById('tgl'),
      frame=document.getElementById('frame'), fnum=document.getElementById('fnum'),
      matte=document.getElementById('matte'), anchors=document.getElementById('anchors'),
      mlabel=document.getElementById('mlabel');
let anchor='sam2';
names.forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n+' ('+SEGS[n]+'f)';subj.appendChild(o);});
function pad(i){return String(i).padStart(3,'0');}
function render(){
 const n=subj.value, i=+frame.value;
 matte.src=n+'/'+anchor+'/'+pad(i)+'.png';
 anchors.src=n+'/anchors.png';
 fnum.textContent=i+' / '+(SEGS[n]-1);
 mlabel.textContent='matte anchor = '+anchor;
}
function selectSeg(){frame.max=SEGS[subj.value]-1; if(+frame.value>frame.max)frame.value=0; render();}
subj.onchange=selectSeg;
frame.oninput=render;
tgl.onclick=()=>{anchor=(anchor==='sam2')?'biref':'sam2'; tgl.textContent='anchor: '+(anchor==='sam2'?'SAM2':'birefnet'); tgl.classList.toggle('on',anchor==='sam2'); render();};
subj.value=names[0]; selectSeg();
</script>
""".replace("__DATA__", data)
    path = os.path.join(view, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", path, "segments:", len(segs), flush=True)


if __name__ == "__main__":
    main()
