"""Two servos on the Pi 4's hardware PWM, written straight to /sys/class/pwm.

Setup: `dtoverlay=pwm,pin=19,func=2` in /boot/firmware/config.txt, reboot: channel 1 on GPIO19
(pin 35), chip 0. Both servos get the same pulse, so their signal wires share that pin. (GPIO18 is
one of the camera adapter's select lines; GPIO12/13 read as stuck at ground with it fitted.)

A servo wants a pulse every 20 ms (50 Hz); the pulse width sets the angle:
min_us -> 0 deg, max_us -> range_deg.

The PWM clock is shared with the 3.5 mm audio jack: the first sound played after boot switches it
from 50 MHz (what the kernel driver assumes) to 107 MHz until the next reboot, which makes every
pulse 2.14x too short and too frequent. So all timings are scaled by the real clock from
`vcgencmd measure_clock pwm`, re-checked at most once a second while the servos move.

Self-test: python -m scare.servos          (both up, wait, both down)
           python -m scare.servos sweep    (slow sweep 0 -> range_deg -> 0, twice)
           python -m scare.servos mid [deg]  (centre, then slow sweep centre +/- deg, default 60)
"""

import os
import subprocess
import sys
import time

PERIOD_US = 20_000            # 50 Hz servo frame
KERNEL_CLOCK_HZ = 50_000_000  # PWM clock rate the kernel driver assumes
CLOCK_CHECK_S = 1.0


def _write(path: str, value) -> None:
    with open(path, "w") as f:
        f.write(str(value))


class Servos:
    """Both servos move together: up(), down(), set(angle), close()."""

    def __init__(self, cfg: dict):
        self.min_us, self.max_us = cfg["min_us"], cfg["max_us"]
        self.range_deg = cfg["range_deg"]
        self.up_angle, self.down_angle = cfg["up_angle"], cfg["down_angle"]
        self.pins = [c["pin"] for c in cfg["channels"].values()]
        self.dirs = []
        for c in cfg["channels"].values():
            chip = f"/sys/class/pwm/pwmchip{c['chip']}"
            if not os.path.exists(f"{chip}/pwm{c['channel']}"):
                _write(f"{chip}/export", c["channel"])
                time.sleep(0.5)  # let udev hand the new files to the gpio group
            self.dirs.append(f"{chip}/pwm{c['channel']}")
        self.scale = None      # real PWM clock / what the kernel assumes
        self.checked = 0.0     # time.monotonic() of the last clock check
        self.angle = None      # None = not driving (servos limp)
        self._check_clock()

    def _check_clock(self) -> None:
        """Re-time the PWM if the real clock differs from what the kernel assumes."""
        self.checked = time.monotonic()
        out = subprocess.run(["vcgencmd", "measure_clock", "pwm"],
                             capture_output=True, text=True, check=True).stdout
        real = int(out.split("=")[1]) / KERNEL_CLOCK_HZ
        if self.scale is None or abs(real - self.scale) > 0.01:
            self.scale = real
            print(f"[servos] PWM clock is {real:.3f}x what the kernel assumes; scaling pulses",
                  flush=True)
            for d in self.dirs:
                _write(f"{d}/duty_cycle", 0)  # must stay <= period while it changes
                _write(f"{d}/period", round(PERIOD_US * 1000 * real))

    def _pulse_us(self, angle: float) -> float:
        return self.min_us + (self.max_us - self.min_us) * angle / self.range_deg

    def set(self, angle: float, quiet: bool = False) -> None:
        angle = max(0.0, min(self.range_deg, angle))
        pulse_us = self._pulse_us(angle)
        if not quiet:
            print(f"[servos] GPIO{'+'.join(map(str, self.pins))} -> {angle:.0f} deg "
                  f"({pulse_us:.0f} us pulse)", flush=True)
        if time.monotonic() - self.checked > CLOCK_CHECK_S:
            self._check_clock()
        for d in self.dirs:
            _write(f"{d}/duty_cycle", round(pulse_us * 1000 * self.scale))
            if self.angle is None:
                _write(f"{d}/enable", 1)
        self.angle = angle

    def up(self) -> None:
        self.set(self.up_angle)

    def down(self) -> None:
        self.set(self.down_angle)

    def sweep(self, seconds: float = 4.0) -> None:
        """0 -> range_deg -> 0, smoothly, in about `seconds`."""
        print(f"[servos] sweep 0 -> {self.range_deg} -> 0 deg", flush=True)
        steps = 100
        for i in list(range(steps + 1)) + list(range(steps, -1, -1)):
            self.set(self.range_deg * i / steps, quiet=True)
            time.sleep(seconds / (2 * steps))

    def ramp(self, target: float, deg_per_s: float = 30.0) -> None:
        """Current angle -> `target` in 1 deg steps at about `deg_per_s`."""
        start = self.angle
        if start is None:  # not driving yet, so nothing to ramp from
            self.set(target)
            return
        steps = max(1, round(abs(target - start)))
        print(f"[servos] GPIO{'+'.join(map(str, self.pins))} ramp {start:.0f} -> {target:.0f} deg "
              f"({self._pulse_us(target):.0f} us pulse) at {deg_per_s:.0f} deg/s", flush=True)
        for i in range(1, steps + 1):
            self.set(start + (target - start) * i / steps, quiet=True)
            time.sleep(abs(target - start) / steps / deg_per_s)

    def close(self) -> None:
        print(f"[servos] GPIO{'+'.join(map(str, self.pins))} -> PWM off", flush=True)
        for d in self.dirs:
            _write(f"{d}/enable", 0)
        self.angle = None


if __name__ == "__main__":
    from . import load_config

    servos = Servos(load_config()["servos"])
    try:
        if sys.argv[1:] == ["sweep"]:
            servos.set(0)
            time.sleep(1)
            for _ in range(2):
                servos.sweep()
                time.sleep(0.5)
        elif sys.argv[1:2] == ["mid"]:
            mid = servos.range_deg / 2
            amp = min(float(sys.argv[2]) if sys.argv[2:] else 60.0, mid)
            servos.set(mid)    # jumps from wherever the horn is; centre is the shortest worst case
            time.sleep(2)
            for _ in range(2):
                servos.ramp(mid + amp)
                servos.ramp(mid - amp)
                servos.ramp(mid)
                time.sleep(0.5)
            time.sleep(1)
        else:
            servos.up()
            time.sleep(1)
            servos.down()
            time.sleep(1)
    finally:
        servos.close()
