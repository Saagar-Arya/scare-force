"""Light: one GPIO pin driving a relay. strobe() blinks it in a background thread.

Self-test: python -m scare.light   (on 2 s, strobe 2 s, off)
"""

from gpiozero import DigitalOutputDevice


class Light:
    def __init__(self, pin: int, active_high: bool = True):
        self.pin = pin
        self.on_level, self.off_level = ("HIGH", "LOW") if active_high else ("LOW", "HIGH")
        self.out = DigitalOutputDevice(pin, active_high=active_high, initial_value=False)

    def on(self) -> None:
        print(f"[light] GPIO{self.pin} -> {self.on_level} (on)", flush=True)
        self.out.on()

    def off(self) -> None:
        print(f"[light] GPIO{self.pin} -> {self.off_level} (off)", flush=True)
        self.out.off()

    def strobe(self, period: float) -> None:
        """Toggle every `period` seconds until on() or off() is called."""
        print(f"[light] GPIO{self.pin} -> strobe every {period}s", flush=True)
        self.out.blink(on_time=period, off_time=period, background=True)

    @property
    def is_on(self) -> bool:
        return bool(self.out.value)

    def close(self) -> None:
        self.out.close()


if __name__ == "__main__":
    import time

    from . import load_config

    cfg = load_config()["light"]
    light = Light(cfg["pin"], cfg["active_high"])
    light.on()
    time.sleep(2)
    light.strobe(0.25)
    time.sleep(2)
    light.off()
    light.close()
