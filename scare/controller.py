"""Main loop: camera -> detector -> decide -> light, servos, sound.

A matching detection starts a scare: a random clip (never the same one twice in a row), servos
flapping up/down and the light on. After scare_seconds everything stops, then nothing fires
again until cooldown_seconds have passed. Detection keeps running the whole time.

Run: python -m scare.controller              (debug window; q / Esc / Ctrl+C stops)
     python -m scare.controller --headless   (no window)
"""

import argparse
import random
import time

from . import ROOT, load_config
from .camera import Camera
from .detector import Detector
from .light import Light
from .servos import Servos
from .sound import Sound


def main() -> None:
    ap = argparse.ArgumentParser(description="Scare Force main loop")
    ap.add_argument("--headless", action="store_true", help="no debug window")
    args = ap.parse_args()
    if not args.headless:
        from . import display

    cfg = load_config()
    rules = cfg["controller"]
    d = cfg["detector"]

    camera = Camera(cfg["camera"]["width"], cfg["camera"]["height"])
    detector = Detector(ROOT / d["model"], ROOT / d["labels"], d["num_threads"],
                        d["conf_threshold"], d["iou_threshold"])
    light = Light(cfg["light"]["pin"], cfg["light"]["active_high"])
    sound = Sound(cfg["sound"], ROOT)
    sound.prime()  # switches the shared PWM clock now, so Servos reads its final rate
    servos = Servos(cfg["servos"])

    scare_until = 0.0     # scare is running while now < scare_until
    quiet_until = 0.0     # no new scare before this
    next_flap = 0.0
    servo_up = False
    last_clip = None
    last_frame_t = time.monotonic()
    fps = 0.0
    print(f"[controller] watching for {rules['labels']} (conf >= {rules['min_confidence']})",
          flush=True)

    try:
        servos.down()
        while True:
            frame = camera.read()
            detections = detector.detect(frame)
            hits = [
                det for det in detections
                if det.label in rules["labels"] and det.confidence >= rules["min_confidence"]
            ]
            now = time.monotonic()

            if hits and now >= quiet_until:
                best = hits[0]
                print(f"[controller] SCARE: {best.label} {best.confidence:.2f} at {best.box}",
                      flush=True)
                scare_until = now + rules["scare_seconds"]
                quiet_until = scare_until + rules["cooldown_seconds"]
                next_flap = now
                light.on()
                choices = [i for i in range(len(sound.clips)) if i != last_clip] or [0]
                last_clip = random.choice(choices)
                sound.play(last_clip)

            if scare_until and now < scare_until:
                if now >= next_flap:
                    servo_up = not servo_up
                    servos.up() if servo_up else servos.down()
                    next_flap = now + rules["flap_seconds"]
            elif scare_until:
                print("[controller] scare over", flush=True)
                scare_until = 0.0
                light.off()
                sound.stop()
                servos.ramp(servos.down_angle, rules["return_deg_per_s"])
                servo_up = False

            fps = 0.9 * fps + 0.1 / max(now - last_frame_t, 1e-6)
            last_frame_t = now
            if not args.headless:
                angle = servos.angle
                if scare_until:
                    state = f"SCARING {scare_until - now:.1f}s"
                elif now < quiet_until:
                    state = f"cooldown {quiet_until - now:.1f}s"
                else:
                    state = "watching"
                status = [
                    ("sound", sound.current.name if sound.is_playing() else "off",
                     sound.is_playing()),
                    ("light", "ON" if scare_until else "off", bool(scare_until)),
                    ("servo", "off" if angle is None else f"{angle:.0f} deg", servo_up),
                    ("state", state, bool(scare_until)),
                ]
                line2 = f"{fps:.1f} fps   {len(detections)} objects   triggers: {rules['labels']}"
                if not display.show(frame, detections, hits, status, line2):
                    break
    except KeyboardInterrupt:
        print("\n[controller] stopping", flush=True)
    finally:
        light.off()
        sound.stop()
        servos.ramp(servos.down_angle, rules["return_deg_per_s"])
        time.sleep(0.5)
        servos.close()
        light.close()
        camera.close()
        if not args.headless:
            display.close()


if __name__ == "__main__":
    main()
