"""DEV TOOL: pick per-player figure frames from the 360 gifs in a browser.

Shows every gif in temp/360/, lets you scrub frames slowly (slider / step / play),
and assign an Offline, Online and On-pace frame for each player. Opens seeded with
everyone's CURRENT online/offline picks already selected (from the manifest, or the
legacy heuristic if none yet). Saving writes assets/player_figures.json, copies any
newly-chosen gif into assets/player_gifs/, and regenerates
src/assets/players/<name>__{on,off,onpace}.png via gen_player_figures.

Run:  python scripts/pick_player_figures.py
      python scripts/pick_player_figures.py --no-serve   # build cache + print state, no browser
"""
import os
import sys
import json
import shutil
import filecmp
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image
import gen_player_figures as gen

GIF_DIR = os.path.join(gen.ROOT, "temp", "360")
CACHE = os.path.join(gen.ROOT, "temp", "360_frames")
COMMIT = gen.SRC                      # assets/player_gifs (committed, read by gen)
PLAYERS = list(gen.MAP.keys())
PREVIEW_H = 320                       # cache frame height (display only)


def list_gifs():
    if not os.path.isdir(GIF_DIR):
        sys.exit(f"no gif dir: {GIF_DIR}")
    return sorted(f for f in os.listdir(GIF_DIR) if f.lower().endswith(".gif"))


def player_of(gif):
    s = gif.lower()
    return next((p for p in PLAYERS if s.startswith(p)), None)


def extract_cache():
    """Explode every gif into temp/360_frames/<stem>/<i>.png. -> { gif: frame_count }."""
    counts = {}
    for g in list_gifs():
        stem = g[:-4]
        outdir = os.path.join(CACHE, stem)
        im = Image.open(os.path.join(GIF_DIR, g))
        n = getattr(im, "n_frames", 1)
        counts[g] = n
        done = os.path.join(outdir, ".done")
        if os.path.exists(done) and open(done).read().strip() == str(n):
            continue
        os.makedirs(outdir, exist_ok=True)
        print(f"  caching {g} ({n} frames)")
        for i in range(n):
            im.seek(i)
            fr = im.convert("RGBA")
            if fr.height > PREVIEW_H:
                fr = fr.resize((round(fr.width * PREVIEW_H / fr.height), PREVIEW_H), Image.LANCZOS)
            fr.save(os.path.join(outdir, f"{i}.png"))
        open(done, "w").write(str(n))
    return counts


def default_selection(counts):
    """Seed from the saved manifest; fill any gap with the legacy heuristic pick."""
    man = gen.load_manifest()
    sel = {}
    for p in PLAYERS:
        e = {k: list(v) for k, v in man.get(p, {}).items()}
        on_gif, off_gif = gen.MAP[p]
        if "online" not in e and on_gif in counts:
            e["online"] = [on_gif, gen.heuristic_index(os.path.join(GIF_DIR, on_gif), True)]
        if "offline" not in e and off_gif in counts:
            e["offline"] = [off_gif, gen.heuristic_index(os.path.join(GIF_DIR, off_gif), False)]
        sel[p] = e
    return sel


def build_state():
    counts = extract_cache()
    gifs = [{"name": g, "stem": g[:-4], "player": player_of(g), "frames": counts[g]}
            for g in list_gifs()]
    return {"players": PLAYERS, "gifs": gifs, "selection": default_selection(counts)}


