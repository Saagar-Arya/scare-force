# Scare Force

Camera → YOLO detection → controller → light, servos, sound. One Python process on the Pi 4.

```
scare/camera.py      Camera.read() -> RGB frame
scare/detector.py    Detector.detect(frame) -> [Detection(label, confidence, box)]
scare/light.py       Light.on() / off() / strobe(period)    GPIO23 (pin 16) -> relay
scare/servos.py      Servos.up() / down() / set(angle)      GPIO18 + GPIO19, hardware PWM
scare/sound.py       Sound.play(n) / stop() / is_playing()  3.5 mm jack, clips in sounds/
scare/controller.py  main loop: which labels trigger, how long, cooldown
scare/display.py     debug window: camera, boxes, status bar
config.yaml          pins, servo angles, labels, timings, volume
```

Every output prints what it is about to do (`[light] GPIO23 -> HIGH (on)`) right before doing it.

## Setup (once)

```bash
python3 -m venv --system-site-packages .venv   # picamera2 + gpiozero come from apt
.venv/bin/pip install onnxruntime
```

Servos need the Pi 4's hardware PWM on GPIO18/19:

```bash
echo "dtoverlay=pwm-2chan" | sudo tee -a /boot/firmware/config.txt && sudo reboot
```

That PWM clock is shared with the 3.5 mm audio jack: once any sound plays it runs 2.14x fast until
the next reboot (107 Hz instead of 50 Hz on a scope). `scare/servos.py` reads the real rate with
`vcgencmd measure_clock pwm` and scales its pulses, so nothing needs doing, but anything else driving
these pins has to do the same.

Wiring: servo signal wires to GPIO18 (physical pin 12) and GPIO19 (pin 35). Power the servos from
their own 5 V supply, not the Pi, and join that supply's ground to a Pi ground pin.

## Run

Test each part on its own, from this folder:

```bash
.venv/bin/python -m scare.camera      # saves camera_test.jpg
.venv/bin/python -m scare.detector    # one frame (or pass an image path), prints detections
.venv/bin/python -m scare.light       # on 2 s, strobe 2 s, off
.venv/bin/python -m scare.servos      # up, down, release
.venv/bin/python -m scare.servos sweep  # each servo alone 0-270-0, then both
.venv/bin/python -m scare.servos mid    # centre (135), then slow sweep +/-60 around it (or pass deg)
.venv/bin/python -m scare.sound       # plays every clip
```

Everything together. A `teddy bear` (any stuffed animal, as far as YOLO's COCO labels go)
plays a clip, flaps the servos and turns the light on. The window draws every box (red = trigger)
and a status bar: sound, light, servo angle, scare/cooldown state, fps.

```bash
.venv/bin/python -m scare.controller             # debug window; q / Esc / Ctrl+C stops
.venv/bin/python -m scare.controller --headless  # no window
```

## Sounds

`sounds/` holds generic placeholder clips. `play(n)` plays the n-th `.wav` sorted by name, so
name them `0_...`, `1_...`. Swap in real clips any time.
