# BH1750 光照传感器 — I2C 驱动
import logging
import time

from sensors.base import BaseSensor, SensorReading

logger = logging.getLogger("sensors.light")

try:
    import smbus2

    _HAS_SMBUS = True
except ImportError:
    _HAS_SMBUS = False

# BH1750 命令
_CMD_POWER_ON = 0x01
_CMD_POWER_OFF = 0x00
_CMD_RESET = 0x07

# 测量模式 (ADDR=L: 0x23, ADDR=H: 0x5C)
# 连续模式 — 推荐
_CMD_CONT_H_RES = 0x10  # 高分辨率 (1 lux), 测量 120ms
_CMD_CONT_H_RES2 = 0x11  # 高分辨率 2 (0.5 lux), 测量 120ms
_CMD_CONT_L_RES = 0x13  # 低分辨率 (4 lux), 测量 16ms

# 单次模式 — 省电
_CMD_ONCE_H_RES = 0x20
_CMD_ONCE_H_RES2 = 0x21
_CMD_ONCE_L_RES = 0x23

# 测量时间寄存器基值 (在 set_measurement_time 中 OR 低位)
_CMD_MT_HIGH = 0x40  # MT[7:5]
_CMD_MT_LOW = 0x60   # MT[4:0]

# 暗环境 (±1 lux) 建议 MT=32, 亮环境 (>10000 lux) 建议 MT=200
_DEFAULT_MT = 69


class LightSensor(BaseSensor):
    name = "light"

    def __init__(self, bus: int | None = None, addr: int = 0x23, mt: int = _DEFAULT_MT):
        self._bus_num = bus or 1  # RK3566 I2C1
        self._addr = addr
        self._mt = mt
        self._i2c: smbus2.SMBus | None = None

    def read(self) -> SensorReading:
        bus = self._ensure_open()
        # 写测量命令 (write_byte 单字节, 无寄存器地址 — 正确)
        bus.write_byte(self._addr, _CMD_CONT_H_RES)
        # BH1750 高分辨率模式典型测量时间 120ms
        time.sleep(0.15)
        # 读 2 bytes (raw I2C read, 不发送寄存器地址)
        read_msg = smbus2.i2c_msg.read(self._addr, 2)
        bus.i2c_rdwr(read_msg)
        buf = list(read_msg)
        if len(buf) < 2:
            raise IOError(f"BH1750 响应不完整: {len(buf)}/2 bytes")

        raw = (buf[0] << 8) | buf[1]
        lux = raw / 1.2
        lux = max(0.0, lux)
        return SensorReading(value=round(lux, 1), unit="lux", timestamp=time.time())

    def set_measurement_time(self, mt: int):
        """设置测量时间寄存器 (31~254), 影响灵敏度和范围"""
        mt = max(31, min(254, mt))
        self._mt = mt
        bus = self._ensure_open()
        bus.write_byte(self._addr, 0x40 | (mt >> 5))
        bus.write_byte(self._addr, 0x60 | (mt & 0x1F))
        time.sleep(0.01)

    def reset(self):
        bus = self._ensure_open()
        bus.write_byte(self._addr, _CMD_RESET)
        time.sleep(0.01)

    def power_off(self):
        if self._i2c is not None:
            self._i2c.write_byte(self._addr, _CMD_POWER_OFF)

    def _ensure_open(self):
        if self._i2c is not None:
            return self._i2c
        if not _HAS_SMBUS:
            raise RuntimeError("smbus2 未安装: pip install smbus2")
        try:
            self._i2c = smbus2.SMBus(self._bus_num)
            logger.info("BH1750 打开: I2C bus %d addr 0x%02x", self._bus_num, self._addr)
            # 上电 + 复位
            self._i2c.write_byte(self._addr, _CMD_POWER_ON)
            time.sleep(0.01)
            self._i2c.write_byte(self._addr, _CMD_RESET)
            time.sleep(0.01)
            # 设置测量时间
            self.set_measurement_time(self._mt)
        except OSError as e:
            logger.error("BH1750 I2C 打开失败: %s, 回退模拟值", e)
            raise
        return self._i2c

    def cleanup(self):
        if self._i2c is not None:
            try:
                self.power_off()
                self._i2c.close()
                logger.info("BH1750 I2C 已关闭")
            except Exception:
                pass
