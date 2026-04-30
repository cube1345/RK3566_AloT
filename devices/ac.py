# 空调驱动 — IR 红外
import logging

from devices.base import BaseDevice
from config import GPIO, GPIO_CHIP

try:
    import gpiod
    _HAS_GPIO = True
    _GPIOD_V2 = hasattr(gpiod, "LineSettings")
except ImportError:
    _HAS_GPIO = False
    _GPIOD_V2 = False

logger = logging.getLogger("devices.ac")


class ACDevice(BaseDevice):
    name = "ac"

    # NEC 红外协议基础码 (需根据实际空调型号替换)
    _CODES = {
        ("cool", 26, "auto"): 0x88C005A,
        ("cool", 24, "high"): 0x88C006A,
        ("heat", 22, "auto"): 0x88C007A,
        ("dry", 26, "low"):   0x88C008A,
        "off":                 0x88C009A,
    }

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
                        direction=gpiod.Direction.OUTPUT,
                        output_value=gpiod.Value.INACTIVE,
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
        if action == "power":
            self._power = not self._power
        elif action == "off":
            self._power = False
        elif action in ("cool", "heat", "dry", "auto"):
            self._power = True
            self._mode = action
            self._temp = int(params.get("temp", 26))
        self._send_ir()
        return f"空调: {self._mode} {self._temp}°C {'开' if self._power else '关'}"

    def _set_pin(self, val: bool):
        if self._req:
            self._req.set_value(self._pin, gpiod.Value.ACTIVE if val else gpiod.Value.INACTIVE)
        elif self._line:
            self._line.set_value(1 if val else 0)

    def _send_ir(self):
        """模拟 NEC 红外协议发送序列 (用于演示 LED 闪烁)"""
        if not self._req and not self._line:
            return
        import time as _t

        code = self._get_code()
        bits = [(code >> i) & 1 for i in range(32)]

        # Leader: 9ms ON + 4.5ms OFF
        self._set_pin(True)
        _t.sleep(0.009)
        self._set_pin(False)
        _t.sleep(0.0045)

        # 32-bit data
        for bit in bits:
            self._set_pin(True)
            _t.sleep(0.00056)  # 560µs carrier
            self._set_pin(False)
            _t.sleep(0.00169 if bit else 0.00056)  # 1: 1690µs, 0: 560µs

        # 结束脉冲
        self._set_pin(True)
        _t.sleep(0.00056)
        self._set_pin(False)

    def _get_code(self) -> int:
        if not self._power:
            return self._CODES.get("off", 0x88C009A)
        return self._CODES.get((self._mode, self._temp, "auto"), 0x88C005A)

    def status(self) -> dict:
        return {"name": "ac", "power": self._power, "mode": self._mode, "temp": self._temp}

    def cleanup(self):
        self._req = None
        self._line = None
