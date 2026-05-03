# 空调驱动 — IR 红外 (LED 模拟)
import logging

from devices.base import BaseDevice
from config import GPIO, GPIO_CHIP

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

logger = logging.getLogger("devices.ac")


class ACDevice(BaseDevice):
    name = "ac"

    def __init__(self):
        self._pin = GPIO["ir_led"]
        self._mode = "cool"
        self._temp = 26
        self._power = False
        self._req = None
        self._line = None
        if _HAS_GPIO:
            self._init_gpio()

    def _init_gpio(self):
        try:
            chip = gpiod.Chip(GPIO_CHIP)
            if _GPIOD_V2:
                self._req = chip.request_lines(
                    consumer="ac_ir",
                    config={self._pin: gpiod.LineSettings(
                        direction=Direction.OUTPUT,
                        output_value=Value.INACTIVE,
                    )},
                )
                logger.info("AC IR GPIO (v2): %s pin %d", GPIO_CHIP, self._pin)
            else:
                self._line = chip.get_line(self._pin)
                self._line.request(consumer="ac_ir", type=gpiod.LINE_REQ_DIR_OUT)
                logger.info("AC IR GPIO (v1): %s pin %d", GPIO_CHIP, self._pin)
        except Exception as e:
            logger.warning("AC IR GPIO 初始化失败: %s", e)

    def control(self, action: str, **params):
        if action == "off":
            self._power = False
        elif action in ("cool", "heat", "dry", "auto"):
            self._power = True
            self._mode = action
            self._temp = int(params.get("temp", 26))
        self._send_ir()
        return f"空调: {self._mode} {self._temp}°C {'开' if self._power else '关'}"

    def _set_pin(self, val: bool):
        if self._req:
            self._req.set_value(self._pin, Value.ACTIVE if val else Value.INACTIVE)
        elif self._line:
            self._line.set_value(1 if val else 0)

    def _send_ir(self):
        """IR LED 闪烁模拟 — 收到指令后闪 3 次表示发送"""
        if not self._req and not self._line:
            return
        import time as _t
        for _ in range(3):
            self._set_pin(True)
            _t.sleep(0.15)
            self._set_pin(False)
            _t.sleep(0.15)

    def status(self) -> dict:
        return {"name": "ac", "power": self._power, "mode": self._mode, "temp": self._temp}

    def cleanup(self):
        self._req = None
        self._line = None
