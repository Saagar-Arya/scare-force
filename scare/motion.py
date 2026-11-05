"""Motion: the arms' part of a scare. Drop, wave until the scare's end, then reset in the cooldown.

    m = Motion(servos, cfg["motion"])   # puts the arms at rest (T-pose)
    m.start(deadline)                   # deadline: time.monotonic() the scare ends (the sound's end)
    m.finish(grace_s)                   # at the deadline: stop waving, start the reset (background)
    m.close()                           # shutdown: back to rest, PWM off, latch pin released

A scare, as an outside viewer sees it (angles are the servo's; with the horn mounted so the arms
are horizontal at rest_angle 135, drop/down 90 = 45 deg below, up 180 = 45 deg above):
  0          the drop. With a latch (motion.latch.pin set): the servos go limp, the solenoid
             pulls the pin for release_s, gravity drops the arm, and after drop_s the servos
             take it at drop_angle, where it should be by then. Without a latch (pin null, as
             now): the servos drive to drop_angle and drop_s is the time allowed for that.
  drop_s     waving: up_angle, down_angle, up_angle, ... one move per ~flap_seconds. The move
             time is stretched or shrunk slightly so a whole number of moves fills the time
             left, the last one landing at the deadline.
  deadline   the arms stop, wherever the last move put them: light, sound and arms end together.
The reset is not part of the scare: in the cooldown, after holding still for reset_delay_s (so
the scare's end reads as an end), the arms ease back to rest_angle at return_deg_per_s (fast reversals spike the servo current and can brown out
their supply). With a latch and relatch: true, the solenoid holds the pin back during that
return and lets it go at rest so it springs into the notch; then the servos go limp, the latch
holding the arm. Without a latch the servos hold rest_angle. start() waits for a reset still
running, so the next scare always starts from rest.

NOT VALIDATED ON HARDWARE. Servo angles, drop_s and every latch setting are placeholders until
the arms are reconnected and Ella confirms the latch (pin, power-to-release, pulse length, where
the arm stops after the drop). drop_s 0.2 comes from 0.18 s calculated for a free 1 ft, 0.106 kg
arm falling from horizontal to 45 deg below, not from a measurement.
"""

import threading
import time

EPS = 1e-6


def moves(span: float, flap_seconds: float) -> tuple[int, float]:
    """(count, seconds per move): whole moves nearest flap_seconds that exactly fill `span`."""
    if span <= 0:
        return 0, 0.0
    n = max(1, round(span / flap_seconds))
    return n, span / n


