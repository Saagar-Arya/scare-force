"""Sound through the 3.5 mm jack. play(clip) starts a clip in the background and returns at once.

Clips are the sound library's generic set (sound.library in config.yaml, see
scare/sound_library.py). next_clip() picks one at random for each scare, never the clip the
previous scare played: with 16 clips, each pick is uniform over the other 15. (Only the last
clip is left out, not a shuffled cycle: a clip can come back two scares later.)

Every clip is rendered at startup (scare/sound_fill.py) to end at a pause near the scare length
(controller.scare_seconds, +/- controller.end_window_s), repeating it if it is shorter, or to
fade out where there is no pause (sound.end_fade_s). length(clip) is that render's length: the
scare's length when that clip plays, which scare/deterrent.py gives the light and the arms too.
Renders go to RAM (/dev/shm), not the SD card, and are made again every start. Levels unchanged.
Plays straight to ALSA with aplay: it starts faster than going through PipeWire. aplay runs under
`setpriv --pdeathsig TERM`, so the kernel stops it if this process dies for any reason (even
kill -9): a clip never outlives the program. stop() terminates it and waits for it.
result() describes the last clip: id, sound type, aplay's exit code, how long it played, and
whether stop() cut it off (aplay catches SIGTERM and exits 0, so the exit code can't tell),
plus the render's length, how many times the clip started in it, and how it ended. aplay
normally finishes by itself (the render is exactly the scare long), so stopped=0; stopped=1
means it was cut short (shutdown, or still playing well after the scare's end).

Self-test: python -m scare.sound   (plays every clip in turn)
"""

import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import sound_fill
from .sound_library import Library


def _render_dir(profile: str, seconds: float) -> Path:
    base = Path("/dev/shm") if os.access("/dev/shm", os.W_OK) else Path(tempfile.gettempdir())
    d = base / "scare-force-sounds" / profile / f"{seconds:g}s"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prime(device: str) -> None:
    """Play 0.1 s of silence. The first sound after boot switches the Pi 4's PWM clock (shared
    with the servos), so do this before creating Servos and they time their pulses right."""
    print(f"[sound] priming {device} with 0.1 s of silence", flush=True)
    subprocess.run(["aplay", "-q", "-D", device, "-f", "S16_LE", "-r", "44100", "-c", "2",
                    "-s", "4410", "/dev/zero"], check=False)


