"""Lifecycle tests for the asset-processing state machine (tools/sweep_console/procstate.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "sweep_console"))

import procstate as ps


def test_start_then_natural_completion():
    m = ps.ProcessState()
    assert m.on_event(ps.START) == ["start_processing"]
    assert m.state == ps.RUNNING
    assert m.on_event(ps.EXITED) == ["completed"]      # driver finished all clips
    assert m.state == ps.IDLE


def test_pause_resume_roundtrip():
    m = ps.ProcessState()
    m.on_event(ps.START)
    assert m.on_event(ps.PAUSE) == ["request_process_stop"]
    assert m.state == ps.PAUSE_REQUESTED
    assert m.on_event(ps.EXITED) == []                 # subprocess stopped between clips
    assert m.state == ps.PAUSED
    assert m.on_event(ps.RESUME) == ["start_processing"]   # relaunch -> manifest skips done
    assert m.state == ps.RUNNING


def test_stop_while_running():
    m = ps.ProcessState()
    m.on_event(ps.START)
    assert m.on_event(ps.STOP) == ["request_process_stop"]
    assert m.state == ps.STOP_REQUESTED
    assert m.on_event(ps.EXITED) == []
    assert m.state == ps.IDLE


def test_stop_while_paused_resets_without_relaunch():
    m = ps.ProcessState()
    m.on_event(ps.START); m.on_event(ps.PAUSE); m.on_event(ps.EXITED)
    assert m.state == ps.PAUSED
    assert m.on_event(ps.STOP) == []                   # nothing running to stop
    assert m.state == ps.IDLE


def test_stop_during_pause_request():
    m = ps.ProcessState()
    m.on_event(ps.START); m.on_event(ps.PAUSE)
    assert m.state == ps.PAUSE_REQUESTED
    assert m.on_event(ps.STOP) == ["request_process_stop"]   # escalate pause -> stop
    assert m.state == ps.STOP_REQUESTED
    m.on_event(ps.EXITED)
    assert m.state == ps.IDLE


def test_noops_when_idle():
    m = ps.ProcessState()
    for e in (ps.PAUSE, ps.RESUME, ps.STOP, ps.EXITED):
        assert m.on_event(e) == []
        assert m.state == ps.IDLE
