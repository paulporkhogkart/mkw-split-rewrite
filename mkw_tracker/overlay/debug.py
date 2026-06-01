"""Debug overlay: ROI boxes for screen detection and selection tracking."""
import cv2
import numpy as np
from typing import Dict, Optional, Set

from ..detection.screen import Screen, Tell
from ..detection.selection import SelectionState, KNOWN_COSTUMES, CHAR_NAME_ROI, COSTUME_ROI, KART_NAME_ROI, COURSE_NAME_ROI
from ..utils.image import draw_roi_box, draw_roi_label

# Selection ROI colours (BGR)
_SEL_COLOUR_CHAR    = (255, 180,  60)
_SEL_COLOUR_COSTUME = (255, 120, 200)
_SEL_COLOUR_KART    = (60,  200, 255)
_SEL_COLOUR_COURSE  = (180, 255,  80)


def draw_debug_rois(
    frame:            Optional[np.ndarray],
    display:          Optional[np.ndarray],
    current_screen:   Screen,
    current_score:    float,
    candidate_screens: Set[Screen],
    candidate_scores: Dict[Screen, float],
    tells_by_screen:  Dict[Screen, Tell],
):
    """
    Draw tell ROI boxes on full-res *frame* and labels on resized *display*.

    Green   - active tell matched
    Orange  - active tell not matched
    Cyan    - candidate next-screen ROIs
    """
    if current_screen != Screen.UNKNOWN:
        tell = tells_by_screen.get(current_screen)
        if tell is not None:
            matched = current_score >= tell.match_threshold
            color   = (0, 255, 0) if matched else (0, 140, 255)
            status  = "[OK]" if matched else "[--]"
            label   = f"{current_screen.name} {status} {current_score:.2f}"
            for roi in tell.all_rois():
                if frame is not None:
                    draw_roi_box(frame, roi, color)
                if display is not None:
                    draw_roi_label(display, roi, color, label)

    CYAN = (255, 255, 0)
    for screen in candidate_screens:
        if screen == current_screen:
            continue
        tell = tells_by_screen.get(screen)
        if tell is None:
            continue
        score = candidate_scores.get(screen, 0.0)
        label = f"-> {screen.name} {score:.2f}"
        for roi in tell.all_rois():
            if frame is not None:
                draw_roi_box(frame, roi, CYAN, thickness=1)
            if display is not None:
                draw_roi_label(display, roi, CYAN, label)


def draw_selection_rois(
    frame:     Optional[np.ndarray],
    display:   Optional[np.ndarray],
    screen:    Screen,
    selection: SelectionState,
):
    """Draw selection-scan ROI boxes on the appropriate selection screens."""
    rois: list = []

    if screen == Screen.CHARACTER_SELECT:
        char_label = f"char: {selection.character or '?'} ({selection.character_conf:.2f})"
        rois.append((CHAR_NAME_ROI, _SEL_COLOUR_CHAR, char_label))
        if selection.character and bool(KNOWN_COSTUMES.get(selection.character)):
            cos_label = f"costume: {selection.costume or '?'} ({selection.costume_conf:.2f})"
            rois.append((COSTUME_ROI, _SEL_COLOUR_COSTUME, cos_label))
    elif screen == Screen.KART_SELECT:
        kart_label = f"kart: {selection.kart or '?'} ({selection.kart_conf:.2f})"
        rois.append((KART_NAME_ROI, _SEL_COLOUR_KART, kart_label))
    elif screen == Screen.COURSE_SELECT:
        course_label = f"course: {selection.course or '?'} ({selection.course_conf:.2f})"
        rois.append((COURSE_NAME_ROI, _SEL_COLOUR_COURSE, course_label))

    for roi, colour, label in rois:
        if frame is not None:
            draw_roi_box(frame, roi, colour, thickness=2)
        if display is not None:
            draw_roi_label(display, roi, colour, label)
