"""One-off PROD season reset — fix Season 0 history + re-baseline Season 1.

Run on the Pi from the repo root (default = DRY RUN, writes nothing):

    MKW_DB=/home/pi/mkw-data/mkw.db python3 -m server.reset_season0           # dry run
    MKW_DB=/home/pi/mkw-data/mkw.db python3 -m server.reset_season0 --apply    # commit

What it does, and the ONE invariant it guards:
  * Season 0 = the corrected Discord reconstruction (server/data/season0_recovery.json);
    today becomes the S0/S1 boundary; each player's all-time best carries over to seed S1.
  * INVARIANT: a run that has positional mapping (run_trails) or lap data (run_laps) is
    NEVER deleted or duplicated. The only rows replaced are the point-LESS S0 reconstruction
    and the point-less carryover seeds. Live pbenguin runs (which hold the trails) are left
    exactly in place. The reconstruction is de-duped against live runs so a real attempt with
    a trail is never shadowed by a point-less copy, and is_pb ties break toward the row that
    HAS a trail. A run_trails/run_laps count check gates the commit.
"""
import sqlite3, json, shutil, os, sys, datetime

DB       = os.environ.get("MKW_DB", "/home/pi/mkw-data/mkw.db")
RECOVERY = os.environ.get("MKW_RECOVERY", "server/data/season0_recovery.json")
APPLY    = "--apply" in sys.argv
NOW      = datetime.datetime.now(datetime.timezone.utc).isoformat()


def recompute_is_pb(db, season_id):
    """Fastest finished run per (player,course,cc) wins is_pb; ties break toward a run that
    HAS run_trails (so the canonical PB keeps its trail), then earliest."""
    db.execute("UPDATE runs SET is_pb=0 WHERE season_id=?", (season_id,))
    rows = db.execute(
        """WITH pts AS (SELECT run_id, n FROM run_trails),
                ranked AS (
                  SELECT r.id, ROW_NUMBER() OVER (
                    PARTITION BY r.player_id, r.course_id, r.cc
                    ORDER BY r.total_time_ms ASC,
                             CASE WHEN p.n > 0 THEN 0 ELSE 1 END ASC,
                             datetime(r.ended_at) ASC, r.id ASC) rn
                  FROM runs r LEFT JOIN pts p ON p.run_id = r.id
                  WHERE r.season_id=? AND r.status='finished' AND r.total_time_ms IS NOT NULL)
           SELECT id FROM ranked WHERE rn=1""", (season_id,)).fetchall()
    for r in rows:
        db.execute("UPDATE runs SET is_pb=1 WHERE id=?", (r[0],))


def recompute_was_pb(db, season_id):
    db.execute("UPDATE runs SET was_pb=0 WHERE season_id=?", (season_id,))
    db.execute(
        """WITH f AS (SELECT id, total_time_ms, MIN(total_time_ms) OVER (
                 PARTITION BY player_id, course_id, cc ORDER BY datetime(ended_at), id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_min
             FROM runs WHERE season_id=? AND status='finished')
           UPDATE runs SET was_pb=1 WHERE id IN
             (SELECT id FROM f WHERE prior_min IS NULL OR total_time_ms < prior_min)""", (season_id,))


def fmt(ms): return f"{ms//60000}:{(ms % 60000)/1000:06.3f}" if ms is not None else "-"


