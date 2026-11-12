"""Debug window: every camera side by side, each with its detection boxes, and a status bar for the
outputs underneath.

Triggering detections are drawn red, everything else green. Only one camera streams at a time, so
the others show their last frame, dimmed, with its age in the header.
Sliders above the feeds set the trigger's and people's min confidence and the confirm frames live,
and a 0/1 switch turns the IR camera on (off: it shows its first frame, frozen).

Night mode (the NIGHT switch, top slider in both views) is a different window: the IR camera's
motion watch (show_night) with its own sliders (motion threshold, min area, persist frames).
Switching rebuilds the window with the other set of sliders; values are kept by the controller.
"""

import cv2
import numpy as np

from .detector import Detection

WINDOW = "Scare Force"
HEAD_H = 26
BAR_H = 56
GAP = 4
MAX_W = 1900   # the monitor is 1920 wide; three 640-wide feeds (1928) get scaled down to fit
FONT = cv2.FONT_HERSHEY_SIMPLEX
GREEN, RED, WHITE, GRAY, YELLOW = (0, 200, 0), (0, 0, 255), (255, 255, 255), (150, 150, 150), (0, 220, 255)


def draw_box(img: np.ndarray, det: Detection, color: tuple) -> None:
    x1, y1, x2, y2 = det.box
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    text = f"{det.label} {det.confidence:.2f}"
    (tw, th), _ = cv2.getTextSize(text, FONT, 0.5, 1)
    ty = max(y1, th + 4)
    cv2.rectangle(img, (x1, ty - th - 4), (x1 + tw + 4, ty), color, -1)
    cv2.putText(img, text, (x1 + 2, ty - 3), FONT, 0.5, WHITE, 1, cv2.LINE_AA)


def draw_feed(title: str, frame: np.ndarray | None, detections: list[Detection],
              triggers: list[Detection], live: bool, size: tuple[int, int]) -> np.ndarray:
    """One camera: header line over its frame (black if it has none yet), dimmed unless live."""
    w, h = size
    if frame is None:
        img = np.zeros((h, w, 3), np.uint8)
    else:
        img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        for det in detections:
            draw_box(img, det, RED if det in triggers else GREEN)
        if not live:
            img = cv2.convertScaleAbs(img, alpha=0.6)
    head = np.zeros((HEAD_H, w, 3), np.uint8)
    cv2.putText(head, title, (8, 18), FONT, 0.55, YELLOW if live else GRAY, 1, cv2.LINE_AA)
    return np.vstack([head, img])


def show(feeds: list[tuple[str, np.ndarray | None, list[Detection], list[Detection], bool]],
         status: list[tuple[str, str, bool]], line2: str) -> bool:
    """Draw and show one frame. feeds = [(title, frame or None, detections, triggers, live), ...],
    left to right; status = [(name, value, active), ...]. Returns False on q/Esc."""
    h, w = next(f[1] for f in feeds if f[1] is not None).shape[:2]
    gap = np.full((HEAD_H + h, GAP, 3), 60, np.uint8)
    cols = []
    for feed in feeds:
        cols += [draw_feed(*feed, (w, h)), gap]
    img = np.hstack(cols[:-1])

    bar = np.zeros((BAR_H, img.shape[1], 3), np.uint8)
    x = 8
    for name, value, active in status:
        text = f"{name}: {value}"
        cv2.putText(bar, text, (x, 22), FONT, 0.55, YELLOW if active else GRAY, 1, cv2.LINE_AA)
        x += cv2.getTextSize(text, FONT, 0.55, 1)[0][0] + 20
    cv2.putText(bar, line2, (8, 46), FONT, 0.5, WHITE, 1, cv2.LINE_AA)

    out = np.vstack([img, bar])
    if out.shape[1] > MAX_W:
        out = cv2.resize(out, (MAX_W, round(out.shape[0] * MAX_W / out.shape[1])),
                         interpolation=cv2.INTER_AREA)
    cv2.imshow(WINDOW, out)
    return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)


CONF_BAR = "trigger conf %"
PERSON_BAR = "person conf %"
FRAMES_BAR = "confirm frames"
IR_BAR = "IR camera (0 off, 1 on)"
CONF_MIN, CONF_MAX = 5, 95
FRAMES_MAX = 15


def _conf_bar(name: str, conf: float) -> None:
    cv2.createTrackbar(name, WINDOW, min(max(round(conf * 100), CONF_MIN), CONF_MAX),
                       CONF_MAX, lambda _: None)
    cv2.setTrackbarMin(name, WINDOW, CONF_MIN)


