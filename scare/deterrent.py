"""One scare's light and sound, started together and ended together; its log line and CSV row.
scare.controller (detection) and scare.deter_test (manual trigger) both go through this. The arms
are not in here yet: scare.controller drives the servos itself for the scare's length (stage 2
moves them in here, timing only).

    d = Deterrent(light, sound, log)           # any may be None (no light/sound/CSV)
    length = d.start(trigger_t, "detection", 5.0, label="raccoon", confidence=0.91, camera=0)
    ...                                        # at the scare's end a timer ends all three
    d.end("timeout")                           # logged (outputs already stopped by the timer)
    d.close()                                  # ends a running scare as "shutdown", releases pins

How long a scare lasts: the sound decides. start() takes the next clip, whose render ends at a
pause near `seconds` (controller.scare_seconds +/- end_window_s, see scare/sound_fill.py), and
sets the scare's end, `deadline`, to that length after the start. The light plans whole flashes
whose last goes off at the deadline, and aplay plays the render, which ends there too. Without
sound the scare is exactly `seconds`. start() returns the length; the controller flaps the arms
for that long. At the deadline a timer stops the light and lets aplay play out (it finishes by
itself; stopped only if it is still going SOUND_GRACE_S later).

end() prints one line per scare:
    [scare] end reason=timeout source=detection label=raccoon conf=0.91 camera=0 scare=4.82s
            light=strobe flashes=10 relay_on=+2ms light_s=4.82s
            clip=dog_bark_amada44_03 type=dog_bark rc=0 played=4.84s plays=1
            ending=break stopped=0 aplay_start=+9ms
(on one line). Shutdown: handle_signals() turns SIGTERM and SIGHUP into a Shutdown exception
(SIGINT is already KeyboardInterrupt), so the caller's finally runs and calls close();
ignore_signals() keeps a second signal from interrupting that cleanup.
"""

import signal
import threading
import time
from datetime import datetime

END_REASONS = ("timeout", "shutdown")
SOUND_GRACE_S = 0.3   # aplay starts a few ms late, so it may end a little after the deadline
SYNC_GRACE_S = 0.05   # the light's last off is due at the deadline


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
        self._stop_lock = threading.Lock()  # the timer and end() may stop the outputs at once
        self._off_wall: float | None = None  # wall time this scare's outputs stopped (CSV end)

    def start(self, trigger_t: float, source: str, seconds: float, *, label: str = "",
              confidence=None, camera="", detection_id=None) -> float:
        """Start a scare. trigger_t: time.monotonic() when it was decided (latencies are from
        it). seconds: the target length (the sound picks the exact one). Returns the length; at
        start + length a timer ends the light and sound. end() must still be called."""
        if self._scare is not None:
            raise RuntimeError("a scare is already running")
        clip = self.sound.next_clip() if self.sound else None
        length = self.sound.length(clip) if clip is not None else float(seconds)
        t0 = time.monotonic()
        deadline = t0 + length
        self._scare = {"start": time.time(), "trigger_t": trigger_t, "trigger_source": source,
                       "label": label, "confidence": confidence, "camera": camera,
                       "length": length, "detection_id": detection_id}
        self._off_wall = None
        if self.light:
            self.light.start(deadline)
        if self.sound:
            self.sound.play(clip)
        self._timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._stop_outputs)
        self._timer.daemon = True
        self._timer.start()
        return length

    def _stop_outputs(self) -> None:
        """Light, then sound. Called by the timer, end(), close()."""
        with self._stop_lock:
            try:
                if self.light:
                    self.light.finish(SYNC_GRACE_S)
            finally:
                if self._off_wall is None:
                    self._off_wall = time.time()
                if self.sound:
                    self.sound.finish(SOUND_GRACE_S)

    def end(self, reason: str) -> None:
        """Stop the scare if the timer hasn't, print its line, write its CSV row."""
        if reason not in END_REASONS:
            raise ValueError(f"end reason must be one of {END_REASONS}, got {reason!r}")
        s = self._scare
        if s is None:
            return
        self._scare = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if reason == "shutdown" and self.sound:
            self.sound.stop()  # don't let a clip play out on the way down
        self._stop_outputs()  # nothing left to do if the timer already ran
        snd = self.sound.result() if self.sound else {}
        lt = self.light
        t0 = s["trigger_t"]
        light_s = (lt.last_off_t - lt.first_on_t
                   if lt and lt.first_on_t is not None and lt.last_off_t is not None else None)
        row = {
            "start": _iso(s["start"]), "end": _iso(self._off_wall or time.time()),
            "end_reason": reason,
            "trigger_source": s["trigger_source"], "detection_id": s["detection_id"],
            "label": s["label"],
            "confidence": None if s["confidence"] is None else f"{s['confidence']:.3f}",
            "camera": s["camera"], "scare_s": f"{s['length']:.2f}",
            "light_pattern": lt.pattern if lt else "off",
            "flashes": lt.flashes if lt else None,
            "light_s": None if light_s is None else f"{light_s:.2f}",
            "clip_id": snd.get("clip_id"), "sound_type": snd.get("sound_type"),
            "aplay_rc": snd.get("rc"),
            "sound_s": None if snd.get("seconds") is None else f"{snd['seconds']:.2f}",
            "sound_stopped": int(snd["stopped"]) if snd else None,
            "sound_plays": snd.get("plays"), "sound_ending": snd.get("ending"),
            "trigger_to_relay_on_ms": _ms(lt.first_on_t, t0) if lt else None,
            "trigger_to_aplay_start_ms": _ms(snd.get("started_t"), t0) if snd else None,
        }
        def f(key, unit=""):
            return "" if row[key] is None or row[key] == "" else f"{row[key]}{unit}"

        def plus(key):
            return "" if row[key] is None else f"+{row[key]}ms"

        conf = "" if s["confidence"] is None else f"{s['confidence']:.2f}"
        print(f"[scare] end reason={reason} source={row['trigger_source']} label={row['label']} "
              f"conf={conf} camera={row['camera']} scare={row['scare_s']}s "
              f"light={row['light_pattern']} flashes={f('flashes')} "
              f"relay_on={plus('trigger_to_relay_on_ms')} light_s={f('light_s', 's')} "
              f"clip={f('clip_id')} "
              f"type={f('sound_type')} rc={f('aplay_rc')} played={f('sound_s', 's')} "
              f"plays={f('sound_plays')} ending={f('sound_ending')} "
              f"stopped={f('sound_stopped')} aplay_start={plus('trigger_to_aplay_start_ms')}",
              flush=True)
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
