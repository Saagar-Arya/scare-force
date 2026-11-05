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