class Motion:
    def __init__(self, servos, cfg: dict):
        self.servos = servos
        self.rest_angle, self.drop_angle = float(cfg["rest_angle"]), float(cfg["drop_angle"])
        self.up_angle, self.down_angle = float(cfg["up_angle"]), float(cfg["down_angle"])
        self.drop_s, self.flap_seconds = float(cfg["drop_s"]), float(cfg["flap_seconds"])
        self.return_deg_per_s = float(cfg["return_deg_per_s"])
        self.reset_delay_s = float(cfg["reset_delay_s"])
        if self.flap_seconds <= 0 or self.drop_s < 0 or self.return_deg_per_s <= 0:
            raise ValueError("motion: flap_seconds and return_deg_per_s must be > 0, drop_s >= 0")
        lc = cfg.get("latch") or {}
        self.latch = None
        if lc.get("pin") is not None:
            from gpiozero import DigitalOutputDevice

            self.latch = DigitalOutputDevice(lc["pin"], active_high=lc.get("active_high", True),
                                             initial_value=False)
            self.release_s = float(lc["release_s"])
            self.relatch = bool(lc.get("relatch", True))
        self.drop = "latch" if self.latch else "servo"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reset_thread: threading.Thread | None = None
        self.count = 0                       # moves commanded this scare (the drop not counted)
        self.first_t: float | None = None    # monotonic: the drop began (latch pulled / servo cmd)
        print(f"[motion] drop by {self.drop}; rest {self.rest_angle:g} deg, drop to "
              f"{self.drop_angle:g}, wave {self.down_angle:g}-{self.up_angle:g}", flush=True)
        self._go_to_rest(ramp=False)

    # ---- the scare
    def start(self, deadline: float) -> None:
        self._join_reset()
        self._stop = threading.Event()
        self.count, self.first_t = 0, None
        self._thread = threading.Thread(target=self._run, args=(self._stop, deadline),
                                        name="motion", daemon=True)
        self._thread.start()

    def _run(self, stop: threading.Event, deadline: float) -> None:
        t0 = time.monotonic()
        if self.latch:
            self.servos.close()              # limp, so gravity alone drops the arm
            self.first_t = time.monotonic()
            self.latch.on()
            if stop.wait(min(self.release_s, max(0.0, deadline - time.monotonic()))):
                self.latch.off()
                return
            self.latch.off()
        else:
            self.first_t = time.monotonic()
            self.servos.set(self.drop_angle)
        caught = t0 + min(self.drop_s, max(0.0, deadline - t0))
        if stop.wait(max(0.0, caught - time.monotonic())):
            return
        if self.latch:
            self.servos.set(self.drop_angle)  # take the arm where the drop left it
        n, step = moves(deadline - caught, self.flap_seconds)
        print(f"[motion] {self.drop} drop, then {n} moves of {step:.2f} s to the scare's end",
              flush=True)
        for k in range(n):
            if stop.wait(max(0.0, caught + k * step - time.monotonic())):
                return
            self.servos.set(self.up_angle if k % 2 == 0 else self.down_angle, quiet=True)
            self.count += 1
        stop.wait(max(0.0, deadline - time.monotonic()))

    def finish(self, grace_s: float, hold: bool = True) -> None:
        """At the deadline: let the last move's wait end (up to grace_s), stop, start the reset
        (after reset_delay_s if hold; no hold on shutdown)."""
        if self._thread is not None:
            self._thread.join(timeout=grace_s)
            self._stop.set()
            self._thread.join(timeout=1.0)
            self._thread = None
            if self.latch:
                self.latch.off()
            self._reset_thread = threading.Thread(target=self._go_to_rest,
                                                  args=(True, self.reset_delay_s if hold else 0.0),
                                                  name="motion-reset", daemon=True)
            self._reset_thread.start()

    # ---- the reset, in the cooldown
    def _go_to_rest(self, ramp: bool = True, delay_s: float = 0.0) -> None:
        time.sleep(delay_s)  # hold still first: the scare has visibly ended
        t0 = time.monotonic()
        if self.latch and self.relatch:
            self.latch.on()                  # hold the pin back while the arm comes up
        if ramp:
            self.servos.ramp(self.rest_angle, self.return_deg_per_s)
        else:
            self.servos.set(self.rest_angle)  # startup: from wherever the horn is
        if self.latch:
            time.sleep(0.3)                  # let the arm settle at the latch
            self.latch.off()                 # the pin springs into the notch
            if self.relatch:
                time.sleep(0.2)
                self.servos.close()          # the latch holds the arm; no servo current at rest
        print(f"[motion] at rest ({self.rest_angle:g} deg"
              + (", latched" if self.latch and self.relatch else "")
              + f") in {time.monotonic() - t0:.2f} s", flush=True)

    def _join_reset(self) -> None:
        if self._reset_thread is not None:
            self._reset_thread.join(timeout=10.0)
            self._reset_thread = None

    def close(self) -> None:
        """Stop any scare, return to rest, PWM off, latch pin released."""
        try:
            if self._thread is not None:
                self._stop.set()
                self._thread.join(timeout=1.0)
                self._thread = None
                self._go_to_rest()  # no hold on the way down
            self._join_reset()
        finally:
            try:
                self.servos.close()
            finally:
                if self.latch:
                    self.latch.off()
                    self.latch.close()
