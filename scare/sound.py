"""Sound through the 3.5 mm jack. play(clip) starts a clip in the background and returns at once.

Where the clips come from is sound.source in config.yaml:
    legacy   the original behavior: the .wav files in sound.dir, sorted by name (0_siren.wav is
             clip 0, ...); next_clip() picks one at random, never the same one twice in a row.
    library  the deterrence sound library's generic set (scare/sounds/library.yaml, see
             scare/sound_library.py), in the library's order, which alternates sound types.
             next_clip() starts at clip 0, advances one clip per scare and wraps.
Plays straight to ALSA with aplay: it starts faster than going through PipeWire.

Self-test: python -m scare.sound   (plays every clip in turn)
"""

import random
import subprocess
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

            self.library = Library.load(root / cfg["library"], mode="generic")
            self.clips = self.library.clips_for(None)  # the generic set, in play order
            print(f"[sound] library {cfg['library']}: profile {self.library.profile}, "
                  f"{len(self.clips)} generic clips", flush=True)
        else:
            self.clips = sorted((root / cfg["dir"]).glob("*.wav"))
            if not self.clips:
                raise RuntimeError(f"no .wav files in {root / cfg['dir']}")
        self._next = 0                   # library: index of the next clip in the rotation
        self._last: int | None = None    # legacy: index of the last clip picked
        self.proc: subprocess.Popen | None = None
        self.current: Path | None = None
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
        self.proc = subprocess.Popen(["aplay", "-q", "-D", self.device, str(clip)])

    def prime(self) -> None:
        """Play 0.1 s of silence. The first sound after boot switches the Pi 4's PWM clock (shared
        with the servos), so do this before creating Servos and they time their pulses right."""
        print(f"[sound] priming {self.device} with 0.1 s of silence", flush=True)
        subprocess.run(["aplay", "-q", "-D", self.device, "-f", "S16_LE", "-r", "44100", "-c", "2",
                        "-s", "4410", "/dev/zero"], check=False)

    def is_playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.is_playing():
            print("[sound] stop", flush=True)
            self.proc.terminate()
            self.proc.wait()


if __name__ == "__main__":
    import time

    from . import ROOT, load_config

    sound = Sound(load_config()["sound"], ROOT)
    for i in range(len(sound.clips)):
        sound.play(i)
        while sound.is_playing():
            time.sleep(0.1)
