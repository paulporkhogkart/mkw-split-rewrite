from progress import ProgressModel


def test_empty_is_zero_no_eta():
    snap = ProgressModel(6273).snapshot()
    assert snap["done"] == 0 and snap["total"] == 6273
    assert snap["pct"] == 0.0 and snap["eta_seconds"] is None


def test_rate_and_eta_from_two_samples():
    p = ProgressModel(100)
    p.update(0, 0.0)
    p.update(10, 10.0)          # 10 clips in 10s -> 1/s, 90 remaining -> 90s
    snap = p.snapshot()
    assert abs(snap["rate_per_sec"] - 1.0) < 1e-9
    assert abs(snap["eta_seconds"] - 90.0) < 1e-9
    assert abs(snap["pct"] - 0.10) < 1e-9


def test_no_progress_means_no_eta():
    p = ProgressModel(100)
    p.update(5, 0.0)
    p.update(5, 10.0)           # unchanged -> rate 0
    assert p.snapshot()["eta_seconds"] is None


def test_complete():
    p = ProgressModel(10)
    p.update(0, 0.0); p.update(10, 5.0)
    snap = p.snapshot()
    assert snap["done"] == 10 and snap["pct"] == 1.0 and snap["eta_seconds"] == 0.0
