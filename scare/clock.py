"""Is the system clock trustworthy? The Pi 4 has no battery-backed clock (RTC).

With network it sets the time by NTP. Without network, after a boot it resumes from the time
fake-hwclock last saved (about hourly, and at shutdown), so every timestamp is behind by how long
it was off, and nothing else says so. This module reads the kernel's view of that:

    clock.synced()     "yes" | "no" | "unknown"   (timedatectl NTPSynchronized; cached)
    clock.report()     one startup line, a warning when the clock isn't synchronized

"no" means the time came from fake-hwclock (or an RTC, once one is fitted: with an RTC, "no"
offline is expected and the time is still good). "unknown": no timedatectl (not a Pi/systemd).
The value is refreshed every REFRESH_S by a background thread, so a scare never waits on it.
detection_log writes it as the clock_synced column; export warns about rows with "no".
"""

import shutil
import subprocess
import threading
import time
from datetime import datetime

REFRESH_S = 30.0
_state = {"value": None, "t": 0.0}
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _query() -> str:
    exe = shutil.which("timedatectl")
    if not exe:
        return "unknown"
    try:
        out = subprocess.run([exe, "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=3).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out if out in ("yes", "no") else "unknown"


def _refresh_loop() -> None:
    while True:
        v = _query()
        with _lock:
            _state["value"], _state["t"] = v, time.monotonic()
        time.sleep(REFRESH_S)


def synced() -> str:
    """Last known NTP sync state; starts the refresher on first use (never blocks after that)."""
    global _thread
    with _lock:
        v = _state["value"]
    if v is None:
        v = _query()
        with _lock:
            _state["value"], _state["t"] = v, time.monotonic()
        if _thread is None:
            _thread = threading.Thread(target=_refresh_loop, name="clock", daemon=True)
            _thread.start()
    return v


def report() -> str:
    v = synced()
    now = datetime.now().astimezone()
    line = f"[clock] {now:%Y-%m-%d %H:%M:%S %Z}, synchronized: {v}"
    if v == "no":
        line += (" -- WARNING: no network time since boot; photo and log times may be behind "
                 "(check `timedatectl`, connect to a network, or note the true time now)")
    print(line, flush=True)
    return v


if __name__ == "__main__":
    report()
