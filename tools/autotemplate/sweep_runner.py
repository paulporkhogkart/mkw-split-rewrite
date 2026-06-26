"""WSL2 sweep runner: walk the grid, record one clip per item, ground keep/discard.

Hardware (nxbt) is injected as `controller`; the orchestrator WS as `client`, so
the per-item logic is unit-testable with fakes. main() wires the real ones.
"""
import time


class SweepRunner:
    def __init__(self, grid, controller, client, *, idle_seconds=10.0, lang="en_uk"):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
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
