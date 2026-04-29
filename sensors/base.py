# 传感器抽象基类
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SensorReading:
    value: float
    unit: str
    timestamp: float
    raw: dict = None


class BaseSensor(ABC):
    name: str = ""

    @abstractmethod
    def read(self) -> SensorReading:
        ...

    def cleanup(self):
        pass


class SensorManager:
    def __init__(self):
        self._sensors: dict[str, BaseSensor] = {}

    def register(self, sensor: BaseSensor):
        self._sensors[sensor.name] = sensor

    def read(self, name: str) -> SensorReading:
        return self._sensors[name].read()

    def read_all(self) -> dict[str, SensorReading]:
        return {n: s.read() for n, s in self._sensors.items()}

    def cleanup_all(self):
        for s in self._sensors.values():
            s.cleanup()

    def has(self, name: str) -> bool:
        return name in self._sensors
