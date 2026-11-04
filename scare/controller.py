"""Main loop: camera -> detector -> decide -> light, servos, sound.

With several cameras (camera.ids) only one streams at a time, so the loop watches each for
camera.dwell_seconds, then switches to the next. A matching detection on any of them starts a scare:
a clip (sound.source: library rotation, or legacy: random from a folder), servos flapping up/down and the
light on (light.pattern: steady, strobe or random). After scare_seconds everything stops, then
nothing fires again until cooldown_seconds have passed.
Detection and switching keep running the whole time. Both models see the same frame.
Animal boxes matching person boxes are rejected; smaller held animals remain eligible.
Person regions and active trigger positions are scoped to their source camera.
Light and sound start and stop through scare/deterrent.py (shared with scare.deter_test), which
logs one line and one CSV row per scare. Light settings are checked before the cameras open.
Ctrl+C, SIGTERM, SIGHUP, q / Esc or an error: the lamp is left off, aplay stopped, GPIO released.

Run: python -m scare.controller              (debug window; q / Esc / Ctrl+C stops)
     python -m scare.controller --headless   (no window)
"""

import argparse
import time

from . import ROOT, load_config
from .activation_log import ActivationLog
from .camera import Camera
from .detector import Detector
from .deterrent import Deterrent, Shutdown, handle_signals, ignore_signals
from .person_guard import PersonGuard
from .light import Light
from .servos import Servos
from .sound import Sound


def main() -> None:
    handle_signals()  # SIGTERM / SIGHUP -> Shutdown, so the finally below always runs
    ap = argparse.ArgumentParser(description="Scare Force main loop")
    ap.add_argument("--headless", action="store_true", help="no debug window")
    args = ap.parse_args()
    if not args.headless:
        from . import display

    cfg = load_config()
    Light.check(cfg["light"])  # refuse bad light settings (e.g. relay toggling < 0.15 s) first
    rules = cfg["controller"]
    d = cfg["detector"]
    c = cfg["camera"]

    detector = Detector(ROOT / d["model"], ROOT / d["labels"], d["num_threads"],
                        d["conf_threshold"], d["iou_threshold"])
    # Require both models before opening hardware; never silently skip person checks.
    pd = cfg["person_detector"]
    person_detector = Detector(ROOT / pd["model"], ROOT / pd["labels"], pd["num_threads"],
                               pd["conf_threshold"], pd["iou_threshold"])
    if "person" not in person_detector.labels:
        raise ValueError("Person detector labels must include 'person'.")
    # Validate policy settings before hardware initialization.
    PersonGuard(pd["conf_threshold"], pd["hold_seconds"], pd.get("same_object_iou", 0.5))
    log_path = (cfg.get("log") or {}).get("activations")

    camera = servos = None
    deterrent = Deterrent()
    try:
        activations = ActivationLog(ROOT / log_path) if log_path else None  # null: terminal only
        deterrent.log = activations
        camera = Camera(c["width"], c["height"], c["ids"])
        guards = [PersonGuard(pd["conf_threshold"], pd["hold_seconds"],
                              pd.get("same_object_iou", 0.5)) for _ in camera.ids]
        deterrent.light = Light.from_config(cfg["light"])
        sound = deterrent.sound = Sound(cfg["sound"], ROOT)
        sound.prime()  # switches the shared PWM clock now, so Servos reads its final rate
        servos = Servos(cfg["servos"])
        run(args, cfg, camera, guards, detector, person_detector, deterrent, sound, servos)
    except (KeyboardInterrupt, Shutdown) as e:
        print(f"\n[controller] stopping ({e or 'Ctrl+C'})", flush=True)
    finally:
        ignore_signals()  # a second Ctrl+C / SIGTERM mustn't cut the cleanup short
        try:
            deterrent.close()  # ends a running scare as shutdown: lamp off, aplay stopped, pin freed
        finally:
            if servos is not None:
                servos.ramp(servos.down_angle, rules["return_deg_per_s"])
                time.sleep(0.5)
                servos.close()
            if camera is not None:
                camera.close()
            if not args.headless:
                display.close()
        print("[controller] stopped: lamp off, aplay stopped, GPIO released", flush=True)


