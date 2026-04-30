# DHT11 温湿度传感器 — GPIO 单总线驱动
#
# 时序协议:
#   Host:   LOW 18ms → HIGH 20-40µs
#   DHT11:  LOW 80µs → HIGH 80µs (响应)
#   40 bits: 每个 bit = LOW 50µs + HIGH 26-28µs(0) / 70µs(1)
#   Data: 16bit 湿度 + 16bit 温度 + 8bit 校验和
#
# 注意:
#   - DHT11 最大读取频率 1Hz, 两次读取间隔至少 1s
#   - GPIO 时序依赖 Python 忙等待, 重负载下可能丢数据
#   - 读取失败时返回上次缓存值 + 日志警告
import logging
import time

from sensors.base import BaseSensor, SensorReading

logger = logging.getLogger("sensors.dht11")

try:
    import gpiod
    _HAS_GPIOD = True
    _GPIOD_V2 = hasattr(gpiod, "LineSettings")
except ImportError:
    _HAS_GPIOD = False
    _GPIOD_V2 = False

_READ_INTERVAL = 1.5  # DHT11 两次读取最小间隔
_BITS = 40            # 数据位: 8湿度整 + 8湿度小 + 8温度整 + 8温度小 + 8校验


class DHT11Sensor(BaseSensor):
    name = "temperature"

    def __init__(self, pin: int | None = None, chip: str | None = None):
        from config import GPIO, GPIO_CHIP
        self._pin = pin or GPIO.get("dht11", 4)  # 默认 BCM4 (物理 Pin7)
        self._chip_path = chip or GPIO_CHIP
        self._last_temp = 25.0
        self._last_humidity = 50.0
        self._last_read_time = 0.0

    def read(self) -> SensorReading:
        now = time.time()
        if now - self._last_read_time < _READ_INTERVAL:
            return SensorReading(
                value=self._last_temp, unit="°C", timestamp=now,
                raw={"humidity": self._last_humidity},
            )

        temp, humidity = self._read_dht11()
        if temp is not None:
            self._last_temp = temp
            self._last_humidity = humidity
        else:
            logger.warning("DHT11 读取失败, 使用缓存值: %.1f°C / %.1f%%",
                           self._last_temp, self._last_humidity)

        self._last_read_time = time.time()
        return SensorReading(
            value=self._last_temp, unit="°C", timestamp=time.time(),
            raw={"humidity": self._last_humidity},
        )

    def _read_dht11(self) -> tuple[float | None, float | None]:
        if not _HAS_GPIOD:
            logger.warning("DHT11 需要 gpiod 库")
            return None, None

        chip = gpiod.Chip(self._chip_path)
        try:
            # === Phase 1: Output — 发送起始信号 ===
            out_cfg = {
                self._pin: gpiod.LineSettings(
                    direction=gpiod.Direction.OUTPUT,
                    output_value=gpiod.Value.ACTIVE,
                )
            }
            out_req = chip.request_lines(consumer="dht11_out", config=out_cfg)

            # LOW 20ms (DHT11 要求 >18ms)
            out_req.set_value(self._pin, gpiod.Value.INACTIVE)
            time.sleep(0.020)

            # HIGH 30µs
            out_req.set_value(self._pin, gpiod.Value.ACTIVE)
            self._busy_wait_ns(30000)

            # 释放输出线
            del out_req

            # === Phase 2: Input — 读取 DHT11 响应和数据 ===
            in_cfg = {
                self._pin: gpiod.LineSettings(direction=gpiod.Direction.INPUT)
            }
            in_req = chip.request_lines(consumer="dht11_in", config=in_cfg)

            # 等待 DHT11 拉低 (响应起始)
            timeout_ns = 2000000  # 2ms
            if not self._wait_for(in_req, gpiod.Value.INACTIVE, timeout_ns):
                logger.warning("DHT11 无响应 (超时等待 LOW)")
                return None, None

            if not self._wait_for(in_req, gpiod.Value.ACTIVE, timeout_ns):
                logger.warning("DHT11 响应中断 (超时等待 HIGH)")
                return None, None

            if not self._wait_for(in_req, gpiod.Value.INACTIVE, timeout_ns):
                logger.warning("DHT11 响应中断 (超时等待 LOW 2)")
                return None, None

            # 读取 40 个数据位
            bits = []
            for i in range(_BITS):
                # 等待 HIGH (bit 起始)
                if not self._wait_for(in_req, gpiod.Value.ACTIVE, 500000):
                    logger.warning("DHT11 bit %d 超时", i)
                    return None, None
                # 测量 HIGH 脉冲宽度
                start = time.perf_counter_ns()
                while in_req.get_value(self._pin) == gpiod.Value.ACTIVE:
                    if time.perf_counter_ns() - start > 200000:
                        break
                duration = time.perf_counter_ns() - start
                # bit 0: ~26-28µs, bit 1: ~70µs → 阈值 50µs
                bits.append(1 if duration > 50000 else 0)

            in_req = None  # 释放输入请求

        except Exception as e:
            logger.error("DHT11 GPIO 错误: %s", e)
            return None, None
        finally:
            try:
                chip.close()
            except Exception:
                pass

        if len(bits) != 40:
            return None, None

        return self._parse_bits(bits)

    def _wait_for(self, req, target_value, timeout_ns: int) -> bool:
        """忙等待指定电平，返回是否超时"""
        start = time.perf_counter_ns()
        while req.get_value(self._pin) != target_value:
            if time.perf_counter_ns() - start > timeout_ns:
                return False
        return True

    def _busy_wait_ns(self, ns: int):
        start = time.perf_counter_ns()
        while time.perf_counter_ns() - start < ns:
            pass

    def _parse_bits(self, bits: list[int]) -> tuple[float, float]:
        hum_int = self._bits_to_byte(bits[0:8])
        hum_dec = self._bits_to_byte(bits[8:16])
        temp_int = self._bits_to_byte(bits[16:24])
        temp_dec = self._bits_to_byte(bits[24:32])
        checksum = self._bits_to_byte(bits[32:40])

        calc = (hum_int + hum_dec + temp_int + temp_dec) & 0xFF
        if calc != checksum:
            logger.warning("DHT11 校验和失败: calc=0x%02x, got=0x%02x", calc, checksum)
            return None, None

        temp = temp_int + temp_dec / 10.0
        humidity = hum_int + hum_dec / 10.0

        # DHT11 温度范围 0-50°C, 湿度 20-90%
        if temp < -10 or temp > 60:
            logger.warning("DHT11 温度越界: %.1f", temp)
            return None, None
        if humidity < 5 or humidity > 100:
            logger.warning("DHT11 湿度越界: %.1f", humidity)
            return None, None

        return round(temp, 1), round(humidity, 1)

    @staticmethod
    def _bits_to_byte(bits: list[int]) -> int:
        val = 0
        for b in bits:
            val = (val << 1) | b
        return val

    def cleanup(self):
        pass
