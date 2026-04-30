# SHT30 温湿度传感器 — I2C 驱动
import logging
import struct
import time

from sensors.base import BaseSensor, SensorReading

logger = logging.getLogger("sensors.temp_humid")

try:
    import smbus2

    _HAS_SMBUS = True
except ImportError:
    _HAS_SMBUS = False

# SHT30 常用命令
_CMD_MEAS_HIGH_REP = b"\x2c\x06"  # 高重复性, 时钟延展关闭
_CMD_MEAS_MED_REP = b"\x2c\x0d"
_CMD_MEAS_LOW_REP = b"\x2c\x10"
_CMD_READ_SERIAL = b"\x37\x80"  # 读取序列号
_CMD_SOFT_RESET = b"\x30\xa2"
_CMD_HEATER_ON = b"\x30\x6d"
_CMD_HEATER_OFF = b"\x30\x66"

# CRC-8 多项式: x^8 + x^5 + x^4 + 1 (0x31)
_CRC8_TABLE = None


def _crc8(data: bytes, init: int = 0xFF) -> int:
    """Sensirion CRC-8: poly 0x31, init 0xFF"""
    global _CRC8_TABLE
    if _CRC8_TABLE is None:
        _CRC8_TABLE = []
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc << 1) ^ 0x31 if crc & 0x80 else crc << 1
                crc &= 0xFF
            _CRC8_TABLE.append(crc)
    crc = init
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return crc


def _raw_to_temp(raw: int) -> float:
    """ST = raw / 65535, T = -45 + 175 * ST"""
    return -45.0 + 175.0 * raw / 65535.0


def _raw_to_humidity(raw: int) -> float:
    """ST = raw / 65535, RH = 100 * ST"""
    return 100.0 * raw / 65535.0


class TempHumidSensor(BaseSensor):
    name = "temperature"

    def __init__(self, bus: int | None = None, addr: int = 0x44):
        self._bus_num = bus or 1  # RK3566 I2C1 (物理引脚 3/5)
        self._addr = addr
        self._i2c: smbus2.SMBus | None = None
        self._last_humidity = 50.0  # 读失败时的回退值

    def read(self) -> SensorReading:
        bus = self._ensure_open()
        # 发送测量命令 (raw I2C write, smbus2.write_i2c_block_data 把首字节当寄存器)
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_MEAS_HIGH_REP)
        bus.i2c_rdwr(write_msg)
        # SHT30 高重复性测量耗时 ~15ms
        time.sleep(0.02)
        # 读取 6 bytes (raw I2C read, 不发送寄存器地址)
        read_msg = smbus2.i2c_msg.read(self._addr, 6)
        bus.i2c_rdwr(read_msg)
        buf = bytes(read_msg)
        if len(buf) < 6:
            raise IOError(f"SHT30 响应不完整: {len(buf)}/6 bytes")

        temp_raw = (buf[0] << 8) | buf[1]
        if _crc8(buf[:2]) != buf[2]:
            logger.warning("SHT30 温度 CRC 校验失败")

        hum_raw = (buf[3] << 8) | buf[4]
        if _crc8(buf[3:5]) != buf[5]:
            logger.warning("SHT30 湿度 CRC 校验失败")

        temp = round(_raw_to_temp(temp_raw), 1)
        temp = max(-20, min(60, temp))
        humidity = round(_raw_to_humidity(hum_raw), 1)
        humidity = max(0, min(100, humidity))
        self._last_humidity = humidity

        return SensorReading(
            value=temp,
            unit="°C",
            timestamp=time.time(),
            raw={"humidity": humidity},
        )

    def soft_reset(self):
        bus = self._ensure_open()
        bus.write_bytes(self._addr, _CMD_SOFT_RESET)
        time.sleep(0.01)

    def set_heater(self, on: bool):
        bus = self._ensure_open()
        bus.write_bytes(self._addr, _CMD_HEATER_ON if on else _CMD_HEATER_OFF)
        time.sleep(0.01)

    def read_serial(self) -> int:
        """读取 SHT30 序列号 (用于校验连接)"""
        bus = self._ensure_open()
        bus.write_bytes(self._addr, _CMD_READ_SERIAL)
        time.sleep(0.01)
        buf = bus.read_bytes(self._addr, 6)
        return (buf[0] << 24) | (buf[1] << 16) | (buf[3] << 8) | buf[4]

    def _ensure_open(self) -> smbus2.SMBus:
        if self._i2c is not None:
            return self._i2c
        if not _HAS_SMBUS:
            raise RuntimeError("smbus2 未安装: pip install smbus2")
        try:
            self._i2c = smbus2.SMBus(self._bus_num)
            logger.info("SHT30 打开: I2C bus %d addr 0x%02x", self._bus_num, self._addr)
        except OSError as e:
            logger.error("SHT30 I2C 打开失败: %s, 回退模拟值", e)
            raise
        return self._i2c

    def cleanup(self):
        if self._i2c is not None:
            try:
                self._i2c.close()
                logger.info("SHT30 I2C 已关闭")
            except Exception:
                pass
