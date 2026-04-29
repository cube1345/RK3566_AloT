# 灯光驱动 — GPIO 继电器
from devices.base import BaseDevice
from config import GPIO

try:
    import gpiod
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False


class LightDevice(BaseDevice):
    name = "light"

    def __init__(self):
        self._pin = GPIO["relay_light"]
        self._state = "off"
        self._brightness = 0
        self._line = None

    def control(self, action: str, **params):
        if action in ("on", "off"):
            self._state = action
            self._brightness = params.get("brightness", 255) if action == "on" else 0
        elif action == "set":
            self._state = "on" if params.get("brightness", 0) > 0 else "off"
            self._brightness = params.get("brightness", 255)
        self._apply()
        return f"灯光: {self._state} ({self._brightness})"

    def _apply(self):
        if self._line:
            self._line.set_value(1 if self._state == "on" else 0)

    def status(self) -> dict:
        return {"name": "light", "state": self._state, "brightness": self._brightness}
