# HC-SR501 人体红外传感器 — GPIO 驱动
import logging
import os
import time

from config import GPIO, GPIO_CHIP as _CONFIG_GPIO_CHIP
from sensors.base import BaseSensor, SensorReading

logger = logging.getLogger("sensors.motion")

try:
    import gpiod

    _HAS_GPIOD = True
    _GPIOD_V2 = hasattr(gpiod, "LineSettings")
    if _GPIOD_V2:
        from gpiod.line import Direction, Value
except ImportError:
    _HAS_GPIOD = False
    _GPIOD_V2 = False

_DEBOUNCE_S = 0.3  # 消抖时间 (HC-SR501 输出脉冲约 2-3 秒, 不需要太短)


class MotionSensor(BaseSensor):
    name = "motion"

    def __init__(self, pin: int | None = None, chip: str | None = None):
        self._pin = pin or GPIO["motion_sensor"]
        self._gpio_chip = chip or _CONFIG_GPIO_CHIP
        self._prev_value = 0
        self._last_read = 0.0
        # gpiod 资源
        self._request = None  # v2
        self._line = None  # v1
        # sysfs
        self._sysfs_path = None
        self._sysfs_exported = False

    def read(self) -> SensorReading:
        now = time.time()
        # HC-SR501 输出保持 ~2-3s, 缓存消抖
        if now - self._last_read < _DEBOUNCE_S:
            return SensorReading(
                value=float(self._prev_value),
                unit="bool",
                timestamp=now,
            )
        self._last_read = now

        val = self._read_gpio()
        if val is None:
            logger.warning("Motion GPIO 读取失败, 恢复 last=%d", self._prev_value)
            val = self._prev_value
        self._prev_value = val
        return SensorReading(value=float(val), unit="bool", timestamp=time.time())

    def _read_gpio(self) -> int | None:
        """尝试多种方式读取 GPIO, 返回 0 或 1, 失败返回 None"""
        # 1. gpiod v2
        if _GPIOD_V2 and self._request is None:
            self._init_gpiod_v2()
        if self._request:
            try:
                val = self._request.get_value(self._pin)
                return 1 if val == Value.ACTIVE else 0
            except Exception as e:
                logger.debug("gpiod v2 读取失败: %s", e)
                self._request = None

        # 2. gpiod v1
        if _HAS_GPIOD and not _GPIOD_V2 and self._line is None:
            self._init_gpiod_v1()
        if self._line:
            try:
                return self._line.get_value()
            except Exception as e:
                logger.debug("gpiod v1 读取失败: %s", e)
                self._line = None

        # 3. sysfs fallback
        return self._read_sysfs()

    def _init_gpiod_v2(self):
        try:
            chip = gpiod.Chip(self._gpio_chip)
            self._request = chip.request_lines(
                consumer="motion_sensor",
                config={self._pin: gpiod.LineSettings(direction=Direction.INPUT)},
            )
            logger.info("Motion GPIO (gpiod v2): %s pin %d", self._gpio_chip, self._pin)
        except Exception as e:
            logger.debug("gpiod v2 初始化失败: %s", e)

    def _init_gpiod_v1(self):
        try:
            chip = gpiod.Chip(self._gpio_chip)
            self._line = chip.get_line(self._pin)
            self._line.request(consumer="motion_sensor", type=gpiod.LINE_REQ_DIR_IN)
            logger.info("Motion GPIO (gpiod v1): %s pin %d", self._gpio_chip, self._pin)
        except Exception as e:
            logger.debug("gpiod v1 初始化失败: %s", e)

    def _read_sysfs(self) -> int | None:
        """sysfs GPIO 读取 (无额外依赖)"""
        if not self._sysfs_exported:
            self._export_sysfs()
        if not self._sysfs_path:
            return None
        try:
            with open(self._sysfs_path, "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError) as e:
            logger.debug("sysfs GPIO 读取失败: %s", e)
            return None

    def _export_sysfs(self):
        gpio_dir = f"/sys/class/gpio/gpio{self._pin}"
        if os.path.exists(gpio_dir):
            self._sysfs_path = f"{gpio_dir}/value"
            self._sysfs_exported = True
            return
        # 导出
        try:
            with open("/sys/class/gpio/export", "w") as f:
                f.write(str(self._pin))
            # 等待 udev 创建设备节点
            for _ in range(10):
                if os.path.exists(gpio_dir):
                    self._sysfs_path = f"{gpio_dir}/value"
                    self._sysfs_exported = True
                    # 设置为输入
                    with open(f"{gpio_dir}/direction", "w") as d:
                        d.write("in")
                    logger.info("Motion GPIO (sysfs): pin %d", self._pin)
                    return
                time.sleep(0.05)
        except OSError as e:
            logger.debug("sysfs GPIO 导出失败: %s", e)

    def cleanup(self):
        self._request = None
        self._line = None
        if self._sysfs_exported:
            try:
                with open("/sys/class/gpio/unexport", "w") as f:
                    f.write(str(self._pin))
                self._sysfs_exported = False
                logger.info("Motion GPIO sysfs 已释放")
            except Exception:
                pass
