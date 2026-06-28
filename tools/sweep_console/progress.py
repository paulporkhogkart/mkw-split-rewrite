"""Pure progress + ETA from clip counts sampled over time."""


class ProgressModel:
    def __init__(self, total, window=20):
        self.total = total
        self._window = window
        self._samples = []   # list of (time, done)

    def update(self, done, now):
        if not self._samples or self._samples[-1][1] != done:
            self._samples.append((now, done))
            if len(self._samples) > self._window:
                self._samples.pop(0)

    def _rate(self):
        if len(self._samples) < 2:
            return 0.0
        (t0, d0), (t1, d1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        return (d1 - d0) / dt if dt > 0 and d1 > d0 else 0.0

    def snapshot(self, now=None):
        done = self._samples[-1][1] if self._samples else 0
        rate = self._rate()
        remaining = max(0, self.total - done)
        eta = (remaining / rate) if rate > 0 else None
        pct = (done / self.total) if self.total else 0.0
        return {"done": done, "total": self.total, "pct": pct,
                "rate_per_sec": rate, "eta_seconds": eta}
