"""Motion-watch test on the IR camera: does it see movement reliably, and how often does it
trigger falsely? Nothing else runs: no detector, no light, no sound, no servos. The motion logic
is scare/motion.py, the same as the controller's night mode.

After a short warm-up the exposure is locked, so auto-exposure drifting is not seen as motion
(l toggles auto/locked). The status line shows libcamera's light estimate (lux), exposure time and
gain: note the lux at dusk to choose a "dark" threshold later.

Window: the IR feed with changed areas boxed (yellow: candidate, red: MOTION), the change mask in
the corner, and sliders for the threshold, min area and persist frames (start: config.yaml's
night.motion). Each event: a terminal line, a row in logs/motion/<date>/events.csv and a photo.

Run (stop scare.controller first: only one program can use the cameras):
    .venv/bin/python -m scare.motion_watch              # window; q / Esc / Ctrl+C stops
    .venv/bin/python -m scare.motion_watch --headless   # terminal only (e.g. outside at night)
"""

import argparse
import time

import cv2
import numpy as np

from . import ROOT, load_config
from .camera import Camera
from .motion import MotionDetector, MotionLog, draw

WINDOW = "Scare Force motion watch"
THRESH_BAR, AREA_BAR, PERSIST_BAR = "threshold", "min area 0.1%", "persist frames"


def ir_camera_cfg(cfg: dict) -> dict:
    """config.yaml's camera section with ids reduced to the first IR camera."""
    from picamera2 import Picamera2

    info = Picamera2.global_camera_info()
    ir_models = cfg.get("ir_models", ["imx219"])
    ids = [n for n in cfg["ids"] if n < len(info) and info[n]["Model"] in ir_models]
    if not ids:
        raise RuntimeError(f"no IR camera ({ir_models}) among cameras {cfg['ids']}")
    return {**cfg, "ids": ids[:1]}


def main() -> None:
    cfg = load_config()
    m = cfg.get("night", {}).get("motion", {})
    ap = argparse.ArgumentParser(description="IR camera motion-watch test")
    ap.add_argument("--headless", action="store_true", help="no window")
    ap.add_argument("--no-lock", action="store_true", help="keep auto-exposure on")
    args = ap.parse_args()

    motion = MotionDetector(m.get("threshold", 25), m.get("min_area_pct", 0.5),
                            m.get("persist_frames", 3))
    warmup = float(m.get("warmup_s", 5.0))
    camera = Camera(ir_camera_cfg(cfg["camera"]))
    name = camera.names[0]
    mlog = MotionLog(ROOT)
    if not args.headless:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE | cv2.WINDOW_GUI_NORMAL)
        cv2.createTrackbar(THRESH_BAR, WINDOW, motion.threshold, 80, lambda _: None)
        cv2.setTrackbarMin(THRESH_BAR, WINDOW, 5)
        cv2.createTrackbar(AREA_BAR, WINDOW, max(1, round(motion.min_area_pct * 10)), 100,
                           lambda _: None)
        cv2.setTrackbarMin(AREA_BAR, WINDOW, 1)
        cv2.createTrackbar(PERSIST_BAR, WINDOW, motion.persist, 10, lambda _: None)
        cv2.setTrackbarMin(PERSIST_BAR, WINDOW, 1)
    print(f"[motion] watching {name}; events -> logs/motion/<date>/events.csv"
          f"{'' if args.no_lock else f'; exposure locks after {warmup:g} s'}", flush=True)

    start = last_t = last_print = time.monotonic()
    locked, want_lock = False, not args.no_lock
    n_events, fps = 0, 0.0
    try:
        while True:
            rgb, md = camera.read_meta()
            bgr = rgb[..., ::-1]
            now = time.monotonic()
            fps = 0.9 * fps + 0.1 / max(now - last_t, 1e-6)
            last_t = now
            lux = md.get("Lux", float("nan"))
            exp_ms, gain = md.get("ExposureTime", 0) / 1000, md.get("AnalogueGain", 0.0)
            if want_lock and not locked and now - start >= warmup:
                camera.lock_exposure(md)
                locked = True
                motion.reset()   # the lock nudges brightness: start a fresh background
                print(f"[motion] exposure locked: {exp_ms:.1f} ms, gain {gain:.2f}, "
                      f"lux {lux:.0f}", flush=True)
            if not args.headless:
                motion.threshold = cv2.getTrackbarPos(THRESH_BAR, WINDOW)
                motion.min_area_pct = cv2.getTrackbarPos(AREA_BAR, WINDOW) / 10
                motion.persist = max(1, cv2.getTrackbarPos(PERSIST_BAR, WINDOW))

            r = motion.update(rgb, now)
            if r.started:
                n_events += 1
                motion.event["photo"] = mlog.photo(np.ascontiguousarray(bgr), r)
                print(f"[motion] MOTION #{n_events} at {motion.event['start']:%H:%M:%S}: "
                      f"{r.area_pct:.1f}% of the frame, box {motion.event['box']}, lux {lux:.0f}",
                      flush=True)
            if r.ended:
                mlog.write(r.ended, md, r.ended.get("photo", ""), "test")
                print(f"[motion] #{n_events} over after {r.ended['seconds']:.1f} s "
                      f"(max {r.ended['max_pct']:.1f}%)", flush=True)

            if args.headless:
                if now - last_print >= 10:
                    print(f"[motion] {fps:.0f} fps, lux {lux:.0f}, exposure {exp_ms:.1f} ms, gain "
                          f"{gain:.2f}{' (locked)' if locked else ''}, events {n_events}",
                          flush=True)
                    last_print = now
                continue
            view = draw(np.ascontiguousarray(bgr), r)
            colour = (0, 0, 255) if r.active else (0, 220, 255) if r.boxes else (255, 255, 255)
            bar = np.zeros((44, view.shape[1], 3), np.uint8)
            state = "MOTION" if r.active else ("candidate" if r.boxes else "watching")
            cv2.putText(bar, f"{name}   {state}   events {n_events}", (6, 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
            cv2.putText(bar, f"{fps:.0f} fps  changed {100 * r.changed:.1f}%  lux {lux:.0f}  "
                        f"exp {exp_ms:.1f} ms  gain {gain:.2f} {'locked' if locked else 'auto'}"
                        f"  (l: lock/auto)", (6, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (200, 200, 200), 1, cv2.LINE_AA)
            cv2.imshow(WINDOW, np.vstack([view, bar]))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("l"):
                if locked:
                    camera.auto_exposure()
                    locked = want_lock = False
                    print("[motion] exposure: auto", flush=True)
                else:
                    camera.lock_exposure(md)
                    locked = want_lock = True
                    motion.reset()
                    print(f"[motion] exposure locked: {exp_ms:.1f} ms, gain {gain:.2f}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        camera.close()
        if not args.headless:
            cv2.destroyAllWindows()
        print(f"[motion] stopped after {time.monotonic() - start:.0f} s: {n_events} event(s)",
              flush=True)


if __name__ == "__main__":
    main()
