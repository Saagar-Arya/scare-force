"""Main loop: camera -> detector -> decide -> light, servos, sound.

With several cameras (camera.ids) only one streams at a time, so the loop watches each for
camera.dwell_seconds, then switches to the next. A matching detection on any of them starts a scare:
the next clip from the sound library, servos flapping up/down and the
light (light.pattern: steady, strobe or random, as whole flashes). The clip sets the scare's
length (about scare_seconds, see scare/deterrent.py): light and sound end together then, the
servos stop flapping then, and nothing fires again until cooldown_seconds (+/- cooldown_jitter_s,
random each time) have passed.
Detection and switching keep running the whole time. A single two-class model handles each frame.
Only animal labels can trigger; person predictions never veto an animal.
Confirmation histories are scoped to each camera.
controller.person_min_confidence drops weaker person boxes (window, counts, logs).
IR cameras (camera.ir_models) stream once for a first frame, then are skipped (frame frozen)
unless camera.ir_on or the window's IR switch turns them on.
Light and sound start and stop through scare/deterrent.py (shared with scare.deter_test), which
logs one line and one CSV row per scare. Light settings are checked before the cameras open.
Ctrl+C, SIGTERM, SIGHUP, q / Esc or an error: the lamp is left off, aplay stopped, GPIO released.

Night mode (night.start in config.yaml, or the window's NIGHT switch): the IR camera alone watches
for motion (scare/motion.py, no model). Motion turns the lamp on steadily and the regular cameras
take turns with the detector for night.look_seconds; a confirmed pest gets the usual scare, after
which the lamp is back on steady; each pest sighting restarts the look time. With no pest the lamp
goes off and the IR camera watches again. The night window has its own sliders (motion threshold,
min area, persist frames); every motion event is a row in logs/motion/<date>/events.csv + a photo.

Run: python -m scare.controller              (debug window; q / Esc / Ctrl+C stops)
     python -m scare.controller --headless   (no window)
The debug window has sliders for the min confidence (all trigger labels), the person min confidence
and confirm frames; moving one applies at once, starting from config.yaml's values (not saved back).
"""

import argparse
import random
import time

import numpy as np

from . import ROOT, load_config
from .activation_log import ActivationLog
from .camera import Camera
from .confirmation import TargetConfirmer
from .detector import Detector
from .deterrent import Deterrent, Shutdown, handle_signals, ignore_signals
from .detection_log import DetectionLog
from .light import Light
from .motion import MotionDetector, MotionLog, draw
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

    camera = servos = detlog = None
    deterrent = Deterrent()
    try:
        deterrent.log = ActivationLog(ROOT / log_path) if log_path else None  # null: terminal only
        if cfg["log"].get("detections"):  # a photo + CSV row per confirmed detection
            detlog = DetectionLog(cfg["log"], ROOT)
        camera = Camera(c)
        confirmers = [TargetConfirmer(rules["confirm_frames"], rules["confirm_iou"],
                                      rules["confirmation_gap_seconds"]) for _ in camera.ids]
        deterrent.light = Light(cfg["light"])
        sound = deterrent.sound = Sound(cfg["sound"], ROOT, rules["scare_seconds"],
                                        rules["end_window_s"])
        sound.prime()  # switches the shared PWM clock now, so Servos reads its final rate
        servos = Servos(cfg["servos"])
        run(args, cfg, camera, confirmers, thresholds, detector, deterrent, sound, servos, detlog)
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
            if detlog is not None:
                detlog.close()  # writes the photos still queued
        print("[controller] stopped: lamp off, aplay stopped, GPIO released", flush=True)


