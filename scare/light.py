"""Light: one GPIO pin driving a relay. start() runs the configured pattern for a scare.

The relay only switches the lamp on or off: there is no dimming.

Patterns (light.pattern in config.yaml; ported from scare-force-plan deterrence/light.py):
    steady  on for the whole scare (the original behavior: start() is just on()).
    strobe  on for duty/rate_hz, off for (1-duty)/rate_hz, repeating.
    random  as strobe, but every on and off time is scaled by its own random factor in
            [1-jitter, 1+jitter]: varied timing slows habituation. The seed is printed.
The first flash comes on at once. Flashing runs in a background thread on a monotonic schedule
(no drift); a wake-up more than 20 ms late restarts the schedule from now instead of catching up.
on() and off() stop it first, so off() always leaves the light off.

Relay protection: no setting may switch the relay on or off for less than RELAY_MIN_TOGGLE_S
(0.15 s); light.relay_min_toggle_s can only raise that. An off is always immediate, but the light
never comes back on within that time of the last off (e.g. two scares back to back).

check(cfg) validates the settings without touching the pin (ValueError listing every problem):
rate_hz above max_rate_hz (3 Hz: flashing at 3-60 Hz is a seizure hazard for photosensitive
people) and an on or off time (worst-case jitter included) under the relay minimum. The
controller calls it before opening the cameras; the constructor calls it again.

close() leaves the light off and hands the pin back with the pull it had at boot (pull-down when
active high), so the relay input doesn't float: gpiozero alone leaves it floating, reading HIGH.

Self-test: python -m scare.light   (on 2 s, then the configured pattern 3 s, off)
"""

import random
import shutil
import subprocess
import threading
import time

PATTERNS = ("steady", "strobe", "random")
OPTIONS = ("pattern", "rate_hz", "duty", "jitter", "max_rate_hz", "relay_min_toggle_s")
RELAY_MIN_TOGGLE_S = 0.15  # floor: the relay never switches faster than this, whatever the config
EPS = 1e-9  # so a setting exactly at a limit isn't rejected by float rounding
LATE_TOLERANCE_S = 0.02


