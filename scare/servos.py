"""The two arm servos, mirrored: each arm rests down and flaps up about servos.flap_deg (Nov 11).

Angles in this class are OFFSETS from rest: 0 = arms down, flap_deg = the peak (about 90 deg up,
i.e. roughly a T-pose). Each servo has its own calibrated `down` angle and `up_dir` (+1 / -1), so
one command moves the arms as mirror images even though the servos are mounted the same way:
    servo angle = down + up_dir * offset          (clamped to 0..range_deg)
An offset is always clamped to 0..flap_deg, so no command can drive an arm past its peak or below
its rest, whatever calls set().

Each servo needs its own signal for that (a shared Y cable gives both the same pulse, which makes
them windmill). Channel types (servos.channels in config.yaml):
    hw      the Pi 4's hardware PWM via /sys/class/pwm (GPIO19 = chip 0 channel 1, with
            `dtoverlay=pwm,pin=19,func=2`). The PWM clock is shared with the 3.5 mm audio jack:
            the first sound after boot moves it from 50 MHz (what the kernel assumes) to 107 MHz,
            so pulses are scaled by the real clock from `vcgencmd measure_clock pwm`.
    sysfs   kernel software PWM via /sys/class/pwm, not on the PWM clock, so never scaled
            (`dtoverlay=pwm-gpio,gpio=16`: a new pwmchip, channel 0). Steadier than lgpio under load.
    lgpio   user-space software PWM with lgpio's tx_servo (python3-lgpio): no overlay, no reboot;
            may twitch a little while the detector loads the CPU.
No free hardware PWM pin is left for a second servo: GPIO18 belongs to the camera adapter and
GPIO12/13 read stuck low (Nov 9 spark).

A servo wants a pulse every 20 ms (50 Hz); min_us -> 0 deg, max_us -> range_deg.

Self-test / calibration (servos.flap_deg etc. from config.yaml):
    python -m scare.servos                 down, up (peak), down
    python -m scare.servos flap [n]        n flaps (default 3) at controller.flap_seconds, then down
    python -m scare.servos sweep           down -> peak -> down, slowly
    python -m scare.servos cal NAME DEG    only servo NAME to absolute angle DEG, held 3 s
                                           (calibration: find each servo's `down` and `up_dir`)
"""

import os
import subprocess
import sys
import time

PERIOD_US = 20_000            # 50 Hz servo frame
KERNEL_CLOCK_HZ = 50_000_000  # PWM clock rate the kernel driver assumes
CLOCK_CHECK_S = 1.0
SYSFS_PWM = "/sys/class/pwm"


def _write(path: str, value) -> None:
    with open(path, "w") as f:
        f.write(str(value))


class _Sysfs:
    """One /sys/class/pwm channel. scaled: on the PWM clock the audio jack retimes (type hw)."""

    def __init__(self, chip: int, channel: int, scaled: bool):
        base = f"{SYSFS_PWM}/pwmchip{chip}"
        if not os.path.exists(f"{base}/pwm{channel}"):
            _write(f"{base}/export", channel)
            time.sleep(0.5)  # let udev hand the new files to the gpio group
        self.dir = f"{base}/pwm{channel}"
        self.scaled = scaled
        self.enabled = False

    def period(self, scale: float) -> None:
        s = scale if self.scaled else 1.0
        _write(f"{self.dir}/duty_cycle", 0)  # must stay <= period while it changes
        _write(f"{self.dir}/period", round(PERIOD_US * 1000 * s))

    def pulse(self, us: float, scale: float) -> None:
        s = scale if self.scaled else 1.0
        _write(f"{self.dir}/duty_cycle", round(us * 1000 * s))
        if not self.enabled:
            _write(f"{self.dir}/enable", 1)
            self.enabled = True

    def off(self) -> None:
        _write(f"{self.dir}/enable", 0)
        self.enabled = False


