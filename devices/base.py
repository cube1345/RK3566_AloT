# 设备驱动抽象基类
from abc import ABC, abstractmethod
from typing import Any


class BaseDevice(ABC):
    name: str = ""

    @abstractmethod
    def control(self, action: str, **params) -> Any:
        ...

    def status(self) -> dict:
        return {"name": self.name, "state": "unknown"}

    def cleanup(self):
        pass
