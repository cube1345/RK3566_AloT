# SGP30 CO₂+TVOC 传感器 — I2C 驱动
import logging
import time

from sensors.base import BaseSensor, SensorReading

logger = logging.getLogger("sensors.sgp30")

try:
    import smbus2
    _HAS_SMBUS = True
except ImportError:
    _HAS_SMBUS = False

# SGP30 I2C 命令
_CMD_INIT_AIR_QUALITY = b"\x20\x03"
_CMD_MEAS_IAQ = b"\x20\x08"
_CMD_GET_BASELINE = b"\x20\x15"
_CMD_SET_BASELINE = b"\x20\x1e"
_CMD_SET_HUMIDITY = b"\x20\x61"
_CMD_GET_SERIAL = b"\x36\x82"
_CMD_MEAS_TEST = b"\x20\x32"  # 自检: 0x4d00=OK, 0x4d01=NG
_CMD_GET_FEATURESET = b"\x20\x2f"
_CMD_SOFT_RESET = b"\x00\x06"  # 通用 I2C 软复位

# CRC-8 (Sensirion 标准, 同 SHT30)
_CRC8_TABLE = None


def _crc8(data: bytes, init: int = 0xFF) -> int:
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


class SGP30Sensor(BaseSensor):
    name = "co2"

    def __init__(self, bus: int | None = None, addr: int = 0x58):
        self._bus_num = bus or 1
        self._addr = addr
        self._i2c: smbus2.SMBus | None = None
        self._initialized = False
        self._warmup_done = False
        self._last_co2 = 400.0
        self._last_tvoc = 0.0
        self._init_time = 0.0

    def read(self) -> SensorReading:
        bus = self._ensure_open()

        if not self._initialized:
            self._init_sensor(bus)

        # IAQ 测量
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_MEAS_IAQ)
        bus.i2c_rdwr(write_msg)
        time.sleep(0.012)  # 最大等待 12ms

        read_msg = smbus2.i2c_msg.read(self._addr, 6)
        bus.i2c_rdwr(read_msg)
        buf = bytes(read_msg)

        if len(buf) < 6:
            raise IOError(f"SGP30 响应不完整: {len(buf)}/6 bytes")

        co2_raw = (buf[0] << 8) | buf[1]
        if _crc8(buf[:2]) != buf[2]:
            logger.warning("SGP30 CO₂ CRC 校验失败，使用上次值")
            return SensorReading(value=self._last_co2, unit="ppm", timestamp=time.time(),
                                 raw={"tvoc": self._last_tvoc})

        tvoc_raw = (buf[3] << 8) | buf[4]
        if _crc8(buf[3:5]) != buf[5]:
            logger.warning("SGP30 TVOC CRC 校验失败")
            tvoc_raw = int(self._last_tvoc)

        # SGP30 在初始化后前 ~15s 输出固定值 400ppm/0ppb
        elapsed = time.time() - self._init_time
        if not self._warmup_done and elapsed > 15.0:
            self._warmup_done = True
            logger.info("SGP30 预热完成 (%.1fs)", elapsed)

        self._last_co2 = float(co2_raw)
        self._last_tvoc = float(tvoc_raw)

        return SensorReading(
            value=self._last_co2,
            unit="ppm",
            timestamp=time.time(),
            raw={"tvoc": self._last_tvoc},
        )

    def _init_sensor(self, bus):
        """发送初始化命令并触发预热"""
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_INIT_AIR_QUALITY)
        bus.i2c_rdwr(write_msg)
        time.sleep(0.01)
        self._initialized = True
        self._init_time = time.time()
        logger.info("SGP30 已初始化，预热 15s...")

    def get_baseline(self) -> tuple[int, int]:
        """读取基线值用于跨周期保存"""
        bus = self._ensure_open()
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_GET_BASELINE)
        bus.i2c_rdwr(write_msg)
        time.sleep(0.01)
        read_msg = smbus2.i2c_msg.read(self._addr, 6)
        bus.i2c_rdwr(read_msg)
        buf = bytes(read_msg)
        co2_base = (buf[0] << 8) | buf[1]
        tvoc_base = (buf[3] << 8) | buf[4]
        return co2_base, tvoc_base

    def set_baseline(self, co2_base: int, tvoc_base: int):
        """恢复之前保存的基线值"""
        bus = self._ensure_open()
        data = bytes([(co2_base >> 8) & 0xFF, co2_base & 0xFF])
        data += bytes([_crc8(data)])
        data += bytes([(tvoc_base >> 8) & 0xFF, tvoc_base & 0xFF])
        data += bytes([_crc8(data)])
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_SET_BASELINE + data)
        bus.i2c_rdwr(write_msg)
        time.sleep(0.01)

    def set_humidity(self, humidity: float):
        """设置环境湿度补偿 (m = humidity * 256 + 0.5)"""
        m = int(humidity * 256 + 0.5)
        data = bytes([(m >> 8) & 0xFF, m & 0xFF, _crc8(bytes([(m >> 8) & 0xFF, m & 0xFF]))])
        bus = self._ensure_open()
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_SET_HUMIDITY + data)
        bus.i2c_rdwr(write_msg)
        time.sleep(0.01)

    def run_self_test(self) -> bool:
        """执行自检, 返回 True=正常"""
        bus = self._ensure_open()
        write_msg = smbus2.i2c_msg.write(self._addr, _CMD_MEAS_TEST)
        bus.i2c_rdwr(write_msg)
        time.sleep(0.2)
        read_msg = smbus2.i2c_msg.read(self._addr, 3)
        bus.i2c_rdwr(read_msg)
        buf = bytes(read_msg)
        raw = (buf[0] << 8) | buf[1]
        return raw == 0x4d00

    def soft_reset(self):
        """通用 I2C 软复位 (影响总线上所有设备)"""
        try:
            bus = self._ensure_open()
            write_msg = smbus2.i2c_msg.write(self._addr, _CMD_SOFT_RESET)
            bus.i2c_rdwr(write_msg)
            time.sleep(0.01)
        except Exception as e:
            logger.debug("SGP30 软复位: %s", e)

    def _ensure_open(self):
        if self._i2c is not None:
            return self._i2c
        if not _HAS_SMBUS:
            raise RuntimeError("smbus2 未安装: pip install smbus2")
        try:
            self._i2c = smbus2.SMBus(self._bus_num)
            logger.info("SGP30 打开: I2C bus %d addr 0x%02x", self._bus_num, self._addr)
        except OSError as e:
            logger.error("SGP30 I2C 打开失败: %s", e)
            raise
        return self._i2c

    def cleanup(self):
        if self._i2c is not None:
            try:
                self._i2c.close()
                logger.info("SGP30 I2C 已关闭")
            except Exception:
                pass
