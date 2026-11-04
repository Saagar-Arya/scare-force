"""Sound through the 3.5 mm jack. play(clip) starts a clip in the background and returns at once.

Where the clips come from is sound.source in config.yaml:
    legacy   the original behavior: the .wav files in sound.dir, sorted by name (0_siren.wav is
             clip 0, ...); next_clip() picks one at random, never the same one twice in a row.
    library  the deterrence sound library's generic set (scare/sounds/library.yaml, see
             scare/sound_library.py), in the library's order, which alternates sound types.
             next_clip() starts at a random clip each run (a restart doesn't replay the same
             opening clips), advances one clip per scare and wraps.
Plays straight to ALSA with aplay: it starts faster than going through PipeWire. aplay runs under
`setpriv --pdeathsig TERM`, so the kernel stops it if this process dies for any reason (even
kill -9): a clip never outlives the program. stop() terminates it and waits for it.
result() describes the last clip: id, sound type, aplay's exit code, how long it played, and
whether stop() cut it off (aplay catches SIGTERM and exits 0, so the exit code can't tell).

Self-test: python -m scare.sound   (plays every clip in turn)
"""

import random
import shutil
import subprocess
import threading
import time
from pathlib import Path

SOURCES = ("legacy", "library")


class Sound:
    def __init__(self, cfg: dict, root: Path):
        self.device = cfg["device"]
        self.source = cfg.get("source", "legacy")
        if self.source not in SOURCES:
            raise ValueError(f"sound.source must be {' or '.join(SOURCES)}, got {self.source!r}")
        self.library = None
        if self.source == "library":
            from .sound_library import Library

            self.library = Library.load(root / cfg["library"])
            self.clips = self.library.clips_for()  # the generic set, in play order
            self.types = {c["id"]: c["sound_type"] for c in self.library.clip_records()}
            print(f"[sound] library {cfg['library']}: profile {self.library.profile}, "
                  f"{len(self.clips)} generic clips", flush=True)
        else:
            self.clips = sorted((root / cfg["dir"]).glob("*.wav"))
            if not self.clips:
                raise RuntimeError(f"no .wav files in {root / cfg['dir']}")
            # legacy clips have no recorded type: 0_siren.wav -> siren
            self.types = {c.stem: c.stem.split("_", 1)[-1] for c in self.clips}
        setpriv = shutil.which("setpriv")
        self._wrap = [setpriv, "--pdeathsig", "TERM"] if setpriv else []
        self._next = random.randrange(len(self.clips))  # library: next clip in the rotation
        self._last: int | None = None    # legacy: index of the last clip picked
        self.proc: subprocess.Popen | None = None
        self.current: Path | None = None
        self.started_t: float | None = None   # monotonic: aplay process started
        self._ended_t: float | None = None    # monotonic: aplay exited (set by the reaper thread)
        self._stopped = False                 # stop() terminated the clip before its end
        print(f"[sound] setting {cfg['mixer_card']}/{cfg['mixer_control']} to {cfg['level']}%",
              flush=True)
        subprocess.run(["amixer", "-q", "-M", "-c", cfg["mixer_card"], "sset",
                        cfg["mixer_control"], f"{cfg['level']}%", "unmute"], check=False)

    def next_clip(self) -> Path:
        """The clip the next scare should play."""
        if self.library is None:  # legacy: Demo 1-3's picker, unchanged
            choices = [i for i in range(len(self.clips)) if i != self._last] or [0]
            self._last = random.choice(choices)
            return self.clips[self._last]
        i = self._next
        self._next = (i + 1) % len(self.clips)
        return self.clips[i]

    def play(self, clip: Path | int) -> None:
        """Play a clip (a path, or an index into self.clips), cutting off whatever is playing now."""
        if isinstance(clip, int):
            clip = self.clips[clip % len(self.clips)]
        self.stop()
        print(f"[sound] playing {clip.name} on {self.device}", flush=True)
        self.current = clip
        self._ended_t = None
        self._stopped = False
        self.proc = subprocess.Popen(self._wrap + ["aplay", "-q", "-D", self.device, str(clip)])
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
        """The last clip played: id, type, aplay rc (None while playing), seconds played, stopped."""
        if self.proc is None:
            return {"clip_id": "", "sound_type": "", "rc": None, "seconds": None, "stopped": False,
                    "started_t": None}
        rc = self.proc.poll()
        end = self._ended_t if rc is not None and self._ended_t else time.monotonic()
        return {"clip_id": self.current.stem, "sound_type": self.clip_type(self.current), "rc": rc,
                "seconds": end - self.started_t, "stopped": self._stopped, "started_t": self.started_t}

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
    import time

    from . import ROOT, load_config

    sound = Sound(load_config()["sound"], ROOT)
    for i in range(len(sound.clips)):
        sound.play(i)
        while sound.is_playing():
            time.sleep(0.1)
