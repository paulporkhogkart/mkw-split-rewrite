"""Run invalidation model.

An overlay that interrupts a run KILLS it - terminal, can never re-validate:
  * INVALIDATE = Photo Mode / Exit-Photo-Mode dialog / GameChat
  * PAUSE      = Race Menu / Home  (resumable - the run continues)

An invalidated run stops tracking, shows "Run invalidated - <reason>" on the UI
(emit_run_invalidated), is HELD for review at its end (never an auto-PB), and is
restored to normal ONLY by a genuinely fresh race start. It can never re-validate by
bouncing back to RACING through an overlay / Gallery / Home.
"""
import json
from unittest.mock import MagicMock

from mkw_tracker.detection.screen import Screen
from mkw_tracker.race.finish import FinishLatch
from mkw_tracker.lifecycle.race import RaceLifecycle


class _FakeIpc:
    def __init__(self):
        self.lines = []

    def emit(self, line):
        self.lines.append(json.loads(line))


def _lc(ipc, total_time=None, splits=None):
    sel = MagicMock()
    sel.state.course = "Rainbow Road"
    sel.state.character = "Mario"
    sel.state.costume = "Base"
    sel.state.kart = "Standard Kart"
    ts = MagicMock(); ts.total_time = total_time; ts.splits = splits or {}
    minimap = MagicMock(); minimap._calibrated = True
    mm_rec = MagicMock(); mm_rec.points = []
    laps = MagicMock(); laps.state.total_laps = 3
    lc = RaceLifecycle(selection=sel, laps=laps, coins=MagicMock(), ts=ts,
                       finish=FinishLatch(templates={}), mush=MagicMock(),
                       minimap=minimap, mm_rec=mm_rec, ipc=ipc)
    lc._race_started_at = "2026-06-23T00:00:00+00:00"   # a run is in progress
    return lc


def _events(ipc, t):
    return [l for l in ipc.lines if l["type"] == t]


def _finalized(ipc):
    return _events(ipc, "run_finalized")


def _inv(ipc):
    return _events(ipc, "run_invalidated")


# ── triggers ───────────────────────────────────────────────────────────────────

def test_photo_mode_during_racing_invalidates():
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.PHOTO_MODE)
    assert lc.run_invalidated
    assert _inv(ipc)[-1]["reason"] == "Photo Mode"


def test_gamechat_during_racing_invalidates():
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.GAMECHAT)
    assert lc.run_invalidated
    assert _inv(ipc)[-1]["reason"] == "GameChat"


def test_gallery_view_during_racing_invalidates():
    # Opening the Switch Album single-photo viewer suspends the game like HOME, so any
    # run it touches is void.
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.GALLERY_VIEW)
    assert lc.run_invalidated
    assert _inv(ipc)[-1]["reason"] == "Gallery"


def test_overlay_from_a_pause_invalidates():
    # RACING -> RACE_MENU (pause) -> PHOTO_MODE: a paused run is still in progress.
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.RACE_MENU)
    lc.on_screen_change(Screen.RACE_MENU, Screen.PHOTO_MODE)
    assert lc.run_invalidated


def test_overlay_in_menus_does_not_invalidate():
    # No run in progress -> nothing to invalidate.
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc._race_started_at = None
    lc.on_screen_change(Screen.COURSE_SELECT, Screen.GAMECHAT)
    lc.on_screen_change(Screen.GAMECHAT, Screen.COURSE_SELECT)
    assert not lc.run_invalidated
    assert not _inv(ipc)


def test_overlay_during_ghost_record_rearms_not_invalidates():
    # A ghost IMPORT interrupted by an overlay is treated like a normal ghost abort: the
    # partial capture is dropped and we stay ARMED for a clean re-watch, with NO run
    # invalidation (a ghost replay isn't the user's live run).
    for overlay in (Screen.GAMECHAT, Screen.GALLERY_VIEW, Screen.PHOTO_MODE):
        ipc = _FakeIpc(); lc = _lc(ipc)
        lc._race_started_at = None          # no live run; the ghost arms the record
        lc.arm_ghost()
        lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)     # fresh ghost -> RECORDING
        assert lc.ghost_recording, overlay
        lc.on_screen_change(Screen.GHOST, overlay)                # overlay mid-record
        assert lc.ghost_armed and not lc.ghost_recording, overlay  # re-armed, partial dropped
        assert not lc.run_invalidated, overlay                    # NOT invalidated
        assert not _inv(ipc), overlay                             # no run_invalidated emit


