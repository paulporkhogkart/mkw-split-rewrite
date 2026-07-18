"""A/B eye-test lab: encode sample combos across recipe variants, emit a static page.

  python tools/asset_matte/build_ab_lab.py --src D:\\kartoff\\asset_chips \\
      --out D:\\kartoff\\asset_chips\\ab_lab
Open <out>/index.html in a real browser (file://). Paul's pick locks the recipe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tools.asset_matte import build_site_pack as bsp              # noqa: E402
from tools.asset_matte import site_pack as sp                     # noqa: E402

VARIANTS = [
    {"id": "candidate", "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 5},
    {"id": "fps60",     "scale": 0.2, "fps": 60, "quality": 60, "alpha_bits": 5},
    {"id": "q75",       "scale": 0.2, "fps": 30, "quality": 75, "alpha_bits": 5},
    {"id": "q50",       "scale": 0.2, "fps": 30, "quality": 50, "alpha_bits": 5},
    {"id": "alpha8",    "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 8},
    {"id": "alpha4",    "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 4},
    {"id": "scale015",  "scale": 0.15, "fps": 30, "quality": 60, "alpha_bits": 5},
]
DEFAULT_COMBOS = ["baby_daisy__base__b_dasher", "bowser__base__bowser_bruiser",
                  "mario__base", "king_boo__base"]
INK_RING = ("filter:drop-shadow(1px 0 0 #101114) drop-shadow(-1px 0 0 #101114) "
            "drop-shadow(0 1px 0 #101114) drop-shadow(0 -1px 0 #101114)")


def _stepper_source() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "lib", "chipSheet.js")
    return re.sub(r"^export ", "", open(p, encoding="utf-8").read(), flags=re.M)


def render_html(combos, variants, manifests, sizes, stepper_js) -> str:
    stepper_js = stepper_js.replace("export ", "")
    data = json.dumps({"combos": combos,
                       "variants": [v["id"] for v in variants],
                       "manifests": manifests,
                       "sizes": {"|".join(k): v for k, v in sizes.items()}})
    # Preload hints double as a literal manifest of every sheet the page will
    # request (variant/combo/anim), so the eye-test never stalls on a cold fetch.
    preloads = "\n".join(
        f'<link rel="preload" as="image" '
        f'href="packs/{variant}/chips/{combo}__{anim}.webp">'
        for (variant, combo, anim) in sizes)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>chip pack A/B lab</title>
{preloads}
<style>
 body{{background:#0b0c0e;color:#f3f4f6;font:13px system-ui;padding:20px}}
 h2{{font-size:13px;margin:26px 0 8px;color:#9a9ca1}}
 .grid{{display:flex;gap:14px;flex-wrap:wrap}}
 .cell{{text-align:center}}
 /* Chips render on CANVAS: drawImage with an integral source rect into a constant
    destination rect, CSS-downscaled as one texture (same path as an <img>). Any
    moving background-position gets pixel-snapped per paint and jitters horizontally
    as columns cycle — measured: the sheet PIXELS are shift-free (dx spread 0.03px,
    same as animated webp), the shake was pure paint snapping. */
 .chip{{{INK_RING};margin:0 auto;display:block}}
 .lbl{{color:#6b6d73;font-size:10px;margin-top:4px}}
 button{{font-size:10px;margin:2px}}
 .silwrap{{position:relative;width:120px;height:126px;background:#191a1d}}
 .silwrap img{{position:absolute;inset:0;width:100%;height:100%}}
</style></head><body>
<div id="root"></div>
<script>
{stepper_js}
const DATA = {data};
// One shared rAF ticks every player; cells register {{el, player, fw, fh, scaleCss}}.
const cells = [];
function addCell(root, variant, combo, cssH) {{
  const man = DATA.manifests[variant]; if (!man || !man.combos[combo]) return;
  const entry = man.combos[combo];
  const wrap = document.createElement("div"); wrap.className = "cell";
  const s = cssH / man.fh;
  // Native-resolution canvas, CSS-downscaled as one texture (the <img> path).
  const el = document.createElement("canvas"); el.className = "chip";
  el.width = man.fw; el.height = man.fh;
  el.style.width = man.fw * s + "px"; el.style.height = man.fh * s + "px";
  // Sheets pinned as ImageBitmaps: a bare Image's decoded pixels live in Chrome's
  // evictable decode cache, and with ~100 large sheets on this page drawImage kept
  // triggering synchronous re-decodes (global jank). createImageBitmap decodes ONCE
  // into a GPU-backed bitmap; draws are pure texture copies. Until a bitmap is
  // ready a draw skips, holding the previous canvas frame -> still no blank flash.
  const bmps = {{}};
  for (const n of Object.keys(entry.anims)) {{
    const im = new Image();
    im.src = `packs/${{variant}}/chips/${{combo}}__${{n}}.webp`;
    im.decode().then(() => createImageBitmap(im))
      .then((b) => {{ bmps[n] = b; }})
      .catch(() => console.warn("sheet decode failed", im.src));
  }}
  const player = createChipPlayer({{entry, fps: man.fps, fw: man.fw, fh: man.fh}});
  cells.push({{el, ctx: el.getContext("2d"), bmps, player, man, entry, last: "", variant, combo}});
  const kb = (n, f) => {{ const b = document.createElement("button"); b.textContent = n; b.onclick = f; return b; }};
  wrap.append(el, kb("SELECT", () => player.select()), kb("CONFIRM", () => player.confirm()));
  const idleKB = (DATA.sizes[[variant, combo, "idle"].join("|")] / 1024) | 0;
  const lbl = document.createElement("div"); lbl.className = "lbl";
  lbl.textContent = `${{variant}} · idle ${{idleKB}}KB`;
  wrap.append(lbl); root.append(wrap);
}}
function tickAll(t) {{
  for (const c of cells) {{
    const st = c.player.tick(t);
    const key = st.anim + ":" + st.frame;
    if (key === c.last) continue;                       // same frame, skip redraw
    const bmp = c.bmps[st.anim];
    if (!bmp) continue;                                 // bitmap not ready: hold last frame
    const a = c.entry.anims[st.anim];
    const r = frameRect(st.frame, a.cols, c.man.fw, c.man.fh);
    c.ctx.clearRect(0, 0, c.man.fw, c.man.fh);
    c.ctx.drawImage(bmp, r.sx, r.sy, c.man.fw, c.man.fh, 0, 0, c.man.fw, c.man.fh);
    c.last = key;
  }}
  requestAnimationFrame(tickAll);
}}
const root = document.getElementById("root");
for (const combo of DATA.combos) {{
  const h = document.createElement("h2"); h.textContent = combo; root.append(h);
  const g = document.createElement("div"); g.className = "grid"; root.append(g);
  for (const v of DATA.variants) addCell(g, v, combo, 112);
  const ref = document.createElement("div"); ref.className = "cell";
  ref.innerHTML = `<img style="height:112px;{INK_RING}" src="animref/${{combo}}__idle.webp"><div class="lbl">animref (animated webp)</div>`;
  g.append(ref);
  const h2 = document.createElement("h2"); h2.textContent = combo + " · candidate at card sizes"; root.append(h2);
  const g2 = document.createElement("div"); g2.className = "grid"; root.append(g2);
  addCell(g2, "candidate", combo, 92); addCell(g2, "candidate", combo, 76);
}}
requestAnimationFrame(tickAll);
</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--combos", nargs="*", default=DEFAULT_COMBOS)
    args = ap.parse_args(argv)

    plan = bsp.plan_combos(args.src)
    combos = [c for c in args.combos if c in plan] or sys.exit("no requested combos exist")
    for c in args.combos:
        if c not in plan:
            print(f"warn: {c} not in masters, skipped", file=sys.stderr)

    manifests, sizes = {}, {}
    for v in VARIANTS:
        vout = os.path.join(args.out, "packs", v["id"])
        rc = bsp.main(["--src", args.src, "--out", vout, "--scale", str(v["scale"]),
                       "--fps", str(v["fps"]), "--quality", str(v["quality"]),
                       "--alpha-bits", str(v["alpha_bits"]), "--workers", "4",
                       "--only", *combos])
        if rc:
            return rc
        man = json.load(open(os.path.join(vout, "chips", "manifest.json"), encoding="utf-8"))
        manifests[v["id"]] = man
        for c in combos:
            for anim in man["combos"].get(c, {}).get("anims", {}):
                p = os.path.join(vout, "chips", f"{c}__{anim}.webp")
                sizes[(v["id"], c, anim)] = os.path.getsize(p)

    # animated-webp reference at the candidate recipe (smoothness comparison)
    from PIL import Image
    ref_dir = os.path.join(args.out, "animref")
    os.makedirs(ref_dir, exist_ok=True)
    cand = VARIANTS[0]
    step = sp.subsample_step(cand["fps"])
    for c in combos:
        d = os.path.join(args.src, "matte", f"{c}__idle_frames")
        files = sorted(f for f in os.listdir(d) if f.endswith(".png"))[::step]
        fw, fh = sp.encode_size(*Image.open(os.path.join(d, files[0])).size, cand["scale"])
        frames = [sp.quant_alpha(sp.premul_resize(Image.open(os.path.join(d, f)).convert("RGBA"),
                                                  (fw, fh)), cand["alpha_bits"]) for f in files]
        frames[0].save(os.path.join(ref_dir, f"{c}__idle.webp"), save_all=True,
                       append_images=frames[1:], duration=round(1000 / cand["fps"]),
                       loop=0, quality=cand["quality"], method=4)

    # placeholder sils for visual comparison (best effort)
    sil_dir = os.path.join(args.out, "placeholder_sil")
    os.makedirs(sil_dir, exist_ok=True)
    for k in range(4):
        rel = f"docs/design/site-redesign/sil/paul__idle_k{k}.png"
        try:
            data = subprocess.run(["git", "show", f"site-redesign-p1:{rel}"],
                                  capture_output=True, check=True).stdout
            open(os.path.join(sil_dir, f"paul__idle_k{k}.png"), "wb").write(data)
        except subprocess.CalledProcessError:
            print(f"warn: placeholder sil {rel} unavailable", file=sys.stderr)
            break

    html = render_html(combos, VARIANTS, manifests, sizes, _stepper_source())
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"lab ready: {os.path.join(args.out, 'index.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