class Light:
    def __init__(self, pin: int, active_high: bool = True, *, pattern: str = "steady",
                 rate_hz: float = 2.0, duty: float = 0.5, jitter: float = 0.3,
                 max_rate_hz: float = 3.0, relay_min_toggle_s: float = RELAY_MIN_TOGGLE_S):
        self.pattern = pattern
        self.rate_hz, self.duty, self.jitter = float(rate_hz), float(duty), float(jitter)
        self.max_rate_hz = float(max_rate_hz)
        self.min_toggle_s = float(relay_min_toggle_s)
        problems = self._problems()
        if problems:
            raise ValueError("light settings refused: " + "; ".join(problems))
        from gpiozero import DigitalOutputDevice

        self.pin, self.active_high = pin, active_high
        self.on_level, self.off_level = ("HIGH", "LOW") if active_high else ("LOW", "HIGH")
        self.out = DigitalOutputDevice(pin, active_high=active_high, initial_value=False)
        self._lock = threading.Lock()  # a flash can't turn the light back on after a stop
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._off_t = -1e9            # monotonic time of the last switch off
        self.flashes = 0
        self.first_on_t: float | None = None  # monotonic time start()'s pattern first lit the lamp

    @classmethod
    def from_config(cls, cfg: dict) -> "Light":
        cls.check(cfg)
        return cls(cfg["pin"], cfg.get("active_high", True),
                   **{k: cfg[k] for k in OPTIONS if k in cfg})

    @classmethod
    def check(cls, cfg: dict) -> None:
        """Validate light settings (config.yaml's light section) without touching the pin."""
        problems = []
        try:
            probe = cls.__new__(cls)
            probe.pattern = cfg.get("pattern", "steady")
            probe.rate_hz = float(cfg.get("rate_hz", 2.0))
            probe.duty = float(cfg.get("duty", 0.5))
            probe.jitter = float(cfg.get("jitter", 0.3))
            probe.max_rate_hz = float(cfg.get("max_rate_hz", 3.0))
            probe.min_toggle_s = float(cfg.get("relay_min_toggle_s", RELAY_MIN_TOGGLE_S))
            problems += probe._problems()
        except (TypeError, ValueError) as e:
            problems.append(f"light: bad number ({e})")
        if problems:
            raise ValueError("light settings refused: " + "; ".join(problems))

    def _problems(self) -> list[str]:
        p = []
        if self.min_toggle_s < RELAY_MIN_TOGGLE_S - EPS:
            p.append(f"light.relay_min_toggle_s {self.min_toggle_s:g}s is under the relay's "
                     f"{RELAY_MIN_TOGGLE_S:g}s floor")
        min_toggle = max(self.min_toggle_s, RELAY_MIN_TOGGLE_S)
        if self.pattern not in PATTERNS:
            p.append(f"light.pattern must be {', '.join(PATTERNS)}, got {self.pattern!r}")
        if not 0 < self.duty < 1:
            p.append(f"light.duty must be between 0 and 1 (exclusive), got {self.duty:g}")
        if self.rate_hz <= 0:
            p.append(f"light.rate_hz must be > 0, got {self.rate_hz:g}")
        if not 0 <= self.jitter <= 0.5:
            p.append(f"light.jitter must be between 0 and 0.5, got {self.jitter:g}")
        if p or self.pattern == "steady":
            return p
        jitter = self.jitter if self.pattern == "random" else 0.0
        fastest = self.rate_hz / (1 - jitter)
        if fastest > self.max_rate_hz + EPS:
            p.append(f"fastest flash {fastest:.2f} Hz is above max_rate_hz {self.max_rate_hz:g}, "
                     "the photosensitive-seizure safety ceiling")
        shortest = min(self.duty, 1 - self.duty) / self.rate_hz * (1 - jitter)
        if shortest < min_toggle - EPS:
            p.append(f"the relay's shortest on/off time would be {shortest:.3f}s, under the "
                     f"{min_toggle:g}s minimum")
        return p

    @property
    def _min_toggle(self) -> float:
        return max(self.min_toggle_s, RELAY_MIN_TOGGLE_S)

    def _hold_off(self, stop: threading.Event | None = None) -> bool:
        """Wait until the light has been off for the relay minimum. False if stopped meanwhile."""
        wait = self._off_t + self._min_toggle - time.monotonic()
        if wait <= 0:
            return True
        if stop is None:
            time.sleep(wait)
            return True
        return not stop.wait(wait)

    def _switch(self, lamp: bool) -> None:
        if lamp:
            self.out.on()
            if self.first_on_t is None:
                self.first_on_t = time.monotonic()
        else:
            self.out.off()
            self._off_t = time.monotonic()

    def start(self) -> None:
        """Start the configured pattern; it runs until on() or off() is called."""
        self.first_on_t = None
        self.flashes = 0
        if self.pattern == "steady":
            self.on()
            self.flashes = 1
            return
        self._stop_flashing()
        seed = random.getrandbits(32)
        info = f"{self.pattern} {self.rate_hz:g} Hz duty {self.duty:g}"
        if self.pattern == "random":
            info += f" jitter {self.jitter:g} seed {seed}"
        print(f"[light] GPIO{self.pin} -> {info}", flush=True)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flash, args=(self._stop, random.Random(seed)),
                                        name="light", daemon=True)
        self._thread.start()

    def _flash(self, stop: threading.Event, rng: random.Random) -> None:
        if not self._hold_off(stop):
            return
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
                    self._switch(lamp)
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
        self._hold_off()
        print(f"[light] GPIO{self.pin} -> {self.on_level} (on)", flush=True)
        self._switch(True)

    def off(self) -> None:
        was_on = self._thread is not None or self.is_on
        self._stop_flashing()
        if was_on:
            print(f"[light] GPIO{self.pin} -> {self.off_level} (off)", flush=True)
        self._switch(False)

    @property
    def is_on(self) -> bool:
        return bool(self.out.value)

    def close(self) -> None:
        """Light off, pin released and put back to its boot pull. Safe to call more than once."""
        if self.out.closed:
            return
        self._stop_flashing()
        self.out.off()
        self.out.close()
        pull = "pd" if self.active_high else "pu"  # the pull that holds the relay off
        if shutil.which("pinctrl"):
            subprocess.run(["pinctrl", "set", str(self.pin), "ip", pull], check=False)
        print(f"[light] GPIO{self.pin} released (input, pull-{'down' if pull == 'pd' else 'up'})",
              flush=True)


if __name__ == "__main__":
    from . import load_config

    light = Light.from_config(load_config()["light"])
    try:
        light.on()
        time.sleep(2)
        light.start()
        time.sleep(3)
        light.off()
    finally:
        light.close()
