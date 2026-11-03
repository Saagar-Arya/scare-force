"""Light: one GPIO pin driving a relay. start() runs the configured pattern for a scare.

Patterns (light.pattern in config.yaml; ported from scare-force-plan deterrence/light.py):
    steady  on for the whole scare (the original behavior: start() is just on()).
    strobe  on for duty/rate_hz, off for (1-duty)/rate_hz, repeating.
    random  as strobe, but every on and off time is scaled by its own random factor in
            [1-jitter, 1+jitter]: varied timing slows habituation. The seed is printed.
The first flash comes on at once. Flashing runs in a background thread on a monotonic schedule
(no drift); a wake-up more than 20 ms late restarts the schedule from now instead of catching up.
on(), off() and strobe() stop it first, so off() always leaves the light off.

Settings are checked before the pin is claimed (ValueError): rate_hz above max_rate_hz (3 Hz:
flashing at 3-60 Hz is a seizure hazard for photosensitive people), and with driver: relay an on
or off time (worst-case jitter included) shorter than relay_min_toggle_s.

Self-test: python -m scare.light   (on 2 s, strobe 2 s, then the configured pattern 3 s, off)
"""

import random
import threading
import time

from gpiozero import DigitalOutputDevice

DRIVERS = ("mosfet", "relay")
PATTERNS = ("steady", "strobe", "random")
OPTIONS = ("pattern", "driver", "rate_hz", "duty", "jitter", "max_rate_hz", "relay_min_toggle_s")
EPS = 1e-9  # so a setting exactly at a limit isn't rejected by float rounding
LATE_TOLERANCE_S = 0.02


class Light:
    def __init__(self, pin: int, active_high: bool = True, *, pattern: str = "steady",
                 driver: str = "relay", rate_hz: float = 2.0, duty: float = 0.5,
                 jitter: float = 0.3, max_rate_hz: float = 3.0, relay_min_toggle_s: float = 0.15):
        self.pattern, self.driver = pattern, driver
        self.rate_hz, self.duty, self.jitter = float(rate_hz), float(duty), float(jitter)
        self._validate(float(max_rate_hz), float(relay_min_toggle_s))
        self.pin = pin
        self.on_level, self.off_level = ("HIGH", "LOW") if active_high else ("LOW", "HIGH")
        self.out = DigitalOutputDevice(pin, active_high=active_high, initial_value=False)
        self._lock = threading.Lock()  # a flash can't turn the light back on after a stop
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self.flashes = 0

    @classmethod
    def from_config(cls, cfg: dict) -> "Light":
        return cls(cfg["pin"], cfg.get("active_high", True),
                   **{k: cfg[k] for k in OPTIONS if k in cfg})

    def _validate(self, max_rate_hz: float, relay_min_toggle_s: float) -> None:
        if self.driver not in DRIVERS:
            raise ValueError(f"light.driver must be {' or '.join(DRIVERS)}, got {self.driver!r}")
        if self.pattern not in PATTERNS:
            raise ValueError(f"light.pattern must be {', '.join(PATTERNS)}, got {self.pattern!r}")
        if not 0 < self.duty < 1:
            raise ValueError(f"light.duty must be between 0 and 1 (exclusive), got {self.duty:g}")
        if self.rate_hz <= 0:
            raise ValueError(f"light.rate_hz must be > 0, got {self.rate_hz:g}")
        if not 0 <= self.jitter <= 0.5:
            raise ValueError(f"light.jitter must be between 0 and 0.5, got {self.jitter:g}")
        jitter = self.jitter if self.pattern == "random" else 0.0
        fastest = self.rate_hz / (1 - jitter)
        if fastest > max_rate_hz + EPS:
            raise ValueError(f"light: fastest flash {fastest:.2f} Hz is above max_rate_hz "
                             f"{max_rate_hz:g}, the photosensitive-seizure safety ceiling")
        if self.driver == "relay" and self.pattern != "steady":
            shortest = min(self.duty, 1 - self.duty) / self.rate_hz * (1 - jitter)
            if shortest < relay_min_toggle_s - EPS:
                raise ValueError(f"light: the relay's shortest on/off time would be "
                                 f"{shortest:.3f}s, under relay_min_toggle_s {relay_min_toggle_s:g}s")

    def start(self) -> None:
        """Start the configured pattern; it runs until on(), off() or strobe() is called."""
        if self.pattern == "steady":
            self.on()
            return
        self._stop_flashing()
        seed = random.getrandbits(32)
        info = f"{self.pattern} {self.rate_hz:g} Hz duty {self.duty:g}"
        if self.pattern == "random":
            info += f" jitter {self.jitter:g} seed {seed}"
        print(f"[light] GPIO{self.pin} -> {info}", flush=True)
        self.flashes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flash, args=(self._stop, random.Random(seed)),
                                        name="light", daemon=True)
        self._thread.start()

    def _flash(self, stop: threading.Event, rng: random.Random) -> None:
        phases = ((True, self.duty / self.rate_hz), (False, (1 - self.duty) / self.rate_hz))
        due = time.monotonic()  # when the current phase is due to start
        while True:
            for lamp, nominal in phases:
                now = time.monotonic()
                if now - due > LATE_TOLERANCE_S:
                    due = now  # stalled: start over from now rather than catch up
                with self._lock:
                    if stop.is_set():
                        return
                    self.out.on() if lamp else self.out.off()
                if lamp:
                    self.flashes += 1
                if self.pattern == "random":
                    nominal *= rng.uniform(1 - self.jitter, 1 + self.jitter)
                due += nominal
                if stop.wait(max(0.0, due - time.monotonic())):
                    return

    def _stop_flashing(self) -> None:
        if self._thread is None:
            return
        with self._lock:
            self._stop.set()
        self._thread.join(timeout=1.0)
        print(f"[light] GPIO{self.pin} {self.pattern} stopped after {self.flashes} flashes",
              flush=True)
        self._thread = None

    def on(self) -> None:
        self._stop_flashing()
        print(f"[light] GPIO{self.pin} -> {self.on_level} (on)", flush=True)
        self.out.on()

    def off(self) -> None:
        self._stop_flashing()
        print(f"[light] GPIO{self.pin} -> {self.off_level} (off)", flush=True)
        self.out.off()

    def strobe(self, period: float) -> None:
        """Toggle every `period` seconds until on() or off() is called."""
        self._stop_flashing()
        print(f"[light] GPIO{self.pin} -> strobe every {period}s", flush=True)
        self.out.blink(on_time=period, off_time=period, background=True)

    @property
    def is_on(self) -> bool:
        return bool(self.out.value)

    def close(self) -> None:
        self._stop_flashing()
        self.out.close()


if __name__ == "__main__":
    from . import load_config

    light = Light.from_config(load_config()["light"])
    light.on()
    time.sleep(2)
    light.strobe(0.25)
    time.sleep(2)
    light.start()
    time.sleep(3)
    light.off()
    light.close()