# ── pause stays resumable ──────────────────────────────────────────────────────

def test_race_menu_resumes_not_invalidates():
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.RACE_MENU)   # pause
    lc.on_screen_change(Screen.RACE_MENU, Screen.RACING)   # resume
    assert not lc.run_invalidated
    assert lc._race_started_at is not None                 # same run, not restarted
    assert not _finalized(ipc)


# ── airtight: never re-validates ───────────────────────────────────────────────

def test_invalidated_run_does_not_revalidate_via_overlay():
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.PHOTO_MODE)         # invalidate
    lc.on_screen_change(Screen.PHOTO_MODE, Screen.EXIT_PHOTO_MODE)
    lc.on_screen_change(Screen.EXIT_PHOTO_MODE, Screen.RACING)    # back to racing
    assert lc.run_invalidated                                    # STILL invalidated


def test_invalidated_run_does_not_revalidate_via_gallery_or_home():
    # The "Gallery re-validates" bug: bouncing through HOME/GALLERY back to RACING must
    # NOT restart an invalidated run.
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.GAMECHAT)          # invalidate
    lc.on_screen_change(Screen.GAMECHAT, Screen.RACING)
    lc.on_screen_change(Screen.RACING, Screen.HOME)
    lc.on_screen_change(Screen.HOME, Screen.GALLERY)
    lc.on_screen_change(Screen.GALLERY, Screen.HOME)
    lc.on_screen_change(Screen.HOME, Screen.RACING)
    assert lc.run_invalidated                                    # STILL invalidated


def test_invalidated_run_does_not_revalidate_via_gallery_view_chain():
    # GALLERY_VIEW kills the run; the user-described chain GALLERY_VIEW -> GALLERY -> HOME
    # -> RACING must NOT revive it (once invalidated, only a fresh start re-validates).
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.GALLERY_VIEW)      # invalidate
    lc.on_screen_change(Screen.GALLERY_VIEW, Screen.GALLERY)
    lc.on_screen_change(Screen.GALLERY, Screen.HOME)
    lc.on_screen_change(Screen.HOME, Screen.RACING)
    assert lc.run_invalidated                                    # STILL invalidated


def test_genuine_restart_revalidates_and_clears(memdb):
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.PHOTO_MODE)        # invalidate
    assert lc.run_invalidated
    lc.on_screen_change(Screen.RESET, Screen.RACING)            # genuine restart
    assert not lc.run_invalidated
    assert _inv(ipc)[-1]["reason"] is None                       # UI restored to normal


# ── held for review at the end (never an auto-PB) ──────────────────────────────

def test_invalidated_run_held_for_review_at_finish():
    ipc = _FakeIpc(); lc = _lc(ipc, total_time="2:17.070", splits={1: "0:44.685"})
    lc.on_screen_change(Screen.RACING, Screen.PHOTO_MODE)        # invalidate
    lc.on_screen_change(Screen.PHOTO_MODE, Screen.EXIT_PHOTO_MODE)
    lc.on_screen_change(Screen.EXIT_PHOTO_MODE, Screen.RACING)   # continue (invalidated)
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)   # FINISHED -> held
    fin = _finalized(ipc)
    assert len(fin) == 1
    assert fin[0]["total_time"] is None                          # never the legit time
    assert fin[0]["status"] == "finished"                        # held via missing total
    assert fin[0]["invalid_reason"] == "Photo Mode"


def test_invalidated_run_reset_is_silently_discarded():
    # Ending an invalidated run by RESETTING (not finishing the full race) auto-discards
    # it: NO run_finalized at all, so the app never holds it -> no review sound + popup.
    ipc = _FakeIpc(); lc = _lc(ipc, total_time="2:17.070", splits={1: "0:44.685"})
    lc.on_screen_change(Screen.RACING, Screen.PHOTO_MODE)        # invalidate
    lc.on_screen_change(Screen.PHOTO_MODE, Screen.EXIT_PHOTO_MODE)
    lc.on_screen_change(Screen.EXIT_PHOTO_MODE, Screen.RACING)   # continue (invalidated)
    lc.on_screen_change(Screen.RACING, Screen.RESET)             # RESET (not a finish)
    assert not _finalized(ipc)                                   # discarded - nothing emitted


