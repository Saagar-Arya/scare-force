"""Debug window: camera frame with detection boxes and a status bar for the outputs.

Triggering detections are drawn red, everything else green.
"""

import cv2
import numpy as np

from .detector import Detection

WINDOW = "Scare Force"
BAR_H = 56
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


def show(frame: np.ndarray, detections: list[Detection], triggers: list[Detection],
         status: list[tuple[str, str, bool]], line2: str) -> bool:
    """Draw and show one frame. status = [(name, value, active), ...]. Returns False on q/Esc."""
    img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    for det in detections:
        draw_box(img, det, RED if det in triggers else GREEN)

    bar = np.zeros((BAR_H, img.shape[1], 3), np.uint8)
    x = 8
    for name, value, active in status:
        text = f"{name}: {value}"
        cv2.putText(bar, text, (x, 22), FONT, 0.55, YELLOW if active else GRAY, 1, cv2.LINE_AA)
        x += cv2.getTextSize(text, FONT, 0.55, 1)[0][0] + 20
    cv2.putText(bar, line2, (8, 46), FONT, 0.5, WHITE, 1, cv2.LINE_AA)

    cv2.imshow(WINDOW, np.vstack([img, bar]))
    return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)


def close() -> None:
    cv2.destroyAllWindows()
