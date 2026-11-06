"""Detection log: a photo, an annotated copy and a CSV row for every confirmed detection.

This is the stored "detection animal photograph, date, and time" the course asks for (Basic
Deliverable #3), and the data behind the user report (Challenge 1, `python -m scare.export`).

    dlog = DetectionLog(cfg["log"], ROOT)          # log.detections null -> DetectionLog is not made
    det_id = dlog.record(frame, hits, cam_id, cam_name, "scare", people=1)
    dlog.close()                                   # writes what is still queued

What is recorded: every frame scare.controller confirms an animal on (controller.labels, at or
over min_confidence, confirm_frames in a row), whether it fires a scare or not:
    outcome  scare                    it started a scare (its id is in that scare's CSV row)
             ignored: scare running   a scare was already on
             ignored: cooldown        within cooldown_seconds after one
             ignored: deterrents off  (not set yet: needs the off-switch sense line)
A scare is always recorded. Ignored ones are recorded at most once per log.photo_every_s per
camera, so an animal standing in view through a cooldown doesn't fill the card.

Files, under log.photos (default logs/photos/):
    <YYYY-MM-DD>/<id>.jpg            the frame as the camera gave it (for retraining)
    <YYYY-MM-DD>/<id>_annotated.jpg  boxes, labels, confidence, camera and date/time burned in
    id = <YYYYMMDD-HHMMSS-mmm>_cam<camera id>_<label>, from the local time the frame was confirmed
and one row per detection in log.detections (default logs/detections.csv):
    time                 local ISO 8601, ms, with UTC offset (the Pi's clock: check it is set,
                         `timedatectl`; with no network and no RTC it can be wrong after a boot)
    detection_id         as above
    camera, camera_name  the camera id (camera.ids) and its name
    label, confidence    the strongest confirmed animal; box = x1 y1 x2 y2 in pixels
    others               any other confirmed animals in the frame, "label conf; ..."
    people               person detections in the frame (they never veto)
    outcome              as above
    photo, annotated     paths relative to log.photos; empty if not saved (see below)
    width, height        frame size

Photos are written by a background thread, so a JPEG never delays a scare or the camera loop.
An ignored detection that finds QUEUE_MAX frames already waiting is not photographed (its row is
still written, photo empty) and counted; a scare's photo is always queued. If the card has less than log.min_free_mb free, photos stop (rows go
on) until space is freed, with one warning.
"""

import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .activation_log import CsvLog

QUEUE_MAX = 16  # frames waiting to be written (~0.9 MB each at 640x480)

FIELDS = ("time", "detection_id", "camera", "camera_name", "label", "confidence", "box",
          "others", "people", "outcome", "photo", "annotated", "width", "height")


class DetectionCsv(CsvLog):
    fields = FIELDS
    name = "detections"


def annotate(frame_rgb: np.ndarray, hits, stamp: str, camera_name: str) -> np.ndarray:
    """A BGR copy with each confirmed animal boxed and labelled, and camera + time burned in."""
    img = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    font, red, white, black = cv2.FONT_HERSHEY_SIMPLEX, (40, 40, 230), (255, 255, 255), (0, 0, 0)
    for det in hits:
        x1, y1, x2, y2 = (int(v) for v in det.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), red, 2)
        text = f"{det.label} {det.confidence:.2f}"
        (tw, th), _ = cv2.getTextSize(text, font, 0.6, 2)
        ty = max(y1, th + 6)
        cv2.rectangle(img, (x1, ty - th - 6), (x1 + tw + 6, ty), red, -1)
        cv2.putText(img, text, (x1 + 3, ty - 4), font, 0.6, white, 2, cv2.LINE_AA)
    banner = f"{stamp}  {camera_name}"
    (tw, th), _ = cv2.getTextSize(banner, font, 0.55, 1)
    cv2.rectangle(img, (0, h - th - 12), (tw + 12, h), black, -1)
    cv2.putText(img, banner, (6, h - 6), font, 0.55, white, 1, cv2.LINE_AA)
    return img


