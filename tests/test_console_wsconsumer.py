from wsconsumer import route


def test_route_preview():
    kind, msg = route({"type": "preview", "data": "x"})
    assert kind == "preview"


def test_route_state():
    assert route({"type": "heartbeat", "fps": 60})[0] == "state"
    assert route({"type": "selection_update"})[0] == "state"
    assert route({"type": "clip_done", "item": "z"})[0] == "state"