def save_selection(selection):
    """Write manifest, copy chosen gifs into the committed folder, regenerate PNGs."""
    man = {}
    for p, states in selection.items():
        e = {}
        for st in ("online", "offline", "onpace"):
            v = states.get(st)
            if v and v[0]:
                e[st] = [v[0], int(v[1])]
        if e:
            man[p] = e
    os.makedirs(os.path.dirname(gen.MANIFEST), exist_ok=True)
    with open(gen.MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2)

    copied = []
    for e in man.values():
        for gif, _ in e.values():
            src, dst = os.path.join(GIF_DIR, gif), os.path.join(COMMIT, gif)
            if os.path.exists(src) and (not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False)):
                os.makedirs(COMMIT, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(gif)
    gen.main()
    return {"ok": True, "players": list(man), "copied_gifs": sorted(set(copied))}


STATE = None

PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Pick player figures</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;background:#1b1c1e;color:#d9dadd;font:14px/1.5 system-ui,"Segoe UI",sans-serif}
  header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:14px;padding:12px 20px;
         background:#202023;border-bottom:1px solid #34353a}
  header h1{font-size:15px;font-weight:650;margin:0}
  .tabs{display:flex;gap:6px;margin-left:8px}
  .tab{padding:5px 12px;border-radius:5px;background:#26272b;color:#9a9ca1;cursor:pointer;border:1px solid #34353a;font-weight:600}
  .tab.on{background:#3d7cc2;color:#fff;border-color:#3d7cc2}
  .sp{flex:1}
  button.save{padding:7px 16px;border:none;border-radius:5px;background:#5aa86a;color:#fff;font-weight:650;cursor:pointer}
  button.save:disabled{background:#3a4a3d;color:#9a9ca1;cursor:default}
  .status{color:#9a9ca1;font-size:12px;min-width:120px}
  main{padding:18px 20px 60px}
  .slots{display:flex;gap:14px;margin-bottom:22px}
  .slot{flex:0 0 200px;background:#202023;border:1px solid #34353a;border-radius:8px;padding:10px}
  .slot h3{margin:0 0 8px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#9a9ca1}
  .slot .pv{height:150px;display:flex;align-items:flex-end;justify-content:center;background:#161718;border-radius:6px;overflow:hidden}
  .slot .pv img{max-height:100%;max-width:100%}
  .slot .meta{font-size:11px;color:#6b6d73;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .slot.unset .pv{color:#56585e;font-size:12px;align-items:center}
  .gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
  .card{background:#202023;border:1px solid #34353a;border-radius:8px;padding:10px}
  .card .ct{font-size:12px;font-weight:600;display:flex;justify-content:space-between;margin-bottom:6px}
  .card .ct .pl{color:#6b6d73;font-weight:400}
  .frame{display:block;width:100%;height:220px;object-fit:contain;background:#161718;border-radius:6px}
  .ctrls{display:flex;align-items:center;gap:6px;margin:8px 0}
  .ctrls input[type=range]{flex:1}
  .ctrls button,.ctrls select{background:#26272b;color:#d9dadd;border:1px solid #34353a;border-radius:4px;padding:3px 7px;cursor:pointer}
  .fr{font-size:11px;color:#9a9ca1;font-variant-numeric:tabular-nums;min-width:44px;text-align:right}
  .assign{display:flex;gap:6px}
  .assign button{flex:1;background:#26303c;color:#cde1f5;border:1px solid #2d5e94;border-radius:4px;padding:5px;cursor:pointer;font-size:11px}
  .assign button:hover{background:#2d5e94;color:#fff}
  label.all{font-size:12px;color:#9a9ca1;display:flex;align-items:center;gap:5px;margin:0 0 14px}
</style></head><body>
<header>
  <h1>Pick player figures</h1>
  <div class="tabs" id="tabs"></div>
  <div class="sp"></div>
  <span class="status" id="status"></span>
  <button class="save" id="save" disabled onclick="save()">Save</button>
</header>
<main>
  <div class="slots" id="slots"></div>
  <label class="all"><input type="checkbox" id="all" onchange="showAll=this.checked;renderGallery()"> show all players' gifs</label>
  <div class="gallery" id="gallery"></div>
</main>
<script>
let S=null, cur=null, showAll=false, dirty=false;
const idx={}, fps={}, timer={};
const STATES=["offline","online","onpace"];
const stem=n=>n.replace(/\.gif$/i,"");
const url=(n,i)=>`/cache/${stem(n)}/${i}.png`;
const gi=n=>S.gifs.find(g=>g.name===n);

async function load(){
  S=await (await fetch("/api/state")).json();
  cur=S.players[0];
  for(const g of S.gifs){ idx[g.name]=0; fps[g.name]=4; }
  renderTabs(); render();
}
function renderTabs(){
  document.getElementById("tabs").innerHTML=S.players.map(p=>
    `<div class="tab ${p===cur?'on':''}" onclick="cur='${p}';renderTabs();render()">${p}</div>`).join("");
}
function render(){ renderSlots(); renderGallery(); }
function renderSlots(){
  const sel=S.selection[cur]||(S.selection[cur]={});
  document.getElementById("slots").innerHTML=STATES.map(st=>{
    const v=sel[st];
    if(!v) return `<div class="slot unset"><h3>${st}</h3><div class="pv">unset</div><div class="meta">&mdash;</div></div>`;
    return `<div class="slot"><h3>${st}</h3><div class="pv"><img src="${url(v[0],v[1])}"></div>
      <div class="meta">${v[0]} &middot; #${v[1]}</div></div>`;
  }).join("");
}
function renderGallery(){
  const list=S.gifs.filter(g=>showAll||g.player===cur);
  document.getElementById("gallery").innerHTML=list.map(g=>{
    const i=idx[g.name];
    return `<div class="card"><div class="ct"><span>${g.name}</span><span class="pl">${g.player||'?'}</span></div>
      <img class="frame" id="img-${g.stem}" src="${url(g.name,i)}">
      <div class="ctrls">
        <button onclick="step('${g.name}',-1)">&#9664;</button>
        <input type="range" min="0" max="${g.frames-1}" value="${i}" id="sl-${g.stem}" oninput="scrub('${g.name}',+this.value)">
        <button onclick="step('${g.name}',1)">&#9654;</button>
        <button id="pl-${g.stem}" onclick="toggle('${g.name}')">play</button>
        <select onchange="fps['${g.name}']=+this.value"><option>4</option><option>2</option><option>1</option><option>8</option></select>
        <span class="fr" id="fr-${g.stem}">${i}/${g.frames-1}</span>
      </div>
      <div class="assign">
        ${STATES.map(st=>`<button onclick="setSlot('${st}','${g.name}')">Set ${st}</button>`).join("")}
      </div></div>`;
  }).join("");
}
function show(name){
  const g=gi(name), i=idx[name];
  document.getElementById("img-"+g.stem).src=url(name,i);
  document.getElementById("fr-"+g.stem).textContent=i+"/"+(g.frames-1);
  const sl=document.getElementById("sl-"+g.stem); if(sl) sl.value=i;
}
function scrub(name,i){ idx[name]=i; show(name); }
function step(name,d){ const g=gi(name); idx[name]=(idx[name]+d+g.frames)%g.frames; show(name); }
function toggle(name){
  const g=gi(name), btn=document.getElementById("pl-"+g.stem);
  if(timer[name]){ clearInterval(timer[name]); timer[name]=0; btn.textContent="play"; return; }
  btn.textContent="stop";
  timer[name]=setInterval(()=>step(name,1), 1000/(fps[name]||4));
}
function setSlot(st,name){
  (S.selection[cur]||(S.selection[cur]={}))[st]=[name, idx[name]];
  dirty=true; document.getElementById("save").disabled=false;
  document.getElementById("status").textContent="unsaved changes";
  renderSlots();
}
async function save(){
  document.getElementById("status").textContent="saving...";
  const r=await (await fetch("/api/save",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({selection:S.selection})})).json();
  dirty=false; document.getElementById("save").disabled=true;
  document.getElementById("status").textContent= r.ok
    ? ("saved ✓" + (r.copied_gifs.length? " (+"+r.copied_gifs.length+" gif)":"")) : "save failed";
}
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, json.dumps(STATE).encode("utf-8"), "application/json")
        if path.startswith("/cache/"):
            parts = path[len("/cache/"):].split("/")
            if len(parts) == 2 and parts[0].isidentifier() and parts[1].endswith(".png") and parts[1][:-4].isdigit():
                fp = os.path.join(CACHE, parts[0], parts[1])
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "image/png")
            return self._send(404, b"no", "text/plain")
        return self._send(404, b"no", "text/plain")

    def do_POST(self):
        if self.path == "/api/save":
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            try:
                res = save_selection(data.get("selection", {}))
            except Exception as exc:  # noqa: BLE001 - surface to the browser
                res = {"ok": False, "error": str(exc)}
            STATE["selection"] = default_selection({g["name"]: g["frames"] for g in STATE["gifs"]})
            return self._send(200, json.dumps(res).encode("utf-8"), "application/json")
        return self._send(404, b"no", "text/plain")


def main():
    global STATE
    print("building frame cache (first run is slow)...")
    STATE = build_state()
    print(f"  {len(STATE['gifs'])} gifs, players: {', '.join(STATE['players'])}")
    if "--no-serve" in sys.argv:
        print(json.dumps(STATE["selection"], indent=2))
        return
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"\n  picker:  {url}\n  Ctrl+C to stop\n")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
