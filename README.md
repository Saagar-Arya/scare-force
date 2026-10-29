# Scare Force

Camera → YOLO detection → controller → light, servos, sound. One Python process on the Pi 4.

```
scare/camera.py      Camera.read() -> RGB frame, Camera.select(i) switches camera
scare/detector.py    Detector.detect(frame) -> [Detection(label, confidence, box)]
scare/light.py       Light.on() / off() / strobe(period)    GPIO26 (pin 37) -> relay
scare/servos.py      Servos.up() / down() / set(angle)      GPIO19, hardware PWM
scare/sound.py       Sound.play(n) / stop() / is_playing()  3.5 mm jack, clips in sounds/
scare/controller.py  main loop: which labels trigger, how long, cooldown
scare/display.py     debug window: cameras side by side, boxes, status bar
config.yaml          cameras, pins, servo angles, labels, timings, volume
```

Every output prints what it is about to do (`[light] GPIO26 -> HIGH (on)`) right before doing it.

## Setup (once)

```bash
python3 -m venv --system-site-packages .venv   # picamera2 + gpiozero come from apt
.venv/bin/pip install onnxruntime
```

Two Camera Module 3s on an Arducam Multi Camera Adapter V2.2 (ports A and C), and both servos on
one hardware PWM channel on GPIO19. The adapter sits on the GPIO header and uses GPIO4, 17 and 18
(physical pins 7, 11, 12) to switch cameras: without it seated there both cameras are detected but
every frame comes back black. In /boot/firmware/config.txt, with `camera_auto_detect=0`:

```
dtoverlay=camera-mux-4port,cam0-imx708,cam2-imx708     # cam0..cam3 = ports A..D
dtoverlay=pwm,pin=19,func=2
```

then reboot. `rpicam-hello --list-cameras` should list two imx708s. The Pi 4 has one CSI port, so
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
.venv/bin/python -m scare.light       # on 2 s, strobe 2 s, off
.venv/bin/python -m scare.servos      # up, down, release
.venv/bin/python -m scare.servos sweep  # each servo alone 0-270-0, then both
.venv/bin/python -m scare.servos mid    # centre (135), then slow sweep +/-60 around it (or pass deg)
.venv/bin/python -m scare.sound       # plays every clip
```

Everything together. A `teddy bear` (any stuffed animal, as far as YOLO's COCO labels go)
plays a clip, flaps the servos and turns the light on. The window shows every camera side by side
(the ones not streaming right now dimmed, with the age of their last frame), draws every box
(red = trigger) and a status bar: sound, light, servo angle, scare/cooldown state, fps.

```bash
.venv/bin/python -m scare.controller             # debug window; q / Esc / Ctrl+C stops
.venv/bin/python -m scare.controller --headless  # no window
```

## Sounds

`sounds/` holds generic placeholder clips. `play(n)` plays the n-th `.wav` sorted by name, so
name them `0_...`, `1_...`. Swap in real clips any time.