def _read_conf(name: str) -> float:
    return min(max(cv2.getTrackbarPos(name, WINDOW), CONF_MIN), CONF_MAX) / 100


NIGHT_BAR = "NIGHT (0 day, 1 night)"
M_THRESH_BAR, M_AREA_BAR, M_PERSIST_BAR = "motion threshold", "min area 0.1%", "persist frames"
_bars: set = set()   # the trackbars the window has now


def _bar(name: str, value: int, top: int, bottom: int = 0) -> None:
    cv2.createTrackbar(name, WINDOW, min(max(value, bottom), top), top, lambda _: None)
    if bottom:
        cv2.setTrackbarMin(name, WINDOW, bottom)
    _bars.add(name)


def _new_window() -> None:
    """A fresh window (OpenCV can't remove trackbars, so a mode switch rebuilds it)."""
    if _bars:
        cv2.destroyWindow(WINDOW)
        cv2.waitKey(1)
    _bars.clear()
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE | cv2.WINDOW_GUI_NORMAL)


def add_controls(conf: float, person_conf: float, frames: int, ir_on: bool | None = None,
                 night_switch: bool = False) -> None:
    """The day window's sliders: the NIGHT switch (if night_switch), trigger and person min
    confidence (percent), confirm frames, and an IR camera on/off switch (0/1) unless ir_on is
    None (no IR camera)."""
    _new_window()
    if night_switch:
        _bar(NIGHT_BAR, 0, 1)
    _conf_bar(CONF_BAR, conf)
    _conf_bar(PERSON_BAR, person_conf)
    _bars.update((CONF_BAR, PERSON_BAR))
    _bar(FRAMES_BAR, frames, FRAMES_MAX, 1)
    if ir_on is not None:
        _bar(IR_BAR, int(ir_on), 1)


def read_controls() -> tuple[float, float, int, bool | None, bool | None]:
    """The day sliders now: (trigger min confidence 0-1, person min confidence 0-1, frames >= 1,
    IR on or None without an IR switch, night or None without a NIGHT switch)."""
    frames = max(cv2.getTrackbarPos(FRAMES_BAR, WINDOW), 1)
    ir = cv2.getTrackbarPos(IR_BAR, WINDOW) == 1 if IR_BAR in _bars else None
    night = cv2.getTrackbarPos(NIGHT_BAR, WINDOW) == 1 if NIGHT_BAR in _bars else None
    return _read_conf(CONF_BAR), _read_conf(PERSON_BAR), frames, ir, night


def add_night_controls(threshold: int, min_area_pct: float, persist: int) -> None:
    """The night window's sliders: the NIGHT switch (on), motion threshold (grey levels 5-80),
    min area (0.1% steps of the frame) and persist frames."""
    _new_window()
    _bar(NIGHT_BAR, 1, 1)
    _bar(M_THRESH_BAR, threshold, 80, 5)
    _bar(M_AREA_BAR, round(min_area_pct * 10), 100, 1)
    _bar(M_PERSIST_BAR, persist, 10, 1)


def read_night_controls() -> tuple[bool, int, float, int]:
    """The night sliders now: (night, motion threshold, min area %, persist frames)."""
    return (cv2.getTrackbarPos(NIGHT_BAR, WINDOW) == 1,
            max(cv2.getTrackbarPos(M_THRESH_BAR, WINDOW), 5),
            max(cv2.getTrackbarPos(M_AREA_BAR, WINDOW), 1) / 10,
            max(cv2.getTrackbarPos(M_PERSIST_BAR, WINDOW), 1))


def show_night(view_bgr: np.ndarray, title: str, line1: str, line2: str, alert: bool) -> bool:
    """The night watch view: one feed (already drawn, BGR) under a NIGHT header, two status lines
    (red when alert). Returns False on q/Esc."""
    head = np.zeros((HEAD_H, view_bgr.shape[1], 3), np.uint8)
    cv2.putText(head, title, (8, 18), FONT, 0.55, YELLOW, 1, cv2.LINE_AA)
    bar = np.zeros((BAR_H, view_bgr.shape[1], 3), np.uint8)
    cv2.putText(bar, line1, (8, 22), FONT, 0.55, RED if alert else YELLOW, 1, cv2.LINE_AA)
    cv2.putText(bar, line2, (8, 46), FONT, 0.5, WHITE, 1, cv2.LINE_AA)
    cv2.imshow(WINDOW, np.vstack([head, view_bgr, bar]))
    return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)


def close() -> None:
    cv2.destroyAllWindows()