class _Lgpio:
    """lgpio tx_servo on one GPIO (software-timed, 50 Hz)."""

    def __init__(self, pin: int):
        import lgpio
        self.lg, self.pin = lgpio, pin
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.h, pin, 0)

    def period(self, scale: float) -> None:
        pass

    def pulse(self, us: float, scale: float) -> None:
        self.lg.tx_servo(self.h, self.pin, int(round(us)), 50)

    def off(self) -> None:
        self.lg.tx_servo(self.h, self.pin, 0)  # stop pulsing: the servo goes limp


class Servos:
    """Both arms together, as mirror images: up(), down(), rest(), set(offset), ramp(), close()."""

    def __init__(self, cfg: dict):
        self.min_us, self.max_us = cfg["min_us"], cfg["max_us"]
        self.range_deg = cfg["range_deg"]
        chans = cfg["channels"]
        if "flap_deg" in cfg:
            self.flap_deg = float(cfg["flap_deg"])
            self.chans = {n: dict(c) for n, c in chans.items()}
        else:  # pre-Nov 11 config: one shared signal, up_angle / down_angle for both
            self.flap_deg = abs(cfg["up_angle"] - cfg["down_angle"])
            sign = 1 if cfg["up_angle"] >= cfg["down_angle"] else -1
            self.chans = {n: {**c, "type": c.get("type", "hw"), "down": cfg["down_angle"],
                              "up_dir": sign} for n, c in chans.items()}
        self.down_angle, self.up_angle = 0.0, self.flap_deg  # offsets: what the controller uses
        self.pins = [c["pin"] for c in self.chans.values()]
        self.out = {}
        for name, c in self.chans.items():
            kind = c.get("type", "hw")
            if c.get("up_dir") not in (1, -1):
                raise ValueError(f"servos.channels.{name}.up_dir must be 1 or -1")
            if kind in ("hw", "sysfs"):
                self.out[name] = _Sysfs(c["chip"], c["channel"], scaled=(kind == "hw"))
            elif kind == "lgpio":
                self.out[name] = _Lgpio(c["pin"])
            else:
                raise ValueError(f"servos.channels.{name}.type: hw, sysfs or lgpio (got {kind!r})")
        self.has_hw = any(c.get("type", "hw") == "hw" for c in self.chans.values())
        self.scale = 1.0 if not self.has_hw else None  # real PWM clock / what the kernel assumes
        self.checked = 0.0     # time.monotonic() of the last clock check
        self.angle = None      # current offset; None = not driving (servos limp)
        if self.has_hw:
            self._check_clock()
        else:
            for o in self.out.values():
                o.period(1.0)
        print("[servos] " + ", ".join(
            f"{n}: GPIO{c['pin']} {c.get('type', 'hw')}, down {c['down']:g} deg, up "
            f"{'+' if c['up_dir'] > 0 else '-'}{self.flap_deg:g}" for n, c in self.chans.items()),
            flush=True)

    def _check_clock(self) -> None:
        """Re-time the hardware PWM if the real clock differs from what the kernel assumes."""
        self.checked = time.monotonic()
        out = subprocess.run(["vcgencmd", "measure_clock", "pwm"],
                             capture_output=True, text=True, check=True).stdout
        real = int(out.split("=")[1]) / KERNEL_CLOCK_HZ
        if self.scale is None or abs(real - self.scale) > 0.01:
            self.scale = real
            print(f"[servos] PWM clock is {real:.3f}x what the kernel assumes; scaling pulses",
                  flush=True)
            for o in self.out.values():
                o.period(real)

    def _pulse_us(self, angle: float) -> float:
        return self.min_us + (self.max_us - self.min_us) * angle / self.range_deg

    def _angle(self, name: str, offset: float) -> float:
        c = self.chans[name]
        return max(0.0, min(self.range_deg, c["down"] + c["up_dir"] * offset))

    def set(self, offset: float, quiet: bool = False) -> None:
        """Both arms to `offset` degrees above rest (0 = down, flap_deg = peak), mirrored."""
        offset = max(0.0, min(self.flap_deg, offset))
        if self.has_hw and time.monotonic() - self.checked > CLOCK_CHECK_S:
            self._check_clock()
        angles = {n: self._angle(n, offset) for n in self.chans}
        for n, a in angles.items():
            self.out[n].pulse(self._pulse_us(a), self.scale)
        if not quiet:
            print(f"[servos] {offset:.0f} deg above rest (" +
                  ", ".join(f"{n} {a:.0f}" for n, a in angles.items()) + ")", flush=True)
        self.angle = offset

    def up(self) -> None:
        self.set(self.up_angle)

    def down(self) -> None:
        self.set(self.down_angle)

    def rest(self) -> None:
        """Arms down (the rest pose)."""
        self.set(self.down_angle)

    def sweep(self, seconds: float = 4.0) -> None:
        """Rest -> peak -> rest, smoothly, in about `seconds`."""
        print(f"[servos] sweep 0 -> {self.flap_deg:g} -> 0 deg above rest", flush=True)
        steps = 100
        for i in list(range(steps + 1)) + list(range(steps, -1, -1)):
            self.set(self.flap_deg * i / steps, quiet=True)
            time.sleep(seconds / (2 * steps))

    def ramp(self, target: float, deg_per_s: float = 30.0) -> None:
        """Current offset -> `target` in 1 deg steps at about `deg_per_s`."""
        start = self.angle
        target = max(0.0, min(self.flap_deg, target))
        if start is None:  # not driving yet, so nothing to ramp from
            self.set(target)
            return
        steps = max(1, round(abs(target - start)))
        print(f"[servos] ramp {start:.0f} -> {target:.0f} deg above rest at {deg_per_s:.0f} deg/s",
              flush=True)
        for i in range(1, steps + 1):
            self.set(start + (target - start) * i / steps, quiet=True)
            time.sleep(abs(target - start) / steps / deg_per_s)

    def calibrate(self, name: str, angle: float) -> None:
        """Calibration only: servo `name` alone to absolute `angle` (0..range_deg)."""
        if name not in self.chans:
            raise SystemExit(f"no servo {name!r} (servos.channels: {', '.join(self.chans)})")
        angle = max(0.0, min(self.range_deg, float(angle)))
        if self.has_hw:
            self._check_clock()
        self.out[name].pulse(self._pulse_us(angle), self.scale)
        print(f"[servos] {name} (GPIO{self.chans[name]['pin']}) -> absolute {angle:.0f} deg "
              f"({self._pulse_us(angle):.0f} us)", flush=True)

    def close(self) -> None:
        print(f"[servos] GPIO{'+'.join(map(str, self.pins))} -> PWM off", flush=True)
        for o in self.out.values():
            o.off()
        self.angle = None


if __name__ == "__main__":
    from . import load_config
    cfg = load_config()
    servos = Servos(cfg["servos"])
    flap_s = float(cfg.get("controller", {}).get("flap_seconds", 0.5))
    args = sys.argv[1:]
    try:
        if args[:1] == ["cal"]:
            if len(args) != 3:
                raise SystemExit("usage: python -m scare.servos cal NAME DEG")
            servos.calibrate(args[1], args[2])
            time.sleep(3)
        elif args[:1] == ["sweep"]:
            servos.down()
            time.sleep(1)
            for _ in range(2):
                servos.sweep()
                time.sleep(0.5)
        elif args[:1] == ["flap"]:
            servos.down()
            time.sleep(1)
            for _ in range(int(args[1]) if args[1:] else 3):
                servos.up()
                time.sleep(flap_s)
                servos.down()
                time.sleep(flap_s)
            time.sleep(0.5)
        else:
            servos.down()
            time.sleep(1)
            servos.up()
            time.sleep(1)
            servos.down()
            time.sleep(1)
    finally:
        servos.close()
