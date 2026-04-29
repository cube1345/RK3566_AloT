# 风扇驱动 — GPIO 继电器
from devices.base import BaseDevice
from config import GPIO

try:
    import gpiod
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False


class FanDevice(BaseDevice):
    name = "fan"

    def __init__(self):
        self._pin = GPIO["relay_fan"]
        self._state = 0  # 0=关, 1=低, 2=中, 3=高
        self._line = None
        if _HAS_GPIO:
            # TODO: 初始化 gpiod line
            pass

    def control(self, action: str, **params):
        if action == "set":
            self._state = max(0, min(3, int(params.get("speed", 0))))
        elif action == "on":
            self._state = params.get("speed", 1)
        elif action == "off":
            self._state = 0
        self._apply()
        return f"风扇: {['关', '低', '中', '高'][self._state]}"

    def _apply(self):
        if self._line:
            self._line.set_value(1 if self._state > 0 else 0)

    def status(self) -> dict:
        return {"name": "fan", "state": ["off", "low", "mid", "high"][self._state]}
