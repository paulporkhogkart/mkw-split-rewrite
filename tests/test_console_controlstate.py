import controlstate as cs


def test_start_rig_from_idle():
    m = cs.ControlState()
    acts = m.on_event(cs.START_RIG)
    assert m.state == cs.RIG_WARM
    assert acts == ["start_agent", "start_tracker", "connect_ws", "connect_manual", "enable_manual"]


def test_begin_sweep_disables_manual_and_starts():
    m = cs.ControlState(); m.on_event(cs.START_RIG)
    acts = m.on_event(cs.BEGIN_SWEEP)
    assert m.state == cs.SWEEPING
    assert acts == ["disable_manual", "start_sweep"]


def test_pause_requests_stop_then_paused_on_exit():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    assert m.on_event(cs.PAUSE) == ["request_sweep_stop"]
    assert m.state == cs.PAUSE_REQUESTED
    assert m.on_event(cs.SWEEP_EXITED) == ["enable_manual"]
    assert m.state == cs.PAUSED


def test_resume_from_paused():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    m.on_event(cs.PAUSE); m.on_event(cs.SWEEP_EXITED)
    assert m.on_event(cs.RESUME) == ["disable_manual", "start_sweep"]
    assert m.state == cs.SWEEPING


def test_stop_while_sweeping_waits_for_exit_then_tears_down():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    assert m.on_event(cs.STOP) == ["request_sweep_stop"]
    assert m.state == cs.STOP_REQUESTED
    assert m.on_event(cs.SWEEP_EXITED) == ["stop_rig", "disconnect"]
    assert m.state == cs.IDLE


def test_stop_from_rig_warm_is_immediate():
    m = cs.ControlState(); m.on_event(cs.START_RIG)
    assert m.on_event(cs.STOP) == ["stop_rig", "disconnect"]
    assert m.state == cs.IDLE


def test_sweep_exits_on_its_own_lands_paused():
    m = cs.ControlState(); m.on_event(cs.START_RIG); m.on_event(cs.BEGIN_SWEEP)
    assert m.on_event(cs.SWEEP_EXITED) == ["enable_manual"]
    assert m.state == cs.PAUSED


def test_invalid_transition_is_noop():
    m = cs.ControlState()
    assert m.on_event(cs.PAUSE) == []
    assert m.state == cs.IDLE
