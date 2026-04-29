# 空调驱动 — IR 红外
from devices.base import BaseDevice
from config import GPIO

try:
    import gpiod
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False


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

    def _send_ir(self):
        """IR 脉冲发送 (占位, 需根据具体 IR LED 驱动实现)"""
        if _HAS_GPIO:
            # NEC 协议: 9ms carrier + 4.5ms space + data bits
            pass

    def status(self) -> dict:
        return {"name": "ac", "power": self._power, "mode": self._mode, "temp": self._temp}
