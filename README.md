# Scare Force

Camera → YOLO detection → controller → light, servos, sound. One Python process on the Pi 4.

```
scare/camera.py          Camera.read() -> RGB frame, Camera.select(i) switches camera
scare/detector.py        Detector.detect(frame) -> [Detection(label, confidence, box)]
scare/confirmation.py    a target must show up in confirm_frames frames in a row to count
scare/light.py           Light.start(deadline): whole flashes to the end   GPIO26 (pin 37) -> relay
scare/servos.py          Servos.up() / down() / set(angle)                GPIO19, hardware PWM
scare/sound.py           Sound.next_clip() / play(clip) / stop()          3.5 mm jack
scare/sound_library.py   which clips play, from scare/sounds/library.yaml
scare/sound_fill.py      makes each clip last the scare, ending at a pause
scare/deterrent.py       one scare's light + sound: start and end together, log it
scare/activation_log.py  one CSV row per scare (log.activations)
scare/detection_log.py   photo + annotated copy + CSV row per confirmed detection (log.detections)
scare/export.py          zip of photos, CSVs and an HTML report: python -m scare.export
scare/controller.py      main loop: cameras -> detector -> scare, cooldown
scare/deter_test.py      run scares by hand, no camera (light + sound only)
scare/display.py         debug window: cameras side by side, boxes, status bar
config.yaml              every setting: cameras, model, thresholds, pins, light, servos, sound, log
data/blank-photos/       camera photos with no animal in view (negatives), see its README
```

Every output prints what it is about to do (`[light] GPIO26 -> HIGH (on)`) right before doing it.

## Setup (once)

```bash
python3 -m venv --system-site-packages .venv   # picamera2 + gpiozero come from apt
.venv/bin/pip install onnxruntime
```

Two Camera Module 3s (ports A and C) and an IMX219-160 IR-CUT (port B) on an Arducam Multi Camera
Adapter V2.2, and both servos on one hardware PWM channel on GPIO19. The adapter sits on the GPIO
header and uses GPIO4, 17 and 18 (physical pins 7, 11, 12) to switch cameras: without it seated
there the cameras are detected but every frame comes back black. In /boot/firmware/config.txt, with `camera_auto_detect=0`:

```
dtoverlay=camera-mux-4port,cam0-imx708,cam1-imx219,cam2-imx708     # cam0..cam3 = ports A..D
dtoverlay=pwm,pin=19,func=2
```

then reboot. `rpicam-hello --list-cameras` should list two imx708s and an imx219. The Pi 4 has one CSI port, so
only one camera streams at a time: the controller watches each for `camera.dwell_seconds` and
switches (about 0.5 s). With a single camera straight on the CSI port, use `dtoverlay=imx708`
instead; missing cameras in `camera.ids` are skipped.

That PWM clock is shared with the 3.5 mm audio jack: once any sound plays it runs 2.14x fast until
the next reboot (107 Hz instead of 50 Hz on a scope). `scare/servos.py` reads the real rate with
`vcgencmd measure_clock pwm` and scales its pulses, so nothing needs doing, but anything else driving
these pins has to do the same.

Wiring: both servo signal wires to GPIO19 (physical pin 35) through a Y cable (they always get
the same angle; GPIO12/13 read as stuck at ground with the camera adapter fitted), the light's
relay input to GPIO26 (pin 37). Power the servos from their own 5 V supply, not the Pi, and join that
supply's ground to a Pi ground pin.

## Run

Test each part on its own, from this folder:

```bash
.venv/bin/python -m scare.camera      # saves camera_test_<n>.jpg from every camera
.venv/bin/python -m scare.detector    # one frame (or pass an image path), prints detections
.venv/bin/python -m scare.light       # on 2 s, then light.pattern 3 s, off
.venv/bin/python -m scare.servos      # up, down, release
.venv/bin/python -m scare.servos sweep  # both servos 0-270-0, twice
.venv/bin/python -m scare.servos mid    # centre (135), then slow sweep +/-60 around it (or pass deg)
.venv/bin/python -m scare.sound       # plays every clip
.venv/bin/python -m scare.deter_test  # one scare by hand (light + sound); --help for options
```

Everything together. A raccoon, groundhog or squirrel (`models/scare_four_320.onnx`, above
`controller.min_confidence` for `controller.confirm_frames` frames in a row) starts a scare: the
next clip, the servos flapping and the light pattern, for `scare_seconds`, then `cooldown_seconds`
of quiet. The window shows every camera side by side (the ones not streaming right now dimmed, with
the age of their last frame), draws every box (red = trigger) and a status bar: sound, light, servo
angle, scare/cooldown state, fps. Every scare is a row in `logs/activations.csv`.

```bash
.venv/bin/python -m scare.controller             # debug window; q / Esc / Ctrl+C stops
.venv/bin/python -m scare.controller --headless  # no window
```

