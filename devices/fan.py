# 风扇驱动 — GPIO 继电器
import logging

from devices.base import BaseDevice
from config import GPIO, GPIO_CHIP, RELAY_ACTIVE_LOW

try:
    import gpiod
    _HAS_GPIO = True
    _GPIOD_V2 = hasattr(gpiod, "LineSettings")
    if _GPIOD_V2:
        from gpiod.line import Direction, Value
    else:
        Direction = None
        from enum import IntEnum

        class _Value(IntEnum):
            ACTIVE = 1
            INACTIVE = 0
        Value = _Value
except ImportError:
    _HAS_GPIO = False
    _GPIOD_V2 = False

logger = logging.getLogger("devices.fan")


class FanDevice(BaseDevice):
    name = "fan"

    def __init__(self):
        self._pin = GPIO["relay_fan"]
        self._state = 0
        self._req = None
        self._line = None
        if _HAS_GPIO:
            self._init_gpio()

    def _init_gpio(self):
        try:
            chip = gpiod.Chip(GPIO_CHIP)
            if _GPIOD_V2:
                self._req = chip.request_lines(
                    consumer="fan",
                    config={self._pin: gpiod.LineSettings(
                        direction=Direction.OUTPUT,
                        output_value=Value.INACTIVE,
                    )},
                )
                logger.info("风扇 GPIO (v2): %s pin %d", GPIO_CHIP, self._pin)
            else:
                self._line = chip.get_line(self._pin)
                self._line.request(consumer="fan", type=gpiod.LINE_REQ_DIR_OUT)
                logger.info("风扇 GPIO (v1): %s pin %d", GPIO_CHIP, self._pin)
        except Exception as e:
            logger.warning("风扇 GPIO 初始化失败: %s", e)

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
        on = self._state > 0
        if RELAY_ACTIVE_LOW:
            on_val, off_val = Value.INACTIVE, Value.ACTIVE
        else:
            on_val, off_val = Value.ACTIVE, Value.INACTIVE
        if self._req:
            self._req.set_value(self._pin, on_val if on else off_val)
        elif self._line:
            self._line.set_value(on_val.value if on else off_val.value)

    def status(self) -> dict:
        return {"name": "fan", "state": ["off", "low", "mid", "high"][self._state]}
