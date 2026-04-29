# 设备管理器 — 统一控制入口
import logging
from typing import Any
from devices.base import BaseDevice
from config import GPIO_AVAILABLE

logger = logging.getLogger("devices")


class DeviceManager:
    def __init__(self):
        self._devices: dict[str, BaseDevice] = {}
        self._mock = not GPIO_AVAILABLE

    def register(self, device: BaseDevice):
        self._devices[device.name] = device
        logger.info("设备注册: %s (mock=%s)", device.name, self._mock)

    def get(self, name: str) -> BaseDevice:
        return self._devices.get(name)

    def control(self, name: str, action: str, **params) -> Any:
        dev = self._devices.get(name)
        if not dev:
            logger.warning("设备 '%s' 未注册", name)
            return None
        if self._mock:
            logger.info("[MOCK] %s.%s(%s)", name, action, params)
            return f"{name}: {action} {params}"
        return dev.control(action, **params)

    def status_all(self) -> dict:
        return {n: d.status() for n, d in self._devices.items()}

    def cleanup_all(self):
        for d in self._devices.values():
            d.cleanup()

    @property
    def is_mock(self) -> bool:
        return self._mock
