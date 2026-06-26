"""WSL2 sweep runner: walk the grid, record one clip per item, ground keep/discard.

Hardware (nxbt) is injected as `controller`; the orchestrator WS as `client`, so
the per-item logic is unit-testable with fakes. main() wires the real ones.
"""
import time


class SweepRunner:
    GROUND_THRESHOLD = 0.85

    def __init__(self, grid, controller, client, *, idle_seconds=10.0, settle_seconds=0.8, lang="en_uk"):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
        self.settle = settle_seconds
        self.lang = lang

    def _begin(self, item):
        self.client.send({"type": "at_record_clip_begin", "item": item})

    def _mark(self, event):
        self.client.send({"type": "at_record_clip_mark", "event": event})

    def _exists(self, item) -> bool:
        return self.client.send({"type": "at_clip_exists", "item": item}).get("done", False)

    def capture_char(self, slug):
        if self._exists(slug):
            return None
        self._begin(slug)
        time.sleep(self.idle)                    # settled idle (no spawn-in)
        self.ctrl.press("A")                     # flourish → character_select drops
        self._mark("flourish")
        return self.client.wait_for("clip_done").get("events")

    # ------------------------------------------------------------------
    # Kart capture
    # ------------------------------------------------------------------

    def _ground_kart(self, kart_slug) -> bool:
        r = self.client.send({"type": "at_check_asset_match", "category": "karts",
                              "lang": self.lang, "name": kart_slug})
        return r.get("name_score", 0.0) >= self.GROUND_THRESHOLD

    def _recover_to(self, kart_slug):
        """Find the actually-selected kart by scanning the row, then step the horizontal delta."""
        row = [c.slug for c in self.grid.cells("karts")
               if c.coord[0] == self.grid.coord_of(kart_slug)[0]]
        here = next((k for k in row
                     if self.client.send({"type": "at_check_asset_match", "category": "karts",
                                         "lang": self.lang, "name": k}).get("name_score", 0.0)
                     >= self.GROUND_THRESHOLD), None)
        if here is None:
            return                                  # next loop's press re-tries blindly
        for press in self.grid.horizontal_delta(here, kart_slug):
            self.ctrl.press(press)

    def capture_kart(self, combo_slug, kart_slug, *, first=False):
        item = f"{combo_slug}__{kart_slug}"
        if self._exists(item):
            return None
        while True:
            self._begin(item)
            if first:                               # Standard Kart: off-and-back for spawn-in
                self.ctrl.press("DPAD_RIGHT")
                self.ctrl.press("DPAD_LEFT")
            else:
                self.ctrl.press("DPAD_RIGHT")       # swap onto this kart
            self._mark("swap")
            time.sleep(self.settle)                 # name plate settles
            if self._ground_kart(kart_slug):
                break
            self.client.send({"type": "at_record_clip_abort"})
            self._recover_to(kart_slug)             # step back; loop re-begins
        time.sleep(self.idle)                       # spawn-in already rolling; capture idle
        self.ctrl.press("A")                        # flourish → kart_select drops
        self._mark("flourish")
        ev = self.client.wait_for("clip_done").get("events")
        self.ctrl.press("B")                        # back to kart select (same kart, confirmed)
        return ev

    def sweep_karts(self, combo_slug):
        karts = [c.slug for c in self.grid.cells("karts")]
        out = []
        for i, kart in enumerate(karts):
            out.append(self.capture_kart(combo_slug, kart, first=(i == 0)))
        self.ctrl.press("B")                        # kart select → character select
        return out
