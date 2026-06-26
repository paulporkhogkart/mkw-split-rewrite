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


def test_abort_delegates():
    b, mgr = make()
    assert b._handle_at_command({"type": "at_record_clip_abort"})["type"] == "clip_aborted"
    assert mgr.calls == [("abort",)]


def test_mutating_commands_error_without_manager():
    from mkw_tracker.ipc.broadcaster import EventBroadcaster
    b = EventBroadcaster(port=0)
    b._at_enabled = True                      # NB: no set_clip_manager → _clip_mgr is None
    for cmd in ({"type": "at_record_clip_mark", "event": "swap"},
                {"type": "at_record_clip_abort"},
                {"type": "at_record_clip_begin", "item": "x"}):
        assert b._handle_at_command(cmd)["type"] == "at_error"


def test_exists_false_path():
    b, mgr = make()
    mgr._exists = False
    assert b._handle_at_command({"type": "at_clip_exists", "item": "z"}) == {"type": "exists_result", "done": False}
