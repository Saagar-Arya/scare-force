"""Sound through the 3.5 mm jack. play(n) starts clip n in the background and returns at once.

Clips are the .wav files in the sounds folder, sorted by name (0_siren.wav is clip 0, ...).
Plays straight to ALSA with aplay: it starts faster than going through PipeWire.

Self-test: python -m scare.sound   (plays every clip in turn)
"""

import subprocess
from pathlib import Path


class Sound:
    def __init__(self, cfg: dict, root: Path):
        self.device = cfg["device"]
        self.clips = sorted((root / cfg["dir"]).glob("*.wav"))
        if not self.clips:
            raise RuntimeError(f"no .wav files in {root / cfg['dir']}")
        self.proc: subprocess.Popen | None = None
        self.current: Path | None = None
        print(f"[sound] setting {cfg['mixer_card']}/{cfg['mixer_control']} to {cfg['level']}%",
              flush=True)
        subprocess.run(["amixer", "-q", "-M", "-c", cfg["mixer_card"], "sset",
                        cfg["mixer_control"], f"{cfg['level']}%", "unmute"], check=False)

    def play(self, n: int) -> None:
        """Play clip n, cutting off whatever is playing now."""
        clip = self.clips[n % len(self.clips)]
        self.stop()
        print(f"[sound] playing {n}: {clip.name} on {self.device}", flush=True)
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
