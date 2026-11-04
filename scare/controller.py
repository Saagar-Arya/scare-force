"""Main loop: camera -> detector -> decide -> light, servos, sound.

With several cameras (camera.ids) only one streams at a time, so the loop watches each for
camera.dwell_seconds, then switches to the next. A matching detection on any of them starts a scare:
the next clip from the sound library, servos flapping up/down and the
light on (light.pattern: steady, strobe or random). After scare_seconds everything stops, then
nothing fires again until cooldown_seconds have passed.
Detection and switching keep running the whole time. A single four-class model handles each frame.
Only animal labels can trigger; person predictions never veto an animal.
Confirmation histories are scoped to each camera.
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
from .confirmation import TargetConfirmer
from .detector import Detector
from .deterrent import Deterrent, Shutdown, handle_signals, ignore_signals
from .light import Light
from .servos import Servos
from .sound import Sound


def min_confidences(rules, detector_conf: float) -> dict:
    """controller.min_confidence as {label: threshold} for every trigger label.

    Either one number for all labels, or a mapping with a `default` and per-label overrides.
    """
    raw = rules["min_confidence"]
    if isinstance(raw, dict):
        unknown = set(raw) - set(rules["labels"]) - {"default"}
        if unknown:
            raise ValueError(f"min_confidence: not in controller.labels: {sorted(unknown)}")
        if "default" not in raw and set(rules["labels"]) - set(raw):
            raise ValueError("min_confidence: needs a default for labels it doesn't list")
        out = {label: raw.get(label, raw.get("default")) for label in rules["labels"]}
    else:
        out = {label: raw for label in rules["labels"]}
    for label, t in out.items():
        if not isinstance(t, (int, float)) or not 0 < t <= 1:
            raise ValueError(f"min_confidence for {label} must be in (0, 1], got {t!r}")
        if t < detector_conf:
            print(f"[controller] note: {label} min_confidence {t} is below detector.conf_threshold "
                  f"{detector_conf}, so it acts as {detector_conf}", flush=True)
    return out


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

    detector = Detector(d, ROOT)
    missing = set(rules["labels"]) - set(detector.labels)
    if missing:  # a typo or the wrong model would otherwise never trigger, silently
        raise ValueError(f"controller.labels not in {d['labels']}: {sorted(missing)}")
    thresholds = min_confidences(rules, d["conf_threshold"])  # validate before hardware
    log_path = cfg["log"]["activations"]

    camera = servos = None
    deterrent = Deterrent()
    try:
        deterrent.log = ActivationLog(ROOT / log_path) if log_path else None  # null: terminal only
        camera = Camera(c)
        confirmers = [TargetConfirmer(rules["confirm_frames"], rules["confirm_iou"],
                                      rules["confirmation_gap_seconds"]) for _ in camera.ids]
        deterrent.light = Light(cfg["light"])
        sound = deterrent.sound = Sound(cfg["sound"], ROOT)
        sound.prime()  # switches the shared PWM clock now, so Servos reads its final rate
        servos = Servos(cfg["servos"])
        run(args, cfg, camera, confirmers, thresholds, detector, deterrent, sound, servos)
    except (KeyboardInterrupt, Shutdown) as e:
        print(f"\n[controller] stopping ({str(e) or 'Ctrl+C'})", flush=True)
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


def run(args, cfg, camera, confirmers, thresholds, detector, deterrent, sound, servos) -> None:
    """The main loop; returns when the debug window is closed (q / Esc)."""
    if not args.headless:
        from . import display
    rules = cfg["controller"]
    c = cfg["camera"]
    scare_until = 0.0     # scare is running while now < scare_until
    quiet_until = 0.0     # no new scare before this
    next_flap = 0.0
    servo_up = False
    last_frame_t = time.monotonic()
    fps = 0.0
    next_switch = last_frame_t + c["dwell_seconds"]
    switch_ms = 0.0
    next_note = 0.0       # the "seen but ignored" notes print at most once a second
    feeds = [None] * len(camera.ids)  # per camera, its last (frame, detections, hits, time)
    print(f"[controller] watching {', '.join(camera.names)}, min confidence "
          + ", ".join(f"{k} {v}" for k, v in thresholds.items())
          + f", {rules['confirm_frames']} frame(s) to confirm", flush=True)

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
        detections = detector.detect(frame)
        people = [det for det in detections if det.label == "person"]
        animal_detections = [det for det in detections if det.label in rules["labels"]]
        now = time.monotonic()
        hits = [
            det for det in animal_detections
            if det.label in thresholds and det.confidence >= thresholds[det.label]
        ]
        hits = confirmers[camera.current].update(hits, now)
        feeds[camera.current] = (frame, detections, hits, now)

        if hits and now >= quiet_until:
            best = hits[0]
            print(f"[controller] SCARE: {camera.names[camera.current]} {best.label} "
                  f"{best.confidence:.2f} at {best.box}", flush=True)
            scare_until = now + rules["scare_seconds"]
            quiet_until = scare_until + rules["cooldown_seconds"]
            next_flap = now
            # light.pattern (steady, strobe or random), then the library's next clip
            deterrent.start(now, "detection", rules["scare_seconds"], label=best.label,
                            confidence=best.confidence, camera=camera.ids[camera.current])
            note = None
        elif hits:
            note = (f"{hits[0].label} {hits[0].confidence:.2f} ignored: " +
                    ("scare running" if scare_until else f"cooldown, {quiet_until - now:.1f}s left"))
        elif people:
            note = f"{len(people)} person(s), no target"
        else:
            note = None
        if note and now >= next_note:
            print(f"[controller] {camera.names[camera.current]}: {note}", flush=True)
            next_note = now + 1.0

        if scare_until and now < scare_until:
            if now >= next_flap:
                servo_up = not servo_up
                servos.up() if servo_up else servos.down()
                next_flap = now + rules["flap_seconds"]
        elif scare_until:
            print("[controller] scare over", flush=True)
            scare_until = 0.0
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
                    views.append((f"{camera.names[i]}: waiting", None, [], [], False))
                    continue
                f_frame, f_dets, f_hits, f_t = feed
                age = "live" if live else f"{now - f_t:.1f}s ago"
                views.append((f"{camera.names[i]}: {age}", f_frame, f_dets, f_hits, live))
            line2 = f"{fps:.1f} fps   {len(detections)} objects   {len(hits)} confirmed animals"
            if len(camera.ids) > 1:
                line2 += f"   switch {switch_ms:.0f} ms every {c['dwell_seconds']}s"
            if not display.show(views, status, line2):
                return


if __name__ == "__main__":
    main()
