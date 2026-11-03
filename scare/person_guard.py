"""Reject animal/person predictions for approximately the same object."""

import math


class PersonGuard:
    def __init__(self, confidence: float, hold_seconds: float, same_object_iou: float = 0.5):
        if not math.isfinite(confidence) or not 0 < confidence <= 1:
            raise ValueError("Person confidence must be in (0, 1].")
        if not math.isfinite(hold_seconds) or hold_seconds < 0:
            raise ValueError("Person hold_seconds must be finite and nonnegative.")
        if not math.isfinite(same_object_iou) or not 0 < same_object_iou <= 1:
            raise ValueError("Person same_object_iou must be in (0, 1].")
        self.confidence = confidence
        self.hold_seconds = hold_seconds
        self.same_object_iou = same_object_iou
        self.regions = []

    def update(self, detections, now: float) -> None:
        # Retain recent locations to tolerate brief missed person detections.
        self.regions = [(box, expiry) for box, expiry in self.regions if expiry > now]
        for d in detections:
            if d.label == "person" and d.confidence >= self.confidence:
                self.regions.append((tuple(d.box), now + self.hold_seconds))

    def blocks(self, detection) -> bool:
        """Person wins only when the two boxes have sufficiently similar extents.

        A small held animal inside a much larger person box has low IoU and
        remains eligible. Scores from the two models are not compared.
        """
        x1, y1, x2, y2 = detection.box
        animal_area = max(0, x2 - x1) * max(0, y2 - y1)
        if not animal_area:
            return False
        for (px1, py1, px2, py2), _ in self.regions:
            intersection = (max(0, min(x2, px2) - max(x1, px1)) *
                            max(0, min(y2, py2) - max(y1, py1)))
            person_area = max(0, px2 - px1) * max(0, py2 - py1)
            union = animal_area + person_area - intersection
            if union > 0 and intersection / union >= self.same_object_iou:
                return True
        return False
