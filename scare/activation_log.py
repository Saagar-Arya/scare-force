"""Activation log: one CSV row per scare, written when the scare ends (log.activations in
config.yaml; null = no file, terminal output only). scare/deterrent.py fills it in.

Columns:
    start, end                 local ISO 8601 times, to the millisecond; end = light off (and
                               aplay told to stop), not when the row was written
    end_reason                 timeout | person_abort | shutdown (Ctrl+C, SIGTERM, an error)
    trigger_source             detection (scare.controller) | manual (scare.deter_test)
    label, confidence, camera  the detection that fired it (empty for manual)
    light_pattern, flashes     steady | strobe | random | off (no light); times the lamp came on
    clip_id, sound_type        empty with no sound
    aplay_rc                   aplay's exit code (0 also when stopped: aplay exits 0 on SIGTERM)
    sound_s, sound_stopped     seconds the clip played; 1 if the scare's end cut it off
    trigger_to_relay_on_ms     trigger -> GPIO driven on for the first time
    trigger_to_aplay_start_ms  trigger -> aplay process started (not the first sample out)
The header is written when the file is new or empty. A file with a different header (an older
version's) is renamed aside to <name>.<its mtime>.csv first. Each row is flushed and fsynced.
A power cut loses only the scare in progress.
"""

import csv
import os
import time
from pathlib import Path

FIELDS = ("start", "end", "end_reason", "trigger_source", "label", "confidence", "camera",
          "light_pattern", "flashes", "clip_id", "sound_type", "aplay_rc", "sound_s",
          "sound_stopped", "trigger_to_relay_on_ms", "trigger_to_aplay_start_ms")


class ActivationLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            with open(self.path, newline="") as f:
                header = next(csv.reader(f), None)
            if tuple(header or ()) != FIELDS:
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.path.stat().st_mtime))
                old = self.path.with_name(f"{self.path.stem}.{stamp}{self.path.suffix}")
                self.path.rename(old)
                print(f"[log] {self.path.name} had other columns: moved it to {old.name}", flush=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self.file = open(self.path, "a", newline="")
        self.csv = csv.writer(self.file)
        if new:
            self._write(FIELDS)
        print(f"[log] activations -> {self.path}", flush=True)

    def write(self, row: dict) -> None:
        """One scare; row keys are FIELDS (missing ones are left empty)."""
        self._write(["" if row.get(k) is None else row[k] for k in FIELDS])

    def _write(self, row) -> None:
        self.csv.writerow(row)
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()
