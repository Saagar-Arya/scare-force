"""Camera Module 3s on the CSI port: one, or several on an Arducam Multi Camera Adapter V2.2.

The Pi 4 has one CSI port, so with the adapter only one camera streams at a time. select(i) closes
the open camera and opens the i-th one (about 0.5 s; libcamera keeps each camera's exposure and
white balance, so the first frame is already usable). read() returns one RGB frame from the open
camera as a (h, w, 3) uint8 array.

Self-test: python -m scare.camera   (a frame from every camera -> camera_test_<n>.jpg)
"""

import os

import numpy as np

# libcamera logs a few INFO lines on every open; switching cameras would bury the controller's log
os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:WARN")


class Camera:
    def __init__(self, width: int = 640, height: int = 480, ids: list[int] = (0,)):
        from picamera2 import Picamera2

        found = [c["Num"] for c in Picamera2.global_camera_info()]
        self.ids = [n for n in ids if n in found]  # libcamera camera numbers, in switching order
        for n in ids:
            if n not in found:
                print(f"[camera] camera {n} not found (have {found}), skipping it", flush=True)
        if not self.ids:
            raise RuntimeError(f"none of cameras {list(ids)} found (have {found})")
        self.size = (width, height)
        self.cam = None
        self.current = None  # index into self.ids of the open camera
        self.select(0)

    def select(self, i: int) -> None:
        """Close the open camera and stream from self.ids[i] instead."""
        if i == self.current:
            return
        from picamera2 import Picamera2

        if self.cam is not None:
            self.cam.close()
        self.cam = Picamera2(self.ids[i])
        self.cam.configure(
            self.cam.create_preview_configuration(main={"size": self.size, "format": "RGB888"})
        )
        self.cam.start()
        self.current = i

    def read(self) -> np.ndarray:
        # picamera2's "RGB888" is BGR in memory; flip to RGB
        return self.cam.capture_array()[..., ::-1].copy()

    def close(self) -> None:
        if self.cam is not None:
            self.cam.close()
            self.cam = None


if __name__ == "__main__":
    import time

    import cv2

    from . import load_config

    cfg = load_config()["camera"]
    cam = Camera(cfg["width"], cfg["height"], cfg["ids"])
    for i, n in enumerate(cam.ids):
        t = time.perf_counter()
        cam.select(i)
        frame = cam.read()
        cv2.imwrite(f"camera_test_{n}.jpg", frame[..., ::-1])
        print(f"[camera] camera {n}: frame {frame.shape} saved to camera_test_{n}.jpg "
              f"({(time.perf_counter() - t) * 1000:.0f} ms)")
    cam.close()
