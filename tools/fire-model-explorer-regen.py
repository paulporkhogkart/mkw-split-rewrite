"""Refresh the PB snapshot embedded in tools/fire-model-explorer.html from the live DB.

Reads pi/mkw.db (read-only), rebuilds the per-course {leader, #1, #2, WR} data for the
active season / 150cc, and rewrites the array between the FIRE_DATA markers in the HTML.
Also prints which courses are on fire at the locked defaults (E0=0.2%, K=4).

    python tools/fire-model-explorer-regen.py
"""
import sqlite3, json, math, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "pi" / "mkw.db"
HTML = ROOT / "tools" / "fire-model-explorer.html"
CC = 150
E0, K = 0.2, 4.0   # locked defaults, for the on-fire report only

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
sid = con.execute("SELECT id FROM seasons WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()["id"]

rows = []
for c in con.execute("SELECT id, display_name FROM courses ORDER BY display_name").fetchall():
    lb = con.execute(
        """SELECT r.total_time_ms t, p.display_name n, p.color
           FROM runs r JOIN players p ON p.id=r.player_id
           WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
           ORDER BY r.total_time_ms ASC""", (sid, c["id"], CC)).fetchall()
    wr = con.execute("SELECT record_ms m FROM world_records WHERE course_id=? AND cc=? AND is_current=1 LIMIT 1",
                     (c["id"], CC)).fetchone()
    if len(lb) < 2 or not wr:
        continue
    rows.append({"course": c["display_name"], "leader": lb[0]["n"], "color": lb[0]["color"] or "#888",
                 "t1": lb[0]["t"], "t2": lb[1]["t"], "wr": wr["m"]})

payload = "var C=" + json.dumps(rows, ensure_ascii=False) + ";"
html = HTML.read_text(encoding="utf-8")
new = re.sub(r"/\*FIRE_DATA_START\*/.*?/\*FIRE_DATA_END\*/",
             "/*FIRE_DATA_START*/" + payload + "/*FIRE_DATA_END*/", html, flags=re.S)
HTML.write_text(new, encoding="utf-8")
print(f"Updated {HTML.name} with {len(rows)} courses (season {sid}, {CC}cc).")

print(f"\nOn fire at locked E0={E0}% K={K}:")
for r in sorted(rows, key=lambda r: (r["t1"]-r["wr"])/r["wr"]):
    off = (r["t1"]-r["wr"])/r["wr"]*100
    lead = (r["t2"]-r["t1"])/r["wr"]*100
    bar = E0*math.exp(off/K)
    if lead >= bar:
        print(f"  {r['course'][:22]:22} {r['leader'][:5]:5} off {off:4.1f}%  "
              f"snuff within {bar/100*r['wr']/1000:.2f}s")
