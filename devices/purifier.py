# 空气净化设备驱动 — GPIO 继电器 (模拟: 风扇+香薰片)
from devices.base import BaseDevice
from config import GPIO

try:
    import gpiod
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False


class AirPurifierDevice(BaseDevice):
    name = "air_purifier"

    def __init__(self):
        self._pin = GPIO["relay_purifier"]
        self._level = 0
        self._line = None

    def control(self, action: str, **params):
        if action == "set":
            self._level = max(0, min(3, int(params.get("level", 0))))
        elif action == "on":
            self._level = params.get("level", 1)
        elif action == "off":
            self._level = 0
        self._apply()
        return f"净化: {['关', '低', '中', '高'][self._level]}"

    def _apply(self):
        if self._line:
            self._line.set_value(1 if self._level > 0 else 0)

    def status(self) -> dict:
        return {"name": "air_purifier", "level": self._level}