The window has three sliders above the feeds: trigger confidence (for every trigger label), person
confidence (weaker person boxes are dropped; people never trigger) and confirm frames. They start
at config.yaml's values (`controller.min_confidence`, `person_min_confidence`, `confirm_frames`),
apply at once and are not saved back.
The IR camera (`camera.ir_models`, the IMX219) streams once at startup for a first frame and then
stays frozen while only the two regular cameras switch, until the window's IR switch (fourth
slider, 0/1) or `camera.ir_on: true` turns it on.

### Day and night

The top slider, **NIGHT**, switches the window between two views (headless runs use
`night.start` in config.yaml):

- **Day (0):** the view above: the regular cameras take turns with the detector.
- **Night (1):** the IR camera alone watches for motion (scare/motion.py: background difference,
  no model). Motion turns the lamp on steadily and the regular cameras take turns with the detector
  for `night.look_seconds` (15 s); a pest in view restarts that time, and a confirmed pest gets the
  usual scare, after which the lamp is steady again. With no pest the lamp goes off and the IR
  camera watches again. The night window has its own sliders: motion threshold, min area and
  persist frames (`night.motion`). Every motion event is a row in `logs/motion/<date>/events.csv`
  with a photo.

Testing the motion detection alone (no detector, lamp, sound or servos; stop the controller first):

```
.venv/bin/python -m scare.motion_watch             # IR feed with motion boxes and sliders
.venv/bin/python -m scare.motion_watch --headless  # terminal only, e.g. outside at night
```

## Detection photos and the report

Every confirmed detection saves the frame, an annotated copy (boxes, species, confidence, camera,
date and time burned in) and a row in `logs/detections.csv`, whether it fired a scare or was
ignored in a scare or cooldown (those at most one per camera every `log.photo_every_s`). Photos
go to `logs/photos/<date>/`, about 100 KB per detection. Each scare's row in
`logs/activations.csv` names the detection that fired it (`detection_id`).

To get the data off the Pi:

    .venv/bin/python -m scare.export                     # everything, to logs/exports/
    .venv/bin/python -m scare.export --from 2026-11-20 --to 2026-11-24 --out /media/$USER/<stick>/farm.zip

Unzip and open `report.html`: detections per day and species, scares, and every detection with
its photo. Times come from the Pi's clock. Check `timedatectl` before a farm run: with no network
and no RTC the clock can be wrong after a reboot.

## Sounds

Clips come from the sound library in `scare/sounds/` (see `scare/sounds/SOURCE.md` for where they
come from, their licenses and how to update them). Each scare plays a random clip of the generic
set, never the one the scare before played.

At startup `scare/sound_fill.py` renders each clip (into RAM, `/dev/shm`) to end at the pause
nearest `controller.scare_seconds`, within `+/- controller.end_window_s`, so no bark or word is
cut through; a short clip repeats, and a clip with no pause there (the sirens) fades out over
`sound.end_fade_s`, ending at `scare_seconds`. With the generic set and 5 +/- 0.5 s, scares last
4.76-5.34 s. Levels are unchanged. That length is the scare's: the light runs whole flashes (the
first on at once, the last off exactly at the end, the period stretched slightly to fit, never
past `light.max_rate_hz` or under the relay minimum), and the controller flaps the arms for it.
Each CSV row records the scare's length (`scare_s`), the light's (`light_s`), and the clip's
repeats and ending.

The species sets (raccoon 12, groundhog 4, squirrel 4) are in the repo but not connected: only
the generic set plays. `python -m scare.sound_library` checks every set.

## Run at boot (demo, farm test)

`tools/pi_boot.sh <pi-ip> enable` (from the Mac clone) installs `tools/scare-force.service`: on every
power-up the Pi starts `scare.controller --headless` (detection + deterrence, no window, no commands),
restarts it 10 s after any crash, and waits up to 20 s for network time first (no RTC). `status`,
`logs` and `disable` do what they say. On the Pi: `systemctl status scare-force`,
`journalctl -u scare-force -f`; stop it before a manual or windowed run (`sudo systemctl stop
scare-force`, then `start`). `tools/pi_update.sh` stops it for an update and starts it again.

## Operating it without a computer (demo, farm)

Once the boot service is enabled (above):
- **On:** switch power on at the power controller. The Pi boots and starts detection + deterrence by
  itself in about a minute; the lamp flashes twice (`controller.ready_flashes`) when it is live
  (only if the deterrent switch is on). Detection works either way.
- **Deterrents off, detection on:** the deterrent switch (lamp, relay board).
- **Off:** switch power off at the controller. There is no clean shutdown: log rows are fsynced as
  they are written, so at most the photo being saved is lost, but a hard power cut can (rarely)
  corrupt the SD card. Keep a backup image of the card from before the farm test.
- No network at the farm means no network time: see `clock_synced` in detections.csv.

## Arms (Nov 11)

Rest = arms down; a scare flaps them up `servos.flap_deg` (~90, peak about a T-pose) and back, as
mirror images. Each servo has its own signal: "a" on GPIO19 (pin 35, hardware PWM), "b" on GPIO16
(pin 36, software PWM). Each has its own `down` angle and `up_dir`, calibrated on the Pi with
`python -m scare.servos cal NAME DEG`; check with `python -m scare.servos` and `... flap`.
