"""One scare's light and sound, and its log line and CSV row. scare.controller (detection) and
scare.deter_test (manual trigger) both start and end scares through this, so they run the same code.

    d = Deterrent(light, sound, log)       # any of them may be None (no light / no sound / no CSV)
    d.start(trigger_t, "detection", 5.0, label="raccoon", confidence=0.91, camera=0)
    ...                                    # at trigger_t + 5.0 a timer turns the light off and
                                           # stops aplay, however busy the caller's loop is
    d.end("timeout")                       # light off, aplay stopped, logged
    d.close()                              # ends a running scare as "shutdown", releases the pin

end() prints one line per scare:
    [scare] end reason=timeout source=detection label=raccoon conf=0.91 camera=0 light=strobe
            flashes=10 relay_on=+2ms clip=dog_bark_nono_04 type=dog_bark rc=0 played=4.99s
            audible=4.24s plays=1 ending=break stopped=1 aplay_start=+9ms
(on one line). Shutdown: handle_signals() turns SIGTERM and SIGHUP into a Shutdown exception
(SIGINT is already KeyboardInterrupt), so the caller's finally runs and calls close();
ignore_signals() keeps a second signal from interrupting that cleanup.
"""

import signal
import threading
import time
from datetime import datetime

END_REASONS = ("timeout", "shutdown")


class Shutdown(BaseException):
    """SIGTERM or SIGHUP arrived. BaseException, so library `except Exception` blocks don't eat it."""


def _raise(signum, frame):
    raise Shutdown(signal.Signals(signum).name)


def handle_signals() -> None:
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _raise)
    signal.signal(signal.SIGINT, signal.default_int_handler)


def ignore_signals() -> None:
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def _ms(t, trigger_t):
    return None if t is None else round((t - trigger_t) * 1000)


class Deterrent:
    def __init__(self, light=None, sound=None, log=None):
        self.light, self.sound, self.log = light, sound, log
        self._scare: dict | None = None
        self._timer: threading.Timer | None = None
        self._stop_lock = threading.Lock()  # the timer and end() may stop light and sound at once
        self._off_wall: float | None = None  # wall time this scare's outputs stopped (CSV end)

    def start(self, trigger_t: float, source: str, seconds: float, *, label: str = "",
              confidence=None, camera="") -> None:
        """Start a scare: the light pattern, then the next clip. trigger_t: time.monotonic() at
        the moment the scare was decided (latencies are measured from it). At trigger_t + seconds
        a timer stops the light and sound; end() must still be called to log the scare."""
        if self._scare is not None:
            raise RuntimeError("a scare is already running")
        self._scare = {"start": time.time(), "trigger_t": trigger_t, "trigger_source": source,
                       "label": label, "confidence": confidence, "camera": camera}
        self._off_wall = None
        if self.light:
            self.light.start()
        if self.sound:
            self.sound.play(self.sound.next_clip())
        self._timer = threading.Timer(max(0.0, trigger_t + seconds - time.monotonic()),
                                      self._stop_outputs)
        self._timer.daemon = True
        self._timer.start()

    def _stop_outputs(self) -> None:
        """Light off first, then aplay. Called by the timer, end() and close()."""
        with self._stop_lock:
            try:
                if self.light:
                    self.light.off()
            finally:
                if self._off_wall is None:
                    self._off_wall = time.time()
                if self.sound:
                    self.sound.stop()

    def end(self, reason: str) -> None:
        """Stop the scare (light off first, then aplay), print its line, write its CSV row."""
        if reason not in END_REASONS:
            raise ValueError(f"end reason must be one of {END_REASONS}, got {reason!r}")
        s = self._scare
        if s is None:
            return
        self._scare = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._stop_outputs()  # nothing left to do if the timer already ran
        snd = self.sound.result() if self.sound else {}
        t0 = s["trigger_t"]
        row = {
            "start": _iso(s["start"]), "end": _iso(self._off_wall or time.time()),
            "end_reason": reason,
            "trigger_source": s["trigger_source"], "label": s["label"],
            "confidence": None if s["confidence"] is None else f"{s['confidence']:.3f}",
            "camera": s["camera"],
            "light_pattern": self.light.pattern if self.light else "off",
            "flashes": self.light.flashes if self.light else None,
            "clip_id": snd.get("clip_id"), "sound_type": snd.get("sound_type"),
            "aplay_rc": snd.get("rc"),
            "sound_s": None if snd.get("seconds") is None else f"{snd['seconds']:.2f}",
            "sound_stopped": int(snd["stopped"]) if snd else None,
            "sound_audible_s": None if snd.get("audible_s") is None else f"{snd['audible_s']:.2f}",
            "sound_plays": snd.get("plays"), "sound_ending": snd.get("ending"),
            "trigger_to_relay_on_ms": _ms(self.light.first_on_t, t0) if self.light else None,
            "trigger_to_aplay_start_ms": _ms(snd.get("started_t"), t0) if snd else None,
        }
        conf = "" if row["confidence"] is None else f"{s['confidence']:.2f}"
        relay = row["trigger_to_relay_on_ms"]
        aplay = row["trigger_to_aplay_start_ms"]
        print(f"[scare] end reason={reason} source={row['trigger_source']} label={row['label']} "
              f"conf={conf} camera={row['camera']} light={row['light_pattern']} "
              f"flashes={row['flashes'] if self.light else ''} "
              f"relay_on={'' if relay is None else f'+{relay}ms'} "
              f"clip={row['clip_id'] or ''} type={row['sound_type'] or ''} "
              f"rc={'' if row['aplay_rc'] is None else row['aplay_rc']} "
              f"played={row['sound_s'] + 's' if row['sound_s'] else ''} "
              f"audible={row['sound_audible_s'] + 's' if row['sound_audible_s'] else ''} "
              f"plays={row['sound_plays'] or ''} ending={row['sound_ending'] or ''} "
              f"stopped={'' if row['sound_stopped'] is None else row['sound_stopped']} "
              f"aplay_start={'' if aplay is None else f'+{aplay}ms'}", flush=True)
        if self.log:
            self.log.write(row)

    def close(self) -> None:
        """End a running scare as shutdown, then leave the lamp off, aplay stopped and the pin
        released. Each step runs even if an earlier one fails."""
        try:
            self.end("shutdown")
        finally:
            try:
                self._stop_outputs()  # also a scare whose start() was interrupted
            finally:
                try:
                    if self.light:
                        self.light.close()  # stops the flash thread, off, release, pull-down
                finally:
                    if self.log:
                        self.log.close()
