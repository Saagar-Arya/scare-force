"""YOLO object detection (Ultralytics ONNX export on ONNX Runtime).

detect(frame) returns a list of Detection(label, confidence, box), box = (x1, y1, x2, y2) in
frame pixels, highest confidence first.

Self-test: python -m scare.detector [image.jpg]   (no image: uses one camera frame)
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Detection:
    label: str
    confidence: float
    box: tuple[int, int, int, int]


def letterbox(frame: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, tuple]:
    """Resize keeping aspect ratio, pad to size (w, h) with gray. Returns image, scale, pad."""
    h, w = frame.shape[:2]
    scale = min(size[0] / w, size[1] / h)
    nw, nh = round(w * scale), round(h * scale)
    pad_x, pad_y = (size[0] - nw) / 2, (size[1] - nh) / 2
    img = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    img = cv2.copyMakeBorder(
        img, round(pad_y - 0.1), round(pad_y + 0.1), round(pad_x - 0.1), round(pad_x + 0.1),
        cv2.BORDER_CONSTANT, value=(114, 114, 114),
    )
    return img, scale, (pad_x, pad_y)


class Detector:
    def __init__(self, model: Path, labels: Path, num_threads: int = 4,
                 conf_threshold: float = 0.25, iou_threshold: float = 0.45):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = num_threads
        self.session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_size = (inp.shape[3], inp.shape[2])  # exported with a fixed (w, h)
        self.labels = Path(labels).read_text().splitlines()
        self.conf, self.iou = conf_threshold, iou_threshold

    def detect(self, frame: np.ndarray) -> list[Detection]:
        img, scale, (pad_x, pad_y) = letterbox(frame, self.input_size)
        x = img.astype(np.float32).transpose(2, 0, 1)[np.newaxis] / 255.0
        out = self.session.run(None, {self.input_name: x})[0][0].T  # (N, 4 + classes)

        class_ids = out[:, 4:].argmax(axis=1)
        scores = out[np.arange(len(out)), 4 + class_ids]
        keep = scores >= self.conf
        cxcywh, scores, class_ids = out[keep, :4], scores[keep], class_ids[keep]

        xywh = cxcywh.copy()
        xywh[:, :2] -= xywh[:, 2:] / 2
        idx = cv2.dnn.NMSBoxesBatched(
            xywh.tolist(), scores.tolist(), class_ids.tolist(), self.conf, self.iou
        )
        idx = np.array(idx, dtype=int).reshape(-1)
        idx = idx[np.argsort(scores[idx])[::-1]]

        h, w = frame.shape[:2]
        boxes = np.concatenate([xywh[idx, :2], xywh[idx, :2] + xywh[idx, 2:]], axis=1)
        boxes = ((boxes - [pad_x, pad_y, pad_x, pad_y]) / scale).clip(0, [w, h, w, h]).round()
        return [
            Detection(self.labels[c], float(s), tuple(int(v) for v in b))
            for c, s, b in zip(class_ids[idx], scores[idx], boxes)
        ]


if __name__ == "__main__":
    import sys
    import time

    from . import ROOT, load_config

    cfg = load_config()
    d = cfg["detector"]
    det = Detector(ROOT / d["model"], ROOT / d["labels"], d["num_threads"],
                   d["conf_threshold"], d["iou_threshold"])
    if len(sys.argv) > 1:
        frame = cv2.imread(sys.argv[1])[..., ::-1]
    else:
        from .camera import Camera

        cam = Camera(cfg["camera"]["width"], cfg["camera"]["height"])
        frame = cam.read()
        cam.close()
    t = time.perf_counter()
    found = det.detect(frame)
    print(f"[detector] {len(found)} detections in {(time.perf_counter() - t) * 1000:.0f} ms")
    for f in found:
        print(f"  {f.label:15s} {f.confidence:.2f}  {f.box}")
