"""Direct trigger: run scares with no camera or detector, through the same Light, Sound and
Deterrent code scare.controller uses (scare/deterrent.py), logged to the same CSV
(log.activations) with trigger_source=manual. No servos.

    python -m scare.deter_test                       # one scare, config.yaml's pattern and clip
    python -m scare.deter_test --pattern steady      # steady | strobe | random (overrides config)
    python -m scare.deter_test --no-light            # sound only (or --no-sound: light only)
    python -m scare.deter_test --scares 20 --gap 1   # 20 scares, 1 s apart
    python -m scare.deter_test --seconds 10          # each scare 10 s (default scare_seconds)

Each scare lasts controller.scare_seconds and ends as timeout. Ctrl+C, SIGTERM or SIGHUP ends a
running scare as shutdown and leaves the lamp off, aplay stopped and the pin released.
Light settings are checked first, as in the controller. Refuses to run while scare.controller
is running (both would drive GPIO26 and the jack). The relay board is powered through the
deterrent off switch: with that switch OFF the lamp stays dark (the CSV still logs the scare).
"""

import argparse
import subprocess
import time

from . import ROOT, load_config
from .activation_log import ActivationLog
from .deterrent import Deterrent, Shutdown, handle_signals, ignore_signals
from .light import PATTERNS, Light
from .sound import Sound


def main() -> None:
    handle_signals()
    ap = argparse.ArgumentParser(description="Run scares by direct trigger (no camera)")
    ap.add_argument("--pattern", choices=PATTERNS, help="light pattern (default: config's)")
    ap.add_argument("--no-light", action="store_true", help="leave the light out")
    ap.add_argument("--no-sound", action="store_true", help="leave the sound out")
    ap.add_argument("--scares", type=int, default=1, metavar="N", help="how many (default 1)")
    ap.add_argument("--gap", type=float, default=2.0, metavar="S",
                    help="seconds between one scare's end and the next one's start (default 2)")
    ap.add_argument("--seconds", type=float, metavar="S",
                    help="length of each scare (default controller.scare_seconds)")
    args = ap.parse_args()
    if args.scares < 1 or args.gap < 0 or (args.seconds is not None and args.seconds <= 0):
        ap.error("--scares must be >= 1, --gap >= 0 and --seconds > 0")

    cfg = load_config()
    light_cfg = dict(cfg["light"], **({"pattern": args.pattern} if args.pattern else {}))
    Light.check(light_cfg)
    seconds = args.seconds or cfg["controller"]["scare_seconds"]
    running = subprocess.run(["pgrep", "-f", r"python.* -m scare\.controller"],
                             capture_output=True, text=True).stdout.split()
    if running:
        raise SystemExit(f"scare.controller is running (pid {', '.join(running)}): stop it first")

    log_path = (cfg.get("log") or {}).get("activations")
    deterrent = Deterrent()
    try:
        if log_path:
            deterrent.log = ActivationLog(ROOT / log_path)
        if not args.no_light:
            deterrent.light = Light.from_config(light_cfg)
        if not args.no_sound:
            deterrent.sound = Sound(cfg["sound"], ROOT)
        what = " + ".join(x for x, on in (
            (f"light {light_cfg['pattern']}", deterrent.light), ("sound", deterrent.sound)) if on)
        print(f"[deter_test] {args.scares} scare(s) of {seconds:g} s, {args.gap:g} s apart: "
              f"{what or 'nothing (--no-light --no-sound): log only'}", flush=True)
        for i in range(args.scares):
            if i:
                time.sleep(args.gap)
            print(f"[deter_test] scare {i + 1}/{args.scares}", flush=True)
            trigger_t = time.monotonic()
            deterrent.start(trigger_t, "manual")
            time.sleep(max(0.0, trigger_t + seconds - time.monotonic()))
            deterrent.end("timeout")
    except (KeyboardInterrupt, Shutdown) as e:
        print(f"\n[deter_test] stopping ({e or 'Ctrl+C'})", flush=True)
    finally:
        ignore_signals()
        deterrent.close()
        print("[deter_test] stopped: lamp off, aplay stopped, GPIO released", flush=True)


if __name__ == "__main__":
    main()
