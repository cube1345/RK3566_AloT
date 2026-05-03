# 空气净化设备驱动 — GPIO 继电器
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

logger = logging.getLogger("devices.purifier")


class AirPurifierDevice(BaseDevice):
    name = "air_purifier"

    def __init__(self):
        self._pin = GPIO["relay_purifier"]
        self._level = 0
        self._req = None
        self._line = None
        if _HAS_GPIO:
            self._init_gpio()

    def _init_gpio(self):
        try:
            chip = gpiod.Chip(GPIO_CHIP)
            if _GPIOD_V2:
                self._req = chip.request_lines(
                    consumer="purifier",
                    config={self._pin: gpiod.LineSettings(
                        direction=Direction.OUTPUT,
                        output_value=Value.INACTIVE,
                    )},
                )
                logger.info("净化 GPIO (v2): %s pin %d", GPIO_CHIP, self._pin)
            else:
                self._line = chip.get_line(self._pin)
                self._line.request(consumer="purifier", type=gpiod.LINE_REQ_DIR_OUT)
                logger.info("净化 GPIO (v1): %s pin %d", GPIO_CHIP, self._pin)
        except Exception as e:
            logger.warning("净化 GPIO 初始化失败: %s", e)

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
        on = self._level > 0
        if RELAY_ACTIVE_LOW:
            on_val, off_val = Value.INACTIVE, Value.ACTIVE
        else:
            on_val, off_val = Value.ACTIVE, Value.INACTIVE
        if self._req:
            self._req.set_value(self._pin, on_val if on else off_val)
        elif self._line:
            self._line.set_value(on_val.value if on else off_val.value)

    def status(self) -> dict:
        return {"name": "air_purifier", "level": self._level}
