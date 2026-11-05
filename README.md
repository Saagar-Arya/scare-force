# Scare Force

Camera → YOLO detection → controller → light, servos, sound. One Python process on the Pi 4.

```
scare/camera.py          Camera.read() -> RGB frame, Camera.select(i) switches camera
scare/detector.py        Detector.detect(frame) -> [Detection(label, confidence, box)]
scare/confirmation.py    a target must show up in confirm_frames frames in a row to count
scare/light.py           Light.start(deadline): whole flashes to the end   GPIO26 (pin 37) -> relay
scare/servos.py          Servos.set(angle) / ramp(angle) / close()        GPIO19, hardware PWM
scare/motion.py          the arms' scare: drop, wave to the end, reset in the cooldown
scare/sound.py           Sound.next_clip() / play(clip) / stop()          3.5 mm jack
scare/sound_library.py   which clips play, from scare/sounds/library.yaml
scare/sound_fill.py      makes each clip last the whole scare, ending at a pause
scare/deterrent.py       one scare: light, sound and arms start and end together; log it
scare/activation_log.py  one CSV row per scare (log.activations)
scare/controller.py      main loop: cameras -> detector -> scare, cooldown
scare/deter_test.py      run scares by hand, no camera (light, sound, arms)
scare/display.py         debug window: cameras side by side, boxes, status bar
config.yaml              every setting: cameras, model, thresholds, pins, light, servos, sound, log
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
.venv/bin/python -m scare.servos      # motion.up_angle, motion.down_angle, release
.venv/bin/python -m scare.servos sweep  # both servos 0-270-0, twice
.venv/bin/python -m scare.servos mid    # centre (135), then slow sweep +/-60 around it (or pass deg)
.venv/bin/python -m scare.sound       # plays every clip
.venv/bin/python -m scare.deter_test  # one scare by hand (light, sound, arms); --help for options
```

Everything together. A raccoon, groundhog or squirrel (`models/scare_four_320.onnx`, above
`controller.min_confidence` for `controller.confirm_frames` frames in a row) starts a scare: the
next clip, the arms dropping then waving, and the light pattern, then `cooldown_seconds` of quiet
(see "One scare" below). The window shows every camera side by side (the ones not streaming right now dimmed, with
the age of their last frame), draws every box (red = trigger) and a status bar: sound, light, servo
angle, scare/cooldown state, fps. Every scare is a row in `logs/activations.csv`.

```bash
.venv/bin/python -m scare.controller             # debug window; q / Esc / Ctrl+C stops
.venv/bin/python -m scare.controller --headless  # no window
```

## Sounds

Clips come from the sound library in `scare/sounds/` (see `scare/sounds/SOURCE.md` for where they
come from, their licenses and how to update them). Each scare plays the next clip of the generic
set, alternating sound types.

At startup `scare/sound_fill.py` renders each clip (into RAM, `/dev/shm`) to end at the pause
nearest `controller.scare_seconds`, within `+/- controller.end_window_s`, so no bark or word is
cut through; a short clip repeats, and a clip with no pause there (the sirens) fades out over
`sound.end_fade_s`, ending at `scare_seconds`. With the generic set and 5 +/- 0.5 s, scares last
4.76-5.34 s. Levels are unchanged.

## One scare

An outside viewer should see light, sound and arms start together and stop together. The sound
sets the end (`scare/deterrent.py`): the scare lasts its clip's render, and

- light: whole flashes only. The first comes on at once, the last goes off exactly at the end;
  the period is stretched or shrunk slightly to fit (2 Hz over 4.76 s: 10 flashes), never past
  `light.max_rate_hz` or under the relay minimum.
- sound: the render plays out and ends at its pause (aplay starts a few ms after the light).
- arms (`scare/motion.py`): the drop (latch release, or a servo move while no latch is fitted),
  then waving between `motion.up_angle` and `motion.down_angle`, the move time stretched so the
  last move lands on the end. The arms then hold still for `motion.reset_delay_s` and ease back
  to `motion.rest_angle` (T-pose) in the cooldown: the reset is not part of the scare.

Every CSV row records the scare's length (`scare_s`), the light's (`light_s`, first on to last
off), the arms' drop and moves, and the sound's clip, repeats and ending.

The species sets (raccoon, groundhog, squirrel) and their clips are in the repo too, but not
connected: only the generic set plays. `python -m scare.sound_library` checks every set.
