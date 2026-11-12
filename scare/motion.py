"""Motion detection by background difference, for the IR camera at night (no model, no training).

Each frame is shrunk to a small grey copy and compared with a slowly updated background. Changed
areas big enough (min_area_pct of the frame) that last persist_frames frames in a row start a
motion event; it ends after QUIET_S without qualifying motion. A change over most of the picture
at once (lights switched, exposure jump) resets the background instead of counting as motion.

Used by scare.controller (night mode) and scare.motion_watch (the stand-alone test). MotionLog
writes a CSV row and an annotated photo per event under logs/motion/<date>/.
"""

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

WORK = (320, 240)          # motion is computed on a small grey copy (cheap, smooths sensor noise)
BG_RATE = 0.05             # how fast the background follows the scene (per frame)
GLOBAL_FRAC = 0.5          # more than this fraction changed at once: lighting/exposure, not motion
QUIET_S = 2.0              # an event ends after this long without qualifying motion


@dataclass
class MotionResult:
    boxes: list = field(default_factory=list)  # (x1, y1, x2, y2) in frame pixels, qualifying blobs
    changed: float = 0.0       # fraction of the picture that differs from the background
    area_pct: float = 0.0      # qualifying blobs' share of the frame, %
    mask: np.ndarray | None = None   # the change mask (WORK size)
    started: bool = False      # a new event started on this frame
    ended: dict | None = None  # the event that just ended: {"start", "seconds", "max_pct", "box"}
    active: bool = False       # an event is running


class MotionDetector:
    def __init__(self, threshold: int = 25, min_area_pct: float = 0.5, persist_frames: int = 3):
        self.threshold, self.min_area_pct, self.persist = threshold, min_area_pct, persist_frames
        self.event = None   # the running event, or None
        self.reset()

    def reset(self) -> None:
        """Forget the background (camera reopened, exposure changed). Ends no event."""
        self.bg = None
        self.streak = 0

    def update(self, frame: np.ndarray, now: float | None = None, armed: bool = True) -> MotionResult:
        """frame: (h, w, 3) uint8, RGB or BGR (only grey is used). armed=False learns the
        background without starting events (e.g. while the exposure settles)."""
        now = time.monotonic() if now is None else now
        grey = cv2.GaussianBlur(cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), WORK), (5, 5), 0)
        r = MotionResult()
        if self.bg is None:
            self.bg = grey.astype(np.float32)
        mask = cv2.threshold(cv2.absdiff(grey, cv2.convertScaleAbs(self.bg)), self.threshold, 255,
                             cv2.THRESH_BINARY)[1]
        r.mask = cv2.dilate(mask, None, iterations=2)
        r.changed = cv2.countNonZero(r.mask) / r.mask.size
        total = WORK[0] * WORK[1]
        if r.changed > GLOBAL_FRAC:
            self.bg = grey.astype(np.float32)   # lights or exposure, not an animal: re-learn
            self.streak = 0
            small = []
        else:
            contours, _ = cv2.findContours(r.mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            small = [cv2.boundingRect(c) for c in contours
                     if cv2.contourArea(c) >= self.min_area_pct / 100 * total]
            cv2.accumulateWeighted(grey, self.bg, BG_RATE)
            self.streak = self.streak + 1 if small and armed else 0
        sx, sy = frame.shape[1] / WORK[0], frame.shape[0] / WORK[1]
        r.boxes = [(int(x * sx), int(y * sy), int((x + w) * sx), int((y + h) * sy))
                   for x, y, w, h in small]
        r.area_pct = 100 * sum(w * h for _, _, w, h in small) / total

        event = self.event
        if self.streak >= self.persist:
            box = (min(b[0] for b in r.boxes), min(b[1] for b in r.boxes),
                   max(b[2] for b in r.boxes), max(b[3] for b in r.boxes))
            if event is None:
                event = self.event = {"start": datetime.now(), "start_t": now,
                                      "max_pct": r.area_pct, "box": box}
                r.started = True
            event["max_pct"] = max(event["max_pct"], r.area_pct)
            event["last"] = now
        if event is not None and now - event["last"] >= QUIET_S:
            event["seconds"] = event["last"] - event["start_t"]
            r.ended, self.event = event, None
        r.active = self.event is not None
        return r


def draw(frame_bgr: np.ndarray, r: MotionResult) -> np.ndarray:
    """A copy of the frame with the blobs boxed (red during an event, else yellow) and the change
    mask in the bottom-right corner."""
    view = frame_bgr.copy()
    colour = (0, 0, 255) if r.active else (0, 220, 255)
    for b in r.boxes:
        cv2.rectangle(view, b[:2], b[2:], colour, 2)
    if r.mask is not None:
        h, w = view.shape[:2]
        iw, ih = w // 4, h // 4
        view[h - ih:, w - iw:] = cv2.cvtColor(cv2.resize(r.mask, (iw, ih)), cv2.COLOR_GRAY2BGR)
        cv2.rectangle(view, (w - iw, h - ih), (w - 1, h - 1), (255, 255, 255), 1)
    return view


class MotionLog:
    """logs/motion/<date>/events.csv (one row per event) and <time>_motion.jpg per event start."""

    FIELDS = ["start", "end", "seconds", "max_area_pct", "box", "lux", "exposure_ms", "gain",
              "photo", "action"]

    def __init__(self, root: Path):
        self.root = root

    def _dir(self) -> Path:
        d = self.root / "logs" / "motion" / datetime.now().strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def photo(self, frame_bgr: np.ndarray, r: MotionResult) -> str:
        name = f"{datetime.now():%H%M%S}_motion.jpg"
        cv2.imwrite(str(self._dir() / name), draw(frame_bgr, r))
        return name

    def write(self, event: dict, md: dict, photo: str = "", action: str = "") -> None:
        path = self._dir() / "events.csv"
        new = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(self.FIELDS)
            w.writerow([f"{event['start']:%H:%M:%S}", f"{datetime.now():%H:%M:%S}",
                        f"{event.get('seconds', 0):.1f}", f"{event['max_pct']:.1f}", event["box"],
                        f"{md.get('Lux', float('nan')):.0f}",
                        f"{md.get('ExposureTime', 0) / 1000:.1f}",
                        f"{md.get('AnalogueGain', 0.0):.2f}", photo, action])
