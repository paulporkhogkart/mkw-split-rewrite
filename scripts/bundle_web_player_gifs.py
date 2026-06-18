"""Bundle per-player popup GIFs into web/public/players/ from assets/player_figures.json.
  <player>.gif       = the 'online' (posted) source gif, with its NETSCAPE loop block
                       stripped so it PLAYS ONCE (lossless - frames/palette untouched).
  <player>__fire.gif = the 'onpace' source gif, copied as-is (sources loop=0 -> LOOPS).
Run:  python scripts/bundle_web_player_gifs.py
"""
import os, json

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "assets", "player_gifs")
OUT = os.path.join(ROOT, "web", "public", "players")
MANIFEST = os.path.join(ROOT, "assets", "player_figures.json")
NETSCAPE = b"\x21\xFF\x0BNETSCAPE2.0"   # app-ext: 0x21 0xFF, len 11, "NETSCAPE2.0"

def copy_loop(src, out):
    with open(src, "rb") as f, open(out, "wb") as o:
        o.write(f.read())

def copy_once(src, out):
    data = open(src, "rb").read()
    i = data.find(NETSCAPE)
    if i >= 0:                       # remove marker(14) + 0x03 0x01 + loop(2) + 0x00 = 19 bytes
        data = data[:i] + data[i + 19:]
    open(out, "wb").write(data)

def main():
    os.makedirs(OUT, exist_ok=True)
    man = json.load(open(MANIFEST, encoding="utf-8"))
    for name, st in man.items():
        online = st["online"][0]
        onpace = st.get("onpace", st["online"])[0]
        copy_once(os.path.join(SRC, online), os.path.join(OUT, f"{name}.gif"))
        copy_loop(os.path.join(SRC, onpace), os.path.join(OUT, f"{name}__fire.gif"))
        print("bundled", name, "<-", online, "(once) /", onpace, "(loop)")

if __name__ == "__main__":
    main()
