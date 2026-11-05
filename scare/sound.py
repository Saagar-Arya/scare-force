"""Sound through the 3.5 mm jack. play(clip) starts a clip in the background and returns at once.

Clips are the sound library's generic set (sound.library in config.yaml, see
scare/sound_library.py), in the library's order, which alternates sound types. next_clip()
starts at a random clip each run (a restart doesn't replay the same opening clips), advances one
clip per scare and wraps.

Every clip lasts the whole scare: at startup each one is rendered to exactly `seconds` (the
scare length, controller.scare_seconds) by scare/sound_fill.py: repeated if it is shorter,
ended at a pause in its last sound.end_search_s (else faded out over sound.end_fade_s), then
silence. The renders go to RAM (/dev/shm), not the SD card, and are made again every start, so
a change to scare_seconds or a clip needs nothing else. Levels are unchanged.
Plays straight to ALSA with aplay: it starts faster than going through PipeWire. aplay runs under
`setpriv --pdeathsig TERM`, so the kernel stops it if this process dies for any reason (even
kill -9): a clip never outlives the program. stop() terminates it and waits for it.
result() describes the last clip: id, sound type, aplay's exit code, how long it played, and
whether stop() cut it off (aplay catches SIGTERM and exits 0, so the exit code can't tell),
plus the render's audible seconds, how many times the clip started in it, and how it ended.
stopped=1 is normal at a scare's end: the render is exactly the scare long and aplay starts a
few ms after the trigger, so the timer stops it in its final silence.

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


class Sound:
    def __init__(self, cfg: dict, root: Path, seconds: float):
        """seconds: the scare length; every clip is rendered to exactly this long."""
        self.device = cfg["device"]
        library = Library.load(root / cfg["library"])
        self.clips = library.clips_for()  # the generic set, in play order (the source WAVs)
        self.types = {c["id"]: c["sound_type"] for c in library.clip_records()}
        print(f"[sound] library {cfg['library']}: profile {library.profile}, "
              f"{len(self.clips)} generic clips", flush=True)
        self.seconds = float(seconds)
        self.renders = self._render(library.profile, float(cfg["end_search_s"]),
                                    float(cfg["end_fade_s"]))
        setpriv = shutil.which("setpriv")
        self._wrap = [setpriv, "--pdeathsig", "TERM"] if setpriv else []
        self._next = random.randrange(len(self.clips))  # index of the next clip in the rotation
        self.proc: subprocess.Popen | None = None
        self.current: Path | None = None
        self.started_t: float | None = None   # monotonic: aplay process started
        self._ended_t: float | None = None    # monotonic: aplay exited (set by the reaper thread)
        self._stopped = False                 # stop() terminated the clip before its end
        print(f"[sound] setting {cfg['mixer_card']}/{cfg['mixer_control']} to {cfg['level']}%",
              flush=True)
        subprocess.run(["amixer", "-q", "-M", "-c", cfg["mixer_card"], "sset",
                        cfg["mixer_control"], f"{cfg['level']}%", "unmute"], check=False)

    def _render(self, profile: str, search_s: float, fade_s: float) -> dict:
        """Render every clip to the scare length. {clip stem: (path, audible_s, plays, ending)}"""
        if not 0 <= search_s < self.seconds or fade_s <= 0:
            raise ValueError(f"sound.end_search_s must be in [0, {self.seconds:g}) and "
                             f"sound.end_fade_s > 0 (got {search_s:g}, {fade_s:g})")
        out_dir = _render_dir(profile, self.seconds)
        t0 = time.monotonic()
        renders, endings = {}, {"break": 0, "fade": 0}
        for clip in self.clips:
            samples, rate = sound_fill.read_wav(clip)
            r = sound_fill.render(samples, rate, self.seconds, search_s, fade_s)
            path = out_dir / clip.name
            sound_fill.write_wav(path, r.samples, rate)
            renders[clip.stem] = (path, r.audible_s, r.plays, r.ending)
            endings[r.ending] += 1
        audible = [v[1] for v in renders.values()]
        print(f"[sound] rendered {len(renders)} clips to {self.seconds:g} s in {out_dir} "
              f"({(time.monotonic() - t0) * 1000:.0f} ms): sound ends {min(audible):.2f}-"
              f"{max(audible):.2f} s, {endings['break']} at a pause, {endings['fade']} faded",
              flush=True)
        return renders

    def next_clip(self) -> Path:
        """The clip the next scare should play."""
        i = self._next
        self._next = (i + 1) % len(self.clips)
        return self.clips[i]

    def play(self, clip: Path | int) -> None:
        """Play a clip (a path, or an index into self.clips), cutting off whatever is playing now."""
        if isinstance(clip, int):
            clip = self.clips[clip % len(self.clips)]
        self.stop()
        path, audible, plays, ending = self.renders[clip.stem]
        print(f"[sound] playing {clip.name} on {self.device} ({self.seconds:g} s: sound "
              f"{audible:.2f} s, {plays} play(s), ends {ending})", flush=True)
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
        """Play 0.1 s of silence. The first sound after boot switches the Pi 4's PWM clock (shared
        with the servos), so do this before creating Servos and they time their pulses right."""
        print(f"[sound] priming {self.device} with 0.1 s of silence", flush=True)
        subprocess.run(["aplay", "-q", "-D", self.device, "-f", "S16_LE", "-r", "44100", "-c", "2",
                        "-s", "4410", "/dev/zero"], check=False)

    def is_playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

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
    sound = Sound(cfg["sound"], ROOT, cfg["controller"]["scare_seconds"])
    for i in range(len(sound.clips)):
        sound.play(i)
        while sound.is_playing():
            time.sleep(0.1)
