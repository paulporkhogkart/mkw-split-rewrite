"""Pure lifecycle state machine for the sweep console.

No I/O: on_event() returns a list of action names that the supervisor maps to
real calls, so the whole control flow is unit-testable without processes.
"""

IDLE = "IDLE"
RIG_WARM = "RIG_WARM"
SWEEPING = "SWEEPING"
PAUSE_REQUESTED = "PAUSE_REQUESTED"
PAUSED = "PAUSED"
STOP_REQUESTED = "STOP_REQUESTED"

START_RIG = "START_RIG"
BEGIN_SWEEP = "BEGIN_SWEEP"
PAUSE = "PAUSE"
RESUME = "RESUME"
STOP = "STOP"
SWEEP_EXITED = "SWEEP_EXITED"


class ControlState:
    def __init__(self):
        self.state = IDLE

    def on_event(self, event):
        s = self.state
        if event == START_RIG and s == IDLE:
            self.state = RIG_WARM
            return ["start_agent", "start_tracker", "connect_ws", "connect_manual", "enable_manual"]
        if event == BEGIN_SWEEP and s == RIG_WARM:
            self.state = SWEEPING
            return ["disable_manual", "start_sweep"]
        if event == RESUME and s == PAUSED:
            self.state = SWEEPING
            return ["disable_manual", "start_sweep"]
        if event == PAUSE and s == SWEEPING:
            self.state = PAUSE_REQUESTED
            return ["request_sweep_stop"]
        if event == STOP and s in (RIG_WARM, PAUSED):
            self.state = IDLE
            return ["stop_rig", "disconnect"]
        if event == STOP and s in (SWEEPING, PAUSE_REQUESTED):
            self.state = STOP_REQUESTED
            return ["request_sweep_stop"]
        if event == SWEEP_EXITED and s == STOP_REQUESTED:
            self.state = IDLE
            return ["stop_rig", "disconnect"]
        if event == SWEEP_EXITED and s in (PAUSE_REQUESTED, SWEEPING):
            self.state = PAUSED
            return ["enable_manual"]
        return []
