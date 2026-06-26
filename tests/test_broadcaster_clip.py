from mkw_tracker.ipc.broadcaster import EventBroadcaster


class FakeMgr:
    def __init__(self): self.calls = []; self._exists = False
    def begin(self, item): self.calls.append(("begin", item))
    def mark(self, ev): self.calls.append(("mark", ev))
    def abort(self): self.calls.append(("abort",))
    def exists(self, item): return self._exists


def make():
    b = EventBroadcaster(port=0)
    b._at_enabled = True
    mgr = FakeMgr()
    b.set_clip_manager(mgr)
    return b, mgr


def test_begin_and_mark():
    b, mgr = make()
    assert b._handle_at_command({"type": "at_record_clip_begin",
                                 "item": "mario__base"})["type"] == "clip_begun"
    assert b._handle_at_command({"type": "at_record_clip_mark",
                                 "event": "swap"})["type"] == "marked"
    assert mgr.calls == [("begin", "mario__base"), ("mark", "swap")]


def test_exists():
    b, mgr = make()
    mgr._exists = True
    r = b._handle_at_command({"type": "at_clip_exists", "item": "x__y"})
    assert r == {"type": "exists_result", "done": True}
