"""Pure state machine for the asset-PROCESSING run (extract+matte the captured clips).

Mirrors the sweep's pause/stop/resume but without the rig (no agent/tracker/manual): it
just supervises one batch-driver subprocess. Pause/stop both work by writing a stop-file the
driver checks BETWEEN clips, so the subprocess exits cleanly; RESUME relaunches it and the
manifest makes it skip everything already done. on_event() returns action names the app maps
to supervisor calls — no I/O here, so it's unit-testable without processes.
"""

IDLE = "IDLE"
RUNNING = "RUNNING"
PAUSE_REQUESTED = "PAUSE_REQUESTED"
PAUSED = "PAUSED"
STOP_REQUESTED = "STOP_REQUESTED"

START = "START"
PAUSE = "PAUSE"
RESUME = "RESUME"
STOP = "STOP"
EXITED = "EXITED"                                      # the driver subprocess ended


class ProcessState:
    def __init__(self):
        self.state = IDLE

    def on_event(self, event):
        s = self.state
        if event == START and s == IDLE:
            self.state = RUNNING
            return ["start_processing"]
        if event == RESUME and s == PAUSED:
            self.state = RUNNING
            return ["start_processing"]               # relaunch; manifest skips done -> resume
        if event == PAUSE and s == RUNNING:
            self.state = PAUSE_REQUESTED
            return ["request_process_stop"]
        if event == STOP and s in (RUNNING, PAUSE_REQUESTED):
            self.state = STOP_REQUESTED
            return ["request_process_stop"]
        if event == STOP and s == PAUSED:             # already stopped, just reset
            self.state = IDLE
            return []
        if event == EXITED and s == STOP_REQUESTED:
            self.state = IDLE
            return []
        if event == EXITED and s == PAUSE_REQUESTED:
            self.state = PAUSED
            return []
        if event == EXITED and s == RUNNING:          # natural completion (no stop requested)
            self.state = IDLE
            return ["completed"]
        return []
