"""Activation log: one CSV row per scare, written the moment it starts (log.activations in
config.yaml; null = no file, terminal output only).

Columns: epoch_s, time (local ISO 8601), camera, label, confidence, clip, channels.
channels lists what the scare started, separated by ';' (e.g. light:strobe;sound;servos).
The header is written when the file is new or empty. Each row is flushed and fsynced, so a power
cut loses at most the scare in progress.
"""

import csv
import os
import time
from datetime import datetime
from pathlib import Path

FIELDS = ("epoch_s", "time", "camera", "label", "confidence", "clip", "channels")


class ActivationLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self.file = open(self.path, "a", newline="")
        self.csv = csv.writer(self.file)
        if new:
            self._write(FIELDS)
        print(f"[log] activations -> {self.path}", flush=True)

    def write(self, camera, label: str, confidence: float, clip: str, channels: list[str]) -> None:
        t = time.time()
        self._write((f"{t:.3f}", datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds"),
                     camera, label, f"{confidence:.3f}", clip, ";".join(channels)))

    def _write(self, row) -> None:
        self.csv.writerow(row)
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self) -> None:
        self.file.close()