class Sound:
    def __init__(self, cfg: dict, root: Path, seconds: float, window_s: float):
        """seconds, window_s: controller.scare_seconds and controller.end_window_s; each clip
        is rendered to end at a pause within seconds +/- window_s."""
        self.device = cfg["device"]
        library = Library.load(root / cfg["library"])
        self.clips = library.clips_for()  # the generic set, in play order (the source WAVs)
        self.types = {c["id"]: c["sound_type"] for c in library.clip_records()}
        print(f"[sound] library {cfg['library']}: profile {library.profile}, "
              f"{len(self.clips)} generic clips", flush=True)
        self.seconds = float(seconds)
        self.renders = self._render(library.profile, float(window_s), float(cfg["end_fade_s"]))
        setpriv = shutil.which("setpriv")
        self._wrap = [setpriv, "--pdeathsig", "TERM"] if setpriv else []
        self._last: Path | None = None  # the clip the previous scare played
        self.proc: subprocess.Popen | None = None
        self.current: Path | None = None
        self.started_t: float | None = None   # monotonic: aplay process started
        self._ended_t: float | None = None    # monotonic: aplay exited (set by the reaper thread)
        self._stopped = False                 # stop() terminated the clip before its end
        print(f"[sound] setting {cfg['mixer_card']}/{cfg['mixer_control']} to {cfg['level']}%",
              flush=True)
        subprocess.run(["amixer", "-q", "-M", "-c", cfg["mixer_card"], "sset",
                        cfg["mixer_control"], f"{cfg['level']}%", "unmute"], check=False)

    def _render(self, profile: str, window_s: float, fade_s: float) -> dict:
        """Render every clip to the scare length. {clip stem: (path, length_s, plays, ending)}"""
        if not 0 <= window_s < self.seconds or fade_s <= 0:
            raise ValueError(f"controller.end_window_s must be in [0, {self.seconds:g}) and "
                             f"sound.end_fade_s > 0 (got {window_s:g}, {fade_s:g})")
        out_dir = _render_dir(profile, self.seconds)
        t0 = time.monotonic()
        renders, endings = {}, {"break": 0, "fade": 0}
        for clip in self.clips:
            samples, rate = sound_fill.read_wav(clip)
            r = sound_fill.render(samples, rate, self.seconds, window_s, fade_s)
            path = out_dir / clip.name
            sound_fill.write_wav(path, r.samples, rate)
            renders[clip.stem] = (path, r.audible_s, r.plays, r.ending)
            endings[r.ending] += 1
        audible = [v[1] for v in renders.values()]
        print(f"[sound] rendered {len(renders)} clips for {self.seconds:g} s +/- {window_s:g} s "
              f"in {out_dir} ({(time.monotonic() - t0) * 1000:.0f} ms): scares last "
              f"{min(audible):.2f}-{max(audible):.2f} s, {endings['break']} end at a pause, "
              f"{endings['fade']} fade out", flush=True)
        return renders

    def length(self, clip: Path) -> float:
        """Seconds the clip's render lasts: the scare's length when it plays."""
        return self.renders[clip.stem][1]

    def next_clip(self) -> Path:
        """The clip the next scare should play."""
        choices = [c for c in self.clips if c != self._last] or self.clips
        self._last = random.choice(choices)
        return self._last

    def play(self, clip: Path | int) -> None:
        """Play a clip (a path, or an index into self.clips), cutting off whatever is playing now."""
        if isinstance(clip, int):
            clip = self.clips[clip % len(self.clips)]
        self.stop()
        path, audible, plays, ending = self.renders[clip.stem]
        print(f"[sound] playing {clip.name} on {self.device} ({audible:.2f} s, {plays} play(s), "
              f"ends {ending})", flush=True)
        self.current = clip
        self._ended_t = None
        self._stopped = False
        self.proc = subprocess.Popen(self._wrap + ["aplay", "-q", "-D", self.device, str(path)])
        self.started_t = time.monotonic()
        threading.Thread(target=self._reap, args=(self.proc,), name="aplay-reaper",
                         daemon=True).start()

    def _reap(self, proc: subprocess.Popen) -> None:
        proc.wait()
        if proc is self.proc and self._ended_t is None:
            self._ended_t = time.monotonic()

    def clip_type(self, clip: Path) -> str:
        return self.types.get(clip.stem, "unknown")

    def result(self) -> dict:
        """The last clip played: id, type, aplay rc (None while playing), seconds played, stopped,
        and its render's audible seconds, plays and ending."""
        if self.proc is None:
            return {"clip_id": "", "sound_type": "", "rc": None, "seconds": None, "stopped": False,
                    "started_t": None, "audible_s": None, "plays": None, "ending": ""}
        rc = self.proc.poll()
        end = self._ended_t if rc is not None and self._ended_t else time.monotonic()
        _, audible, plays, ending = self.renders[self.current.stem]
        return {"clip_id": self.current.stem, "sound_type": self.clip_type(self.current), "rc": rc,
                "seconds": end - self.started_t, "stopped": self._stopped, "started_t": self.started_t,
                "audible_s": audible, "plays": plays, "ending": ending}

    def prime(self) -> None:
        """See prime() below."""
        prime(self.device)

    def is_playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def finish(self, grace_s: float) -> None:
        """At a scare's end: let aplay play out (its render ends with the scare), giving it up to
        grace_s for its start-up delay, then stop it if it is still going."""
        if self.is_playing():
            try:
                self.proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                pass
        self.stop()

    def stop(self) -> None:
        """Terminate aplay if it's still playing and wait for it to exit."""
        if self.is_playing():
            print("[sound] stop", flush=True)
            self._stopped = True
            # the clip ends here: the PCM stops within ~30 ms of SIGTERM; aplay itself takes
            # ~100 ms more to exit, which isn't sound
            self._ended_t = time.monotonic()
            self.proc.terminate()
        if self.proc is not None:
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                print("[sound] aplay ignored SIGTERM: killing it", flush=True)
                self.proc.kill()
                self.proc.wait()
            if self._ended_t is None:
                self._ended_t = time.monotonic()


if __name__ == "__main__":
    from . import ROOT, load_config

    cfg = load_config()
    rules = cfg["controller"]
    sound = Sound(cfg["sound"], ROOT, rules["scare_seconds"], rules["end_window_s"])
    for i in range(len(sound.clips)):
        sound.play(i)
        while sound.is_playing():
            time.sleep(0.1)