def main():
    events = json.load(open(RECOVERY, encoding="utf-8"))
    print(f"DB={DB}  recovery={RECOVERY} ({len(events)} events)  mode={'APPLY' if APPLY else 'DRY-RUN'}")
    if APPLY:
        bak = DB + ".prereset-bak"
        if not os.path.exists(bak):
            shutil.copy(DB, bak); print(f"backup -> {bak}")
        else:
            print(f"backup {bak} already exists (kept as the original pre-reset copy)")

    db = sqlite3.connect(DB); db.row_factory = sqlite3.Row; db.isolation_level = None
    pid = {r["display_name"]: r["id"] for r in db.execute("SELECT id,display_name FROM players")}
    cid = {r["slug"]: r["id"] for r in db.execute("SELECT id,slug FROM courses")}
    s0 = db.execute("SELECT id FROM seasons WHERE name='Season 0'").fetchone()["id"]
    s1 = db.execute("SELECT id FROM seasons WHERE name='Season 1'").fetchone()["id"]

    pts_before  = db.execute("SELECT COALESCE(SUM(n),0) FROM run_trails").fetchone()[0]
    laps_before = db.execute("SELECT COUNT(*) FROM run_laps").fetchone()[0]
    live_before = db.execute("SELECT COUNT(*) FROM runs WHERE provenance='live'").fetchone()[0]

    # INVARIANT GUARD: refuse to run if any Season-0 row carries a trail/laps (it shouldn't —
    # the reconstruction has none — but never delete positional data).
    s0_pointed = db.execute(
        """SELECT COUNT(*) FROM runs WHERE season_id=? AND
             (id IN (SELECT run_id FROM run_trails) OR id IN (SELECT run_id FROM run_laps))""",
        (s0,)).fetchone()[0]
    if s0_pointed:
        print(f"ABORT: {s0_pointed} Season-0 rows carry run_trails/run_laps — refusing to replace them.")
        return

    # de-dup target: attempts already recorded as live runs (these hold the trails). The
    # reconstruction must not insert a point-less copy of an attempt that has one.
    live_keys = {(r["player_id"], r["course_id"], r["total_time_ms"])
                 for r in db.execute(
                     "SELECT player_id,course_id,total_time_ms FROM runs WHERE provenance='live' AND total_time_ms IS NOT NULL")}

    def pbms(season, who, slug):
        r = db.execute("SELECT total_time_ms FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND is_pb=1",
                       (season, pid[who], cid[slug])).fetchone()
        return r["total_time_ms"] if r else None

    def sample(tag):
        parts = [f"{who} KTB S0/S1={fmt(pbms(s0, who, 'koopa_troopa_beach'))}/{fmt(pbms(s1, who, 'koopa_troopa_beach'))}"
                 for who in ("Paul", "Gub", "Aliias")]
        print(f"  [{tag}] " + "  ".join(parts))
    sample("BEFORE")

    db.execute("BEGIN")
    db.execute("UPDATE seasons SET ended_at=? WHERE id=?", (NOW, s0))
    db.execute("UPDATE seasons SET started_at=? WHERE id=?", (NOW, s1))

    db.execute("DELETE FROM runs WHERE season_id=?", (s0,))   # safe: guarded point-less above
    ins = dup = unmapped = 0
    for e in events:
        p, c = pid.get(e["player"]), cid.get(e["course_slug"])
        if p is None or c is None: unmapped += 1; continue
        if (p, c, e["total_time_ms"]) in live_keys: dup += 1; continue   # keep the live (trail-bearing) row
        db.execute(
            """INSERT INTO runs(attempt_id,season_id,player_id,course_id,cc,status,provenance,
                 started_at,ended_at,total_time_ms,total_time_str,is_pb,created_at,was_pb,source)
               VALUES(?,?,?,?,?, 'finished','legacy_import', ?,?,?,?, 0,?,0,'discord')""",
            (e["attempt_id"], s0, p, c, e["cc"], e["ended_at"], e["ended_at"],
             e["total_time_ms"], e["total_time_str"], e["ended_at"]))
        ins += 1
    recompute_is_pb(db, s0); recompute_was_pb(db, s0)

    db.execute("DELETE FROM runs WHERE season_id=? AND provenance='carryover'", (s1,))
    seeds = db.execute(
        "SELECT player_id,course_id,cc,total_time_ms,total_time_str,started_at,ended_at FROM runs WHERE season_id=? AND is_pb=1",
        (s0,)).fetchall()
    for r in seeds:
        db.execute(
            """INSERT INTO runs(attempt_id,season_id,player_id,course_id,cc,status,provenance,
                 started_at,ended_at,total_time_ms,total_time_str,is_pb,created_at,was_pb,source)
               VALUES(?,?,?,?,?, 'finished','carryover', ?,?,?,?, 1,?,1,NULL)""",
            (f"carry-{r['player_id']}-{r['course_id']}-{r['cc']}", s1, r["player_id"], r["course_id"],
             r["cc"], r["started_at"], r["ended_at"], r["total_time_ms"], r["total_time_str"], NOW))
    recompute_is_pb(db, s1); recompute_was_pb(db, s1)

    # ---- post-change report (inside the txn) ----
    pts_after  = db.execute("SELECT COALESCE(SUM(n),0) FROM run_trails").fetchone()[0]
    laps_after = db.execute("SELECT COUNT(*) FROM run_laps").fetchone()[0]
    live_after = db.execute("SELECT COUNT(*) FROM runs WHERE provenance='live'").fetchone()[0]
    orphan_pts = db.execute("SELECT COUNT(*) FROM run_trails WHERE run_id NOT IN (SELECT id FROM runs)").fetchone()[0]
    pb_pointless_over_pointed = db.execute(
        """WITH pts AS (SELECT run_id, n FROM run_trails)
           SELECT COUNT(*) FROM runs pb LEFT JOIN pts pp ON pp.run_id=pb.id
           WHERE pb.is_pb=1 AND COALESCE(pp.n,0)=0 AND EXISTS (
             SELECT 1 FROM runs r2 JOIN pts x ON x.run_id=r2.id
             WHERE r2.season_id=pb.season_id AND r2.player_id=pb.player_id AND r2.course_id=pb.course_id
               AND r2.cc=pb.cc AND r2.status='finished' AND r2.total_time_ms<=pb.total_time_ms)""").fetchone()[0]
    sample("AFTER ")
    print(f"  S0 inserted={ins}  deduped-against-live={dup}  unmapped={unmapped}  carryover seeds={len(seeds)}")
    print(f"  trail points {pts_before}->{pts_after}  run_laps {laps_before}->{laps_after}  live runs {live_before}->{live_after}  orphan_points={orphan_pts}")
    print(f"  PBs on a point-less row while a trail-bearing run ties/beats it: {pb_pointless_over_pointed}")

    integrity_ok = (pts_after == pts_before and laps_after == laps_before
                    and live_after == live_before and orphan_pts == 0 and pb_pointless_over_pointed == 0)
    if not integrity_ok:
        db.execute("ROLLBACK"); print("ROLLED BACK — integrity check FAILED (positional data would change).")
        return
    if APPLY:
        db.execute("COMMIT"); print("APPLIED + committed. Prod Season 0 corrected; trails preserved.")
    else:
        db.execute("ROLLBACK"); print("DRY-RUN ok (rolled back). Re-run with --apply to commit.")


if __name__ == "__main__":
    main()
