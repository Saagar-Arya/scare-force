"""Light: one GPIO pin driving a relay. start(deadline) runs the configured pattern for a scare.

The relay only switches the lamp on or off: there is no dimming.

Patterns (light.pattern in config.yaml; ported from scare-force-plan deterrence/light.py):
    steady  on for the whole scare (the original behavior: start() is just on()).
    strobe  on for duty/rate_hz, off for (1-duty)/rate_hz, repeating.
    random  as strobe, but every on and off time is scaled by its own random factor in
            [1-jitter, 1+jitter]: varied timing slows habituation. The seed is printed.
Whole flashes only, ending with the scare: start(deadline) plans the scare's flashes so the
first comes on at once and the last goes off exactly at `deadline` (time.monotonic(); the end
the sound chose, see scare/deterrent.py). To fit a whole number of flashes, the period is
stretched or shrunk slightly (2 Hz over 4.76 s: 10 flashes at 2.00 Hz; over 5.34 s: 11 at
2.03 Hz), never past max_rate_hz or under the relay minimum; random scales its jittered times to
fit the same way. Steady is one flash: on at once, off at the deadline. Flashing runs in a
background thread on that absolute schedule; if a wake-up is late, a flash that could no longer
stay on for the relay minimum is skipped (never a half flash), and an off is never delayed.
on() and off() stop it first, so off() always leaves the light off. finish(grace_s) lets the
last flash end on schedule, then turns the light off.

Relay protection: no setting may switch the relay on or off for less than RELAY_MIN_TOGGLE_S
(0.15 s); light.relay_min_toggle_s can only raise that. An off is always immediate, but the light
never comes back on within that time of the last off (e.g. two scares back to back).

check(cfg) validates the settings without touching the pin (ValueError listing every problem):
rate_hz above max_rate_hz (3 Hz: flashing at 3-60 Hz is a seizure hazard for photosensitive
people) and an on or off time (worst-case jitter included) under the relay minimum. The
controller calls it before opening the cameras; the constructor calls it again.

close() leaves the light off and hands the pin back with the pull it had at boot (pull-down when
active high), so the relay input doesn't float: gpiozero alone leaves it floating, reading HIGH.

Self-test: python -m scare.light   (on 2 s, off 1 s, then the configured pattern for 3 s)
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


def plan(pattern, rate_hz, duty, jitter, seconds, max_rate_hz, min_toggle_s, rng=None):
    """A scare's flashes as [(on_s, off_s), ...] from its start: the first on at 0, the last off
    at `seconds`, every flash whole. The flash count is the one nearest the configured rate whose
    stretched period keeps every flash under max_rate_hz and every on/off time at least
    min_toggle_s (random: tries each count with fresh jitter). [] if nothing fits."""
    if seconds < min_toggle_s - EPS:
        return []
    if pattern == "steady":
        return [(0.0, seconds)]
    rng = rng or random.Random()
    ideal = seconds * rate_hz + 1 - duty  # flash count at exactly rate_hz
    first = max(1, round(ideal))
    counts = [first] + [n for k in range(1, 50) for n in (first - k, first + k) if n >= 1]
    for n in counts:
        if pattern == "random":
            on = [duty / rate_hz * rng.uniform(1 - jitter, 1 + jitter) for _ in range(n)]
            off = [(1 - duty) / rate_hz * rng.uniform(1 - jitter, 1 + jitter)
                   for _ in range(n - 1)]
        else:
            on, off = [duty / rate_hz] * n, [(1 - duty) / rate_hz] * (n - 1)
        scale = seconds / (sum(on) + sum(off))
        on, off = [x * scale for x in on], [x * scale for x in off]
        periods = [a + b for a, b in zip(on, off)]
        if min(on + off) < min_toggle_s - EPS:
            continue
        if periods and 1 / min(periods) > max_rate_hz + EPS:
            continue
        flashes, t = [], 0.0
        for i, a in enumerate(on):
            flashes.append((t, t + a))
            t += a + (off[i] if i < len(off) else 0.0)
        flashes[-1] = (flashes[-1][0], seconds)  # exactly, despite float sums
        return flashes
    return []


def _problems(pattern, rate_hz, duty, jitter, max_rate_hz, relay_min_toggle_s) -> list[str]:
    """Everything wrong with a set of light settings ([] if they're fine)."""
    rate_hz, duty, jitter = float(rate_hz), float(duty), float(jitter)
    max_rate_hz, min_toggle = float(max_rate_hz), float(relay_min_toggle_s)
    p = []
    if min_toggle < RELAY_MIN_TOGGLE_S - EPS:
        p.append(f"light.relay_min_toggle_s {min_toggle:g}s is under the relay's "
                 f"{RELAY_MIN_TOGGLE_S:g}s floor")
    if pattern not in PATTERNS:
        p.append(f"light.pattern must be {', '.join(PATTERNS)}, got {pattern!r}")
    if not 0 < duty < 1:
        p.append(f"light.duty must be between 0 and 1 (exclusive), got {duty:g}")
    if rate_hz <= 0:
        p.append(f"light.rate_hz must be > 0, got {rate_hz:g}")
    if not 0 <= jitter <= 0.5:
        p.append(f"light.jitter must be between 0 and 0.5, got {jitter:g}")
    if p or pattern == "steady":
        return p
    jitter = jitter if pattern == "random" else 0.0
    fastest = rate_hz / (1 - jitter)
    if fastest > max_rate_hz + EPS:
        p.append(f"fastest flash {fastest:.2f} Hz is above max_rate_hz {max_rate_hz:g}, "
                 "the photosensitive-seizure safety ceiling")
    shortest = min(duty, 1 - duty) / rate_hz * (1 - jitter)
    if shortest < min_toggle - EPS:
        p.append(f"the relay's shortest on/off time would be {shortest:.3f}s, under the "
                 f"{min_toggle:g}s minimum")
    return p


class Light:
    def __init__(self, cfg: dict):
        """cfg: config.yaml's light section. Refuses bad settings before touching the pin."""
        self.check(cfg)
        self.pattern = cfg["pattern"]
        self.rate_hz, self.duty = float(cfg["rate_hz"]), float(cfg["duty"])
        self.jitter = float(cfg["jitter"])
        self.min_toggle_s = float(cfg["relay_min_toggle_s"])
        self.max_rate_hz = float(cfg["max_rate_hz"])
        from gpiozero import DigitalOutputDevice

        self.pin, self.active_high = cfg["pin"], cfg["active_high"]
        self.on_level, self.off_level = ("HIGH", "LOW") if self.active_high else ("LOW", "HIGH")
        self.out = DigitalOutputDevice(self.pin, active_high=self.active_high, initial_value=False)
        self._lock = threading.Lock()  # a flash can't turn the light back on after a stop
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._off_t = -1e9            # monotonic time of the last switch off
        self.flashes = 0
        self.first_on_t: float | None = None  # monotonic time start()'s pattern first lit the lamp
        self.last_off_t: float | None = None  # monotonic time its last flash went off

    @staticmethod
    def check(cfg: dict) -> None:
        """Validate config.yaml's light section without touching the pin (ValueError listing
        every problem)."""
        try:
            problems = _problems(**{k: cfg[k] for k in OPTIONS})
        except KeyError as e:
            problems = [f"light.{e.args[0]} is missing from config.yaml"]
        except (TypeError, ValueError) as e:
            problems = [f"light: bad number ({e})"]
        if problems:
            raise ValueError("light settings refused: " + "; ".join(problems))

    def _hold_off(self, stop: threading.Event | None = None) -> bool:
        """Wait until the light has been off for the relay minimum. False if stopped meanwhile."""
        wait = self._off_t + self.min_toggle_s - time.monotonic()
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

    def start(self, deadline: float) -> None:
        """Run the configured pattern as whole flashes until `deadline` (time.monotonic())."""
        self._stop_flashing()
        self.first_on_t = self.last_off_t = None
        self.flashes = 0
        seed = random.getrandbits(32)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flash,
                                        args=(self._stop, deadline, random.Random(seed), seed),
                                        name="light", daemon=True)
        self._thread.start()

    def _flash(self, stop: threading.Event, deadline: float, rng: random.Random,
               seed: int) -> None:
        if not self._hold_off(stop):  # the relay minimum after the last off (back-to-back)
            return
        t0 = time.monotonic()
        flashes = plan(self.pattern, self.rate_hz, self.duty, self.jitter, deadline - t0,
                       self.max_rate_hz, self.min_toggle_s, rng)
        n = len(flashes)
        rate = "" if n < 2 else f" at {(n - 1) / flashes[-1][0]:.2f} Hz"
        info = f"{self.pattern}, {n} flash(es){rate}, {deadline - t0:.2f} s"
        if self.pattern == "random":
            info += f", jitter {self.jitter:g} seed {seed}"
        print(f"[light] GPIO{self.pin} -> {info}", flush=True)
        for on_s, off_s in flashes:
            on_t, off_t = t0 + on_s, t0 + off_s
            if stop.wait(max(0.0, on_t - time.monotonic())):
                return
            with self._lock:
                if stop.is_set():
                    return
                if off_t - time.monotonic() < self.min_toggle_s - EPS:
                    continue  # too late to give this flash a whole on-time: skip it
                self._switch(True)
            self.flashes += 1
            stopped = stop.wait(max(0.0, off_t - time.monotonic()))
            with self._lock:
                self._switch(False)  # an off is never skipped, even when stopped
                self.last_off_t = self._off_t
            if stopped:
                return
        print(f"[light] GPIO{self.pin} -> {self.off_level} (off): {self.flashes} whole flash(es) "
              "done", flush=True)

    def finish(self, grace_s: float) -> None:
        """At a scare's end: let the last flash go off on schedule (up to grace_s), then off()."""
        if self._thread is not None:
            self._thread.join(timeout=grace_s)
            if not self._thread.is_alive():
                self._thread = None
        self.off()

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

    light = Light(load_config()["light"])
    try:
        light.on()
        time.sleep(2)
        light.off()
        time.sleep(1)
        light.start(time.monotonic() + 3.0)
        light.finish(3.5)
    finally:
        light.close()
