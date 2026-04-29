# Mock 传感器 — 用于开发环境，模拟真实传感器行为
import math
import time
import random

from sensors.base import BaseSensor, SensorReading


class MockCO2(BaseSensor):
    name = "co2"

    def __init__(self):
        self._base = 450
        self._target = 450
        self._t = 0

    def read(self) -> SensorReading:
        self._t += 1
        # 缓慢漂移 + 随机噪声
        if self._t % 20 == 0:
            self._target = random.choice([400, 450, 600, 800, 1200])
        self._base += (self._target - self._base) * 0.1
        noise = random.gauss(0, 20)
        val = max(400, self._base + noise)
        return SensorReading(value=val, unit="ppm", timestamp=time.time())


class MockTempHumid(BaseSensor):
    name = "temperature"
    _temp = 26.0
    _humid = 55.0

    def read(self) -> SensorReading:
        self._temp += random.uniform(-0.3, 0.3)
        self._temp = max(15, min(38, self._temp))
        return SensorReading(value=round(self._temp, 1), unit="°C", timestamp=time.time(),
                             raw={"humidity": self._read_humidity()})

    def _read_humidity(self) -> float:
        self._humid += random.uniform(-1, 1)
        self._humid = max(20, min(90, self._humid))
        return round(self._humid, 1)


class MockLight(BaseSensor):
    name = "light"
    _lux = 300

    def read(self) -> SensorReading:
        self._lux += random.uniform(-20, 20)
        self._lux = max(0, min(1000, self._lux))
        return SensorReading(value=round(self._lux, 1), unit="lux", timestamp=time.time())


class MockMotion(BaseSensor):
    name = "motion"
    _present = True

    def read(self) -> SensorReading:
        if random.random() < 0.02:
            self._present = not self._present
        return SensorReading(value=float(self._present), unit="bool", timestamp=time.time())