class DetectionLog:
    def __init__(self, cfg: dict, root: Path):
        self.csv = DetectionCsv(root / cfg["detections"])
        self.photos = root / cfg.get("photos", "logs/photos")
        self.photos.mkdir(parents=True, exist_ok=True)
        self.every_s = float(cfg.get("photo_every_s", 5))
        self.quality = int(cfg.get("jpeg_quality", 90))
        self.min_free_mb = float(cfg.get("min_free_mb", 500))
        self._last: dict = {}  # camera id -> monotonic time of its last ignored record
        self._q: queue.Queue = queue.Queue()  # unbounded: QUEUE_MAX is enforced for ignored only
        self.dropped = 0
        self._low_space = False
        self._worker = threading.Thread(target=self._write_photos, name="photos", daemon=True)
        self._worker.start()
        print(f"[log] photos -> {self.photos} (ignored detections at most every "
              f"{self.every_s:g} s per camera)", flush=True)

    def record(self, frame: np.ndarray, hits, camera_id, camera_name: str, outcome: str,
               people: int = 0, now: float | None = None) -> str | None:
        """Log one confirmed detection; returns its id, or None if rate-limited (ignored only)."""
        if not hits:
            return None
        now = time.monotonic() if now is None else now
        if outcome != "scare":
            last = self._last.get(camera_id)
            if last is not None and now - last < self.every_s:
                return None
            self._last[camera_id] = now
        wall = datetime.now().astimezone()
        best = max(hits, key=lambda d: d.confidence)
        det_id = f"{wall:%Y%m%d-%H%M%S}-{wall.microsecond // 1000:03d}_cam{camera_id}_{best.label}"
        day = self.photos / f"{wall:%Y-%m-%d}"
        photo = annotated = ""
        if self._space_ok():
            if outcome != "scare" and self._q.qsize() >= QUEUE_MAX:
                self.dropped += 1
                print(f"[log] photo queue full: {det_id} not saved ({self.dropped} dropped)",
                      flush=True)
            else:
                self._q.put((frame.copy(), list(hits), day, det_id,
                             wall.strftime("%Y-%m-%d %H:%M:%S %Z"), camera_name))
                photo = f"{day.name}/{det_id}.jpg"
                annotated = f"{day.name}/{det_id}_annotated.jpg"
        h, w = frame.shape[:2]
        self.csv.write({
            "time": wall.isoformat(timespec="milliseconds"), "detection_id": det_id,
            "camera": camera_id, "camera_name": camera_name, "label": best.label,
            "confidence": f"{best.confidence:.3f}", "box": " ".join(str(int(v)) for v in best.box),
            "others": "; ".join(f"{d.label} {d.confidence:.2f}" for d in hits if d is not best),
            "people": people, "outcome": outcome, "photo": photo, "annotated": annotated,
            "width": w, "height": h,
        })
        return det_id

    def _space_ok(self) -> bool:
        free_mb = shutil.disk_usage(self.photos).free / 1e6
        if free_mb < self.min_free_mb:
            if not self._low_space:
                print(f"[log] WARNING: {free_mb:.0f} MB free (< log.min_free_mb "
                      f"{self.min_free_mb:g}): photos paused, rows go on", flush=True)
            self._low_space = True
            return False
        if self._low_space:
            print("[log] space freed: photos resumed", flush=True)
        self._low_space = False
        return True

    def _write_photos(self) -> None:
        params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
        while True:
            item = self._q.get()
            if item is None:
                return
            frame, hits, day, det_id, stamp, camera_name = item
            try:
                day.mkdir(parents=True, exist_ok=True)
                ok1 = cv2.imwrite(str(day / f"{det_id}.jpg"),
                                  cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), params)
                ok2 = cv2.imwrite(str(day / f"{det_id}_annotated.jpg"),
                                  annotate(frame, hits, stamp, camera_name), params)
                if not (ok1 and ok2):
                    print(f"[log] could not write the photos of {det_id}", flush=True)
            except Exception as e:  # never take the controller down over a photo
                print(f"[log] photo {det_id} failed: {e}", flush=True)

    def close(self) -> None:
        """Write what is queued (up to 10 s), then close the CSV."""
        try:
            self._q.put(None)
            self._worker.join(timeout=10.0)
        finally:
            self.csv.close()