def run(args, cfg, camera, guards, detector, person_detector, deterrent, sound, servos) -> None:
    """The main loop; returns when the debug window is closed (q / Esc)."""
    if not args.headless:
        from . import display
    rules = cfg["controller"]
    c = cfg["camera"]
    scare_until = 0.0     # scare is running while now < scare_until
    quiet_until = 0.0     # no new scare before this
    next_flap = 0.0
    servo_up = False
    active_target = None
    active_camera = None
    last_frame_t = time.monotonic()
    fps = 0.0
    next_switch = last_frame_t + c["dwell_seconds"]
    switch_ms = 0.0
    feeds = [None] * len(camera.ids)  # per camera, its last (frame, detections, hits, time)
    print(f"[controller] watching cameras {camera.ids} for {rules['labels']} "
          f"(conf >= {rules['min_confidence']})", flush=True)

    servos.down()
    while True:
        switch_t = None
        if len(camera.ids) > 1 and time.monotonic() >= next_switch:
            switch_t = time.monotonic()
            camera.select((camera.current + 1) % len(camera.ids))
        frame = camera.read()
        if switch_t is not None:  # time the switch up to the new camera's first frame
            switch_ms = (time.monotonic() - switch_t) * 1000
            next_switch = time.monotonic() + c["dwell_seconds"]
        people = [det for det in person_detector.detect(frame) if det.label == "person"]
        animal_detections = detector.detect(frame)
        detections = animal_detections + people
        now = time.monotonic()
        guard = guards[camera.current]
        guard.update(people, now)
        hits = [
            det for det in animal_detections
            if det.label in rules["labels"] and det.confidence >= rules["min_confidence"]
        ]
        rejected = [det for det in hits if guard.blocks(det)]
        hits = [det for det in hits if not guard.blocks(det)]
        if (scare_until and active_camera == camera.current and
                active_target is not None and guard.blocks(active_target)):
            print("[controller] person matches active target box: stopping scare", flush=True)
            scare_until = 0.0
            active_target = None
            active_camera = None
            deterrent.end("person_abort")  # light off, aplay stopped, logged
            servos.ramp(servos.down_angle, rules["return_deg_per_s"])
            servo_up = False
            quiet_until = max(quiet_until, time.monotonic() + rules["cooldown_seconds"])
        feeds[camera.current] = (frame, detections, hits, now)

        if hits and now >= quiet_until:
            best = hits[0]
            active_target = best
            active_camera = camera.current
            print(f"[controller] SCARE: camera {camera.ids[camera.current]} {best.label} "
                  f"{best.confidence:.2f} at {best.box}", flush=True)
            scare_until = now + rules["scare_seconds"]
            quiet_until = scare_until + rules["cooldown_seconds"]
            next_flap = now
            # light.pattern (steady, strobe or random), then sound.source's next clip
            deterrent.start(now, "detection", label=best.label, confidence=best.confidence,
                            camera=camera.ids[camera.current])

        if scare_until and now < scare_until:
            if now >= next_flap:
                servo_up = not servo_up
                servos.up() if servo_up else servos.down()
                next_flap = now + rules["flap_seconds"]
        elif scare_until:
            print("[controller] scare over", flush=True)
            scare_until = 0.0
            active_target = None
            active_camera = None
            deterrent.end("timeout")  # light off, aplay stopped, logged
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
            elif rejected:
                state = "PERSON MATCH"
            else:
                state = "watching"
            status = [
                ("sound", sound.current.name if sound.is_playing() else "off",
                 sound.is_playing()),
                ("light", "ON" if scare_until else "off", bool(scare_until)),
                ("servo", "off" if angle is None else f"{angle:.0f} deg", servo_up),
                ("state", state, bool(scare_until)),
            ]
            views = []
            for i, feed in enumerate(feeds):
                live = i == camera.current
                if feed is None:
                    views.append((f"camera {camera.ids[i]}: waiting", None, [], [], False))
                    continue
                f_frame, f_dets, f_hits, f_t = feed
                age = "live" if live else f"{now - f_t:.1f}s ago"
                views.append((f"camera {camera.ids[i]}: {age}", f_frame, f_dets, f_hits, live))
            line2 = f"{fps:.1f} fps   {len(detections)} objects   {len(rejected)} person-match blocks"
            if len(camera.ids) > 1:
                line2 += f"   switch {switch_ms:.0f} ms every {c['dwell_seconds']}s"
            if not display.show(views, status, line2):
                return


if __name__ == "__main__":
    main()