def run(args, cfg, camera, confirmers, thresholds, detector, deterrent, sound, servos,
        detlog=None) -> None:
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
    # person boxes below this are dropped (window, counts, logs); people never trigger either way
    person_conf = float(rules.get("person_min_confidence", detector.conf))
    if not 0 < person_conf <= 1:
        raise ValueError(f"controller.person_min_confidence must be in (0, 1], got {person_conf}")
    print(f"[controller] watching {', '.join(camera.names)}, min confidence "
          + ", ".join(f"{k} {v}" for k, v in thresholds.items())
          + f", person {person_conf}, {rules['confirm_frames']} frame(s) to confirm", flush=True)

    # IR cameras (camera.ir_models): each streams once at startup for a first frame, then is
    # skipped (its frame stays frozen) unless IR is on (camera.ir_on, or the window's switch)
    ir = {i for i, m in enumerate(getattr(camera, "models", [])) if m in c.get("ir_models", [])}
    ir_on = bool(c.get("ir_on", True))
    if ir:
        print(f"[controller] IR camera(s) {', '.join(camera.names[i] for i in sorted(ir))}: "
              f"{'on' if ir_on else 'off (first frame only)'}", flush=True)

    def next_camera() -> int:
        """The next camera in switching order, skipping IR ones that have a frame and are off
        (or it is night, when the IR camera only watches for motion)."""
        for step in range(1, len(camera.ids) + 1):
            j = (camera.current + step) % len(camera.ids)
            if (ir_on and not night) or j not in ir or feeds[j] is None:
                return j
        return camera.current

    # night mode: the IR camera watches for motion; motion -> lamp on, regular cameras look
    n = cfg.get("night") or {}
    nm = n.get("motion") or {}
    motion = MotionDetector(nm.get("threshold", 25), nm.get("min_area_pct", 0.5),
                            nm.get("persist_frames", 3))
    mlog = MotionLog(ROOT)
    warmup_s = float(nm.get("warmup_s", 2.0))   # IR exposure settles, then locks
    look_s = float(n.get("look_seconds", 15))
    ir_i = min(ir) if ir else None
    night = bool(n.get("start", False))
    if night and ir_i is None:
        print("[controller] night.start: no IR camera, staying in day mode", flush=True)
        night = False
    phase = "watch"       # at night: "watch" (IR motion) or "look" (lamp on, regular cameras)
    look_until = 0.0
    watch_t0 = None       # when the IR camera started watching (warm-up, then exposure lock)
    exp_locked = False
    lamp_on = False       # the lamp is on for looking (not for a scare)
    next_lux_print = 0.0

    def lamp(on: bool) -> None:
        nonlocal lamp_on
        lamp_on = on
        if deterrent.light is not None:
            deterrent.light.on() if on else deterrent.light.off()

    if ir_i is not None:
        print(f"[controller] mode: {'NIGHT' if night else 'day'}; night: motion threshold "
              f"{motion.threshold}, min area {motion.min_area_pct}%, {motion.persist} frame(s), "
              f"look {look_s:g} s", flush=True)

    detector_conf = detector.conf  # config's floor; a lower slider lowers it to match
    controls = ncontrols = None

    def day_window() -> None:
        nonlocal controls
        display.add_controls(min(thresholds.values()), person_conf, confirmers[0].frames,
                             ir_on if ir else None, night_switch=ir_i is not None)
        controls = display.read_controls()

    def night_window() -> None:
        nonlocal ncontrols
        display.add_night_controls(motion.threshold, motion.min_area_pct, motion.persist)
        ncontrols = display.read_night_controls()

    if not args.headless:
        night_window() if night else day_window()
    announce_at = None    # print slider changes once they settle, not on every step of a drag

    servos.down()
    while True:
        want_night = night
        if not args.headless and night:
            now_n = display.read_night_controls()
            if now_n != ncontrols:
                want_night, motion.threshold, motion.min_area_pct, motion.persist = now_n
                if now_n[1:] != ncontrols[1:]:
                    announce_at = time.monotonic() + 1.0
                ncontrols = now_n
            if announce_at and time.monotonic() >= announce_at:
                print(f"[controller] night sliders: motion threshold {motion.threshold}, min area "
                      f"{motion.min_area_pct:.1f}%, {motion.persist} frame(s)", flush=True)
                announce_at = None
        elif not args.headless:
            now_controls = display.read_controls()
            if now_controls != controls:
                trigger_c, person_conf, frames, ir_switch, night_switch = now_controls
                if night_switch is not None:
                    want_night = night_switch
                if trigger_c != controls[0]:  # one value for every trigger label
                    thresholds.update(dict.fromkeys(thresholds, trigger_c))
                if frames != controls[2]:
                    for confirmer in confirmers:
                        confirmer.frames = frames
                if ir_switch is not None:
                    ir_on = ir_switch
                detector.conf = min(detector_conf, trigger_c, person_conf)
                controls = now_controls
                announce_at = time.monotonic() + 1.0
            if announce_at and time.monotonic() >= announce_at:
                print(f"[controller] sliders: trigger confidence {controls[0]:.2f} (all trigger "
                      f"labels), person {controls[1]:.2f}, {controls[2]} frame(s) to confirm"
                      + (f", IR {'on' if ir_on else 'off'}" if ir else ""), flush=True)
                announce_at = None
        if want_night != night:
            night, phase, watch_t0 = want_night, "watch", None
            motion.reset()
            motion.event = None
            print(f"[controller] mode: {'NIGHT (IR motion watch)' if night else 'day'}", flush=True)
            if not night:
                if lamp_on and not scare_until:
                    lamp(False)
                lamp_on = False
                next_switch = 0.0   # back to the regular cameras
            if not args.headless:
                night_window() if night else day_window()

        if night and phase == "watch" and not scare_until:
            if camera.current != ir_i or watch_t0 is None:
                camera.select(ir_i)
                watch_t0, exp_locked = time.monotonic(), False
                motion.reset()
            frame, md = camera.read_meta()
            now = time.monotonic()
            if not exp_locked and now - watch_t0 >= warmup_s:
                camera.lock_exposure(md)   # auto-exposure drifting would look like motion
                exp_locked = True
                motion.reset()
            r = motion.update(frame, now, armed=exp_locked)
            feeds[ir_i] = (frame, [], [], now)
            fps = 0.9 * fps + 0.1 / max(now - last_frame_t, 1e-6)
            last_frame_t = now
            lux = md.get("Lux", float("nan"))
            if r.started:
                ev = motion.event
                photo = mlog.photo(np.ascontiguousarray(frame[..., ::-1]), r)
                mlog.write(ev, md, photo, f"lamp on, look {look_s:g} s")
                print(f"[controller] NIGHT MOTION: {camera.names[ir_i]} {r.area_pct:.1f}% of the "
                      f"frame at {ev['box']}, lux {lux:.0f}: lamp on, regular cameras look "
                      f"{look_s:g} s", flush=True)
                motion.event = None
                lamp(True)
                phase, look_until, next_switch, watch_t0 = "look", now + look_s, 0.0, None
            if args.headless:
                if now >= next_lux_print:
                    print(f"[controller] night watch: {fps:.0f} fps, lux {lux:.0f}", flush=True)
                    next_lux_print = now + 60
                continue
            exp = f"exp {md.get('ExposureTime', 0) / 1000:.1f} ms gain {md.get('AnalogueGain', 0):.2f}"
            line1 = ("MOTION: lamp on" if r.started else "candidate" if r.boxes
                     else "warming up" if not exp_locked else "watching for motion")
            line2 = (f"{fps:.0f} fps   changed {100 * r.changed:.1f}%   lux {lux:.0f}   {exp} "
                     f"{'locked' if exp_locked else 'auto'}   motion: threshold {motion.threshold}, "
                     f"min {motion.min_area_pct:.1f}%, {motion.persist} frame(s)   "
                     f"look {look_s:g} s")
            if not display.show_night(draw(np.ascontiguousarray(frame[..., ::-1]), r),
                                      f"NIGHT  {camera.names[ir_i]}: motion watch",
                                      f"NIGHT   {line1}", line2, bool(r.boxes)):
                return
            continue
        switch_t = None
        if len(camera.ids) > 1 and time.monotonic() >= next_switch:
            switch_t = time.monotonic()
            camera.select(next_camera())  # no-op if it is the open camera already
        frame = camera.read()
        if switch_t is not None:  # time the switch up to the new camera's first frame
            switch_ms = (time.monotonic() - switch_t) * 1000
            next_switch = time.monotonic() + c["dwell_seconds"]
        detections = [det for det in detector.detect(frame)
                      if det.label != "person" or det.confidence >= person_conf]
        people = [det for det in detections if det.label == "person"]
        animal_detections = [det for det in detections if det.label in rules["labels"]]
        now = time.monotonic()
        hits = [
            det for det in animal_detections
            if det.label in thresholds and det.confidence >= thresholds[det.label]
        ]
        hits = confirmers[camera.current].update(hits, now)
        feeds[camera.current] = (frame, detections, hits, now)
        if camera.current in ir and (not ir_on or night):  # IR off: first frame kept, move on
            next_switch = 0.0
        if night and phase == "look" and hits:   # a pest in view: keep looking
            look_until = max(look_until, now + look_s)

        if hits and now >= quiet_until:
            best = hits[0]
            print(f"[controller] SCARE: {camera.names[camera.current]} {best.label} "
                  f"{best.confidence:.2f} at {best.box}", flush=True)
            cam_id = camera.ids[camera.current]
            det_id = detlog.record(frame, hits, cam_id, camera.names[camera.current], "scare",
                                   len(people), now) if detlog else None  # photo is async
            # light.pattern (whole flashes) and the library's next clip; the clip sets the length
            length = deterrent.start(now, "detection", rules["scare_seconds"], label=best.label,
                                     confidence=best.confidence, camera=cam_id,
                                     detection_id=det_id)
            scare_until = now + length  # the arms flap until the light and sound end
            jit = float(rules.get("cooldown_jitter_s", 0.0))  # Nov 10: varied rest between scares
            quiet_until = scare_until + max(0.0, rules["cooldown_seconds"] + random.uniform(-jit, jit))
            next_flap = now
            note = None
        elif hits:
            note = (f"{hits[0].label} {hits[0].confidence:.2f} ignored: " +
                    ("scare running" if scare_until else f"cooldown, {quiet_until - now:.1f}s left"))
            if detlog:  # at most one per log.photo_every_s per camera
                detlog.record(frame, hits, camera.ids[camera.current],
                              camera.names[camera.current],
                              "ignored: scare running" if scare_until else "ignored: cooldown",
                              len(people), now)
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
            deterrent.end("timeout")  # its timer already ended light and sound; this logs it
            servos.ramp(servos.down_angle, rules["return_deg_per_s"])
            servo_up = False
            if night and phase == "look":
                lamp(True)   # the scare's flashes ended with the light off: steady again
        if night and phase == "look" and not scare_until and now >= look_until:
            print(f"[controller] night: no pest for {look_s:g} s, lamp off, IR watches again",
                  flush=True)
            lamp(False)
            phase = "watch"

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
            if night and phase == "look" and not scare_until:
                state = f"NIGHT look {max(0.0, look_until - now):.0f}s"
            status = [
                ("sound", sound.current.name if sound.is_playing() else "off",
                 sound.is_playing()),
                ("light", "ON" if scare_until else "ON (night look)" if lamp_on else "off",
                 bool(scare_until) or lamp_on),
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
                age = ("live" if live else "night motion watch" if i in ir and night
                       else "IR off, frozen" if i in ir and not ir_on
                       else f"{now - f_t:.1f}s ago")
                views.append((f"{camera.names[i]}: {age}", f_frame, f_dets, f_hits, live))
            line2 = (f"{fps:.1f} fps   {len(detections)} objects   {len(hits)} confirmed animals"
                     f"   person >= {person_conf:.2f}"
                     f"   trigger: conf >= {min(thresholds.values()):.2f}"
                     f" x {confirmers[0].frames} frames")
            if len(camera.ids) > 1:
                line2 += f"   switch {switch_ms:.0f} ms every {c['dwell_seconds']}s"
            if not display.show(views, status, line2):
                return


if __name__ == "__main__":
    main()
