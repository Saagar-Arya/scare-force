"""Camera Module 3 on the CSI port. read() returns one RGB frame as a (h, w, 3) uint8 array.

Self-test: python -m scare.camera   (grabs a frame, saves it to camera_test.jpg)
"""

import numpy as np


class Camera:
    def __init__(self, width: int = 640, height: int = 480):
        from picamera2 import Picamera2

        self.cam = Picamera2()
        self.cam.configure(
            self.cam.create_preview_configuration(main={"size": (width, height), "format": "RGB888"})
        )
        self.cam.start()

    def read(self) -> np.ndarray:
        # picamera2's "RGB888" is BGR in memory; flip to RGB
        return self.cam.capture_array()[..., ::-1].copy()

    def close(self) -> None:
        self.cam.stop()
        self.cam.close()


if __name__ == "__main__":
    import cv2

    from . import load_config

    cfg = load_config()["camera"]
    cam = Camera(cfg["width"], cfg["height"])
    frame = cam.read()
    cam.close()
    cv2.imwrite("camera_test.jpg", frame[..., ::-1])
    print(f"[camera] frame {frame.shape} saved to camera_test.jpg")