def test_invalidated_run_reset_via_race_menu_is_silently_discarded():
    # Same, when the reset goes through the pause loop: RACING -> RACE_MENU -> RESET.
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.GAMECHAT)          # invalidate
    lc.on_screen_change(Screen.GAMECHAT, Screen.RACING)          # continue (invalidated)
    lc.on_screen_change(Screen.RACING, Screen.RACE_MENU)         # pause
    lc.on_screen_change(Screen.RACE_MENU, Screen.RESET)          # reset out of the menu
    assert not _finalized(ipc)                                   # discarded - nothing emitted


def test_normal_reset_still_finalizes_unchanged():
    # The discard is scoped to INVALIDATED runs: an ordinary reset still emits (status
    # "reset") so the existing run-review path is untouched.
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.RESET)
    fin = _finalized(ipc)
    assert len(fin) == 1 and fin[0]["status"] == "reset"


def test_invalidated_run_finalizes_exactly_once():
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc.on_screen_change(Screen.RACING, Screen.GAMECHAT)          # invalidate
    lc.on_screen_change(Screen.GAMECHAT, Screen.RACING)
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)   # held #1
    assert len(_finalized(ipc)) == 1
    lc._finalize_recording(completed=True)                       # guarded - no duplicate
    assert len(_finalized(ipc)) == 1


def test_normal_race_still_finalizes_with_its_time():
    ipc = _FakeIpc(); lc = _lc(ipc, total_time="1:23.456", splits={1: "0:41.000"})
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)
    fin = _finalized(ipc)
    assert len(fin) == 1
    assert fin[0]["status"] == "finished" and fin[0]["total_time"] == "1:23.456"
    assert fin[0]["invalid_reason"] is None


# ── joined an unknown race (mid-stream) is invalidated, like any other major disruption ──────
# UNKNOWN_RACE_ACTIVE means we detected an active race we never saw START (cold start / NO_SIGNAL
# recovery / unknown reset), so the early laps + minimap trail are missing.  When it resolves to
# RACING the run can never be a valid PB, so it is invalidated exactly like an overlay: held for
# review at its end, never auto-uploaded.

def test_unknown_race_active_resolving_to_racing_invalidates(memdb):
    ipc = _FakeIpc(); lc = _lc(ipc)
    lc._race_started_at = None                                   # this transition IS the (mid) start
    lc.on_screen_change(Screen.UNKNOWN_RACE_ACTIVE, Screen.RACING)
    assert lc.run_invalidated
    assert _inv(ipc)[-1]["reason"] == "Unknown start"


def test_unknown_race_active_run_held_for_review_at_finish(memdb):
    # The user's sequence: unknown race -> race menu -> racing -> finish. Stays invalidated the
    # whole way and is HELD for review (no auto-PB) at the finish.
    ipc = _FakeIpc(); lc = _lc(ipc, total_time="2:17.070", splits={1: "0:44.685"})
    lc._race_started_at = None
    lc.on_screen_change(Screen.UNKNOWN_RACE_ACTIVE, Screen.RACING)   # joined mid-race (invalid)
    lc.on_screen_change(Screen.RACING, Screen.RACE_MENU)             # pause
    lc.on_screen_change(Screen.RACE_MENU, Screen.RACING)            # resume - still invalid
    assert lc.run_invalidated
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)      # finished -> held
    fin = _finalized(ipc)
    assert len(fin) == 1
    assert fin[0]["total_time"] is None
    assert fin[0]["status"] == "finished"
    assert fin[0]["invalid_reason"] == "Unknown start"


def test_genuine_fresh_start_is_not_invalidated(memdb):
    # Guard: a race we DID see begin (START_TIME_TRIAL / RESET -> RACING) stays valid.
    for start in (Screen.START_TIME_TRIAL, Screen.RESET):
        ipc = _FakeIpc(); lc = _lc(ipc)
        lc._race_started_at = None
        lc.on_screen_change(start, Screen.RACING)
        assert not lc.run_invalidated, start
        assert not _inv(ipc), start
