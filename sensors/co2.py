# MH-Z19B CO₂ 传感器 — UART 驱动
import logging
import time

import serial

from config import UART_DEV, UART_BAUD
from sensors.base import BaseSensor, SensorReading

logger = logging.getLogger("sensors.co2")

# MH-Z19B 指令
_CMD_READ_CO2 = b"\xff\x01\x86\x00\x00\x00\x00\x00\x79"
_CMD_AUTO_CALIB_ON = b"\xff\x01\x79\xa0\x00\x00\x00\x00\xe6"
_CMD_AUTO_CALIB_OFF = b"\xff\x01\x79\x00\x00\x00\x00\x00\x86"
_CMD_SET_RANGE = b"\xff\x01\x99\x00\x00\x00\x03\x00\x63"  # 2000ppm
_CMD_CALIB_ZERO = b"\xff\x01\x87\x00\x00\x00\x00\x00\x78"
_CMD_CALIB_SPAN = b"\xff\x01\x88\x00\x00\x00\x00\x00\x77"  # 2000ppm span


def _checksum(data: bytes) -> int:
    """MH-Z19B checksum: sum of bytes 1-7, bit-inverted + 1 (i.e. 0xFF - sum & 0xFF + 1)"""
    s = sum(data[1:8]) & 0xFF
    return (0xFF - s + 1) & 0xFF


class CO2Sensor(BaseSensor):
    name = "co2"

    def __init__(self, device: str | None = None, baud: int = UART_BAUD):
        self._device = device or UART_DEV
        self._baud = baud
        self._ser: serial.Serial | None = None

    def read(self) -> SensorReading:
        ser = self._ensure_open()
        ser.write(_CMD_READ_CO2)
        # 等待响应: 9 bytes @ 9600 8N1 ≈ 9.4ms
        time.sleep(0.01)
        buf = ser.read(9)
        if len(buf) < 9:
            raise IOError(f"MH-Z19B 响应不完整: {len(buf)}/9 bytes")
        if buf[0] != 0xFF or buf[1] != 0x86:
            raise IOError(f"MH-Z19B 帧头错误: {buf.hex()}")

        calc_cs = _checksum(buf)
        if buf[8] != calc_cs:
            raise IOError(f"MH-Z19B 校验和错误: got {buf[8]:02x}, expected {calc_cs:02x}")

        co2 = (buf[2] << 8) | buf[3]
        # buf[4] = 温度 (未校准, 不建议使用)
        # buf[5] = 状态
        # buf[6], buf[7] = 保留
        return SensorReading(value=float(co2), unit="ppm", timestamp=time.time())

    def enable_auto_calibration(self, enable: bool = True):
        ser = self._ensure_open()
        ser.write(_CMD_AUTO_CALIB_ON if enable else _CMD_AUTO_CALIB_OFF)
        time.sleep(0.01)

    def calibrate_zero(self):
        """在 400ppm 新鲜空气环境中校准零点"""
        ser = self._ensure_open()
        ser.write(_CMD_CALIB_ZERO)
        time.sleep(0.01)

    def calibrate_span(self, ppm: int = 2000):
        """在已知浓度标准气体中标定量程"""
        ser = self._ensure_open()
        ser.write(_CMD_SET_RANGE)
        time.sleep(0.01)
        ser.write(_CMD_CALIB_SPAN)
        time.sleep(0.01)

    def _ensure_open(self) -> serial.Serial:
        if self._ser and self._ser.is_open:
            return self._ser
        try:
            self._ser = serial.Serial(
                port=self._device,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5,
            )
            logger.info("MH-Z19B 打开: %s @ %d", self._device, self._baud)
        except serial.SerialException as e:
            logger.error("MH-Z19B 打开失败: %s, 回退模拟值", e)
            self._ser = None
            raise
        return self._ser

    def cleanup(self):
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
                logger.info("MH-Z19B 已关闭")
            except Exception:
                pass
