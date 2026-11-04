"""Cameras on the CSI port: one, or several on an Arducam Multi Camera Adapter V2.2 (two Camera
Module 3s and an IMX219-160 IR-CUT).

The Pi 4 has one CSI port, so with the adapter only one camera streams at a time. select(i) closes
the open camera and opens the i-th one (about 0.5 s; libcamera keeps each camera's exposure and
white balance, so the first frame is already usable). read() returns one RGB frame from the open
camera as a (h, w, 3) uint8 array.

Self-test: python -m scare.camera   (a frame from every camera -> camera_test_<n>.jpg)
"""

import os
import re

import numpy as np

# libcamera logs a few INFO lines on every open; switching cameras would bury the controller's log
os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:WARN")


class Camera:
    def __init__(self, cfg: dict):
        """cfg: config.yaml's camera section."""
        from picamera2 import Picamera2

        ids, exposure_value = cfg["ids"], cfg["exposure_value"]
        # Picamera2(n) opens the n-th entry of this (sorted) list, which is not always the one whose
        # "Num" is n, so go by position. Same order as `rpicam-hello --list-cameras`.
        info = Picamera2.global_camera_info()
        found = list(range(len(info)))
        self.ids = [n for n in ids if n in found]  # libcamera camera numbers, in switching order
        # "camera 2 (port B imx219)": the adapter's i2c@0..3 in the camera's Id are ports A..D
        self.names = []
        for n in self.ids:
            m = re.search(r"pca@70/i2c@(\d)/", info[n]["Id"])
            port = f"port {'ABCD'[int(m.group(1))]} " if m else ""
            self.names.append(f"camera {n} ({port}{info[n]['Model']})")
        self.models = [info[n]["Model"] for n in self.ids]
        self.sensor_modes = {k: tuple(v) for k, v in (cfg.get("sensor_modes") or {}).items()}
        for n in ids:
            if n not in found:
                print(f"[camera] camera {n} not found (have {found}), skipping it", flush=True)
        if not self.ids:
            raise RuntimeError(f"none of cameras {list(ids)} found (have {found})")
        if not -8.0 <= exposure_value <= 8.0:
            raise ValueError(f"exposure_value must be in [-8, 8] stops, got {exposure_value}")
        self.ev = float(exposure_value)  # auto-exposure compensation, applied to every camera
        self.size = (cfg["width"], cfg["height"])
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
        mode = self.sensor_modes.get(self.models[i])
        self.cam.configure(
            self.cam.create_preview_configuration(main={"size": self.size, "format": "RGB888"},
                                                  sensor={"output_size": mode} if mode else {},
                                                  controls={"ExposureValue": self.ev})
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

    cam = Camera(load_config()["camera"])
    for i, n in enumerate(cam.ids):
        t = time.perf_counter()
        cam.select(i)
        frame = cam.read()
        cv2.imwrite(f"camera_test_{n}.jpg", frame[..., ::-1])
        print(f"[camera] {cam.names[i]}: frame {frame.shape} saved to camera_test_{n}.jpg "
              f"({(time.perf_counter() - t) * 1000:.0f} ms)")
    cam.close()
