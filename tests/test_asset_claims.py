import os
import time
from concurrent.futures import ThreadPoolExecutor

import claims


def test_try_claim_is_exclusive(tmp_path):
    d = str(tmp_path / "claims")
    assert claims.try_claim(d, "clipA", "m1") is True
    assert claims.try_claim(d, "clipA", "m2") is False   # already taken
    assert claims.try_claim(d, "clipB", "m2") is True


def _claim(d_name_who):
    d, name, who = d_name_who
    return claims.try_claim(d, name, who)


def test_try_claim_race_exactly_one_winner(tmp_path):
    # 8 threads race for the same name. os.open(O_CREAT|O_EXCL) is atomic in the
    # kernel, so exactly one wins regardless of the GIL — this exercises our
    # FileExistsError->False path. (True cross-machine atomicity is validated live.)
    d = str(tmp_path / "claims")
    os.makedirs(d, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_claim, [(d, "x", f"m{i}") for i in range(8)]))
    assert sum(results) == 1                              # exactly one thread won


def test_done_and_counts(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "a", "m1")
    claims.try_claim(d, "b", "m1")
    claims.mark_done(d, "a")
    assert claims.is_done(d, "a") is True
    assert claims.is_done(d, "b") is False
    assert claims.count_done(d) == 1
    assert claims.claimed_names(d) == {"a", "b"}


def test_pending_excludes_claimed_and_own_done(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "b", "other")                    # someone else owns b
    pend = claims.pending_names(["a", "b", "c"], d, own_done={"c"})
    assert pend == ["a"]                                 # b claimed, c own-done


def test_reclaim_own_only_mine_and_not_done(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "mine_ip", "m1")                 # mine, in progress -> reclaimed
    claims.try_claim(d, "mine_done", "m1"); claims.mark_done(d, "mine_done")  # mine, done -> kept
    claims.try_claim(d, "theirs", "m2")                  # not mine -> kept
    n = claims.reclaim_own(d, "m1")
    assert n == 1
    assert claims.try_claim(d, "mine_ip", "m1") is True  # freed -> reclaimable
    assert "theirs" in claims.claimed_names(d)          # not mine -> kept
    assert "mine_done" in claims.claimed_names(d)


def test_reclaim_orphans_by_age(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "fresh", "m1")
    claims.try_claim(d, "stale", "m2")
    old = time.time() - 5000
    os.utime(os.path.join(d, "stale.claim"), (old, old))
    n = claims.reclaim_orphans(d, stale_secs=1800)
    assert n == 1
    assert "stale" not in claims.claimed_names(d)
    assert "fresh" in claims.claimed_names(d)
