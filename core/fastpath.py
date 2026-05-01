# FastPath 规则引擎
# 负责: 阈值判断 + 自动控制 + 定时任务
# 延迟要求: <10ms

import asyncio
import logging
from typing import Any

from core.tool_registry import registry

logger = logging.getLogger("fastpath")


class FastPathEngine:
    """规则引擎: 条件-动作匹配器"""

    def __init__(self):
        self._sensor_cache: dict[str, Any] = {}
        self._device_state: dict[str, Any] = {}
        self._callbacks: dict[str, list] = {}  # sensor_name -> [rules]

    def update_sensor(self, name: str, value: Any):
        """传感器数据更新 → 触发规则评估"""
        old = self._sensor_cache.get(name)
        self._sensor_cache[name] = value
        self._evaluate(name, value, old)

    def get_sensor(self, name: str, default=None):
        return self._sensor_cache.get(name, default)

    def get_device_state(self, name: str, default=None):
        return self._device_state.get(name, default)

    def set_device_state(self, name: str, value: Any):
        self._device_state[name] = value

    def add_rule(self, sensor: str, callback=None, cooldown: float = 0):
        """注册规则: 支持直接调用和装饰器两种方式"""
        if callback is not None:
            self._callbacks.setdefault(sensor, []).append({
                "fn": callback,
                "cooldown": cooldown,
                "last_fired": 0,
            })
            return callback

        def decorator(fn):
            self._callbacks.setdefault(sensor, []).append({
                "fn": fn,
                "cooldown": cooldown,
                "last_fired": 0,
            })
            return fn
        return decorator

    def _evaluate(self, sensor: str, value: Any, old: Any):
        import time
        now = time.monotonic()
        for rule in self._callbacks.get(sensor, []):
            if now - rule["last_fired"] >= rule["cooldown"]:
                try:
                    rule["fn"](value, old, self)
                    rule["last_fired"] = now
                except Exception as e:
                    logger.error("规则执行失败: %s", e)

    def run_action(self, tool_name: str, **params):
        """快捷执行工具"""
        try:
            return registry.execute(tool_name, **params)
        except Exception as e:
            logger.error("工具调用失败 %s(%s): %s", tool_name, params, e)
            return None


# ===== 预置规则 =====

def setup_rules(fp: FastPathEngine):
    """注册全部 FastPath 规则"""

    @fp.add_rule("temperature", cooldown=30)
    def temp_rule(value, old, ctx):
        # 仅处理紧急情况: AI决策引擎处理日常舒适调节
        if value > 35:
            ctx.run_action("ac_control", mode="cool", temp=26, fan_speed="high")
            ctx.run_action("tts", text="⚠️ 温度过高！已开启紧急降温")
            logger.warning("FP 紧急: 酷热 %.1f → 空调全速", value)
        elif value < 10:
            ctx.run_action("ac_control", mode="heat", temp=22)
            ctx.run_action("tts", text="⚠️ 温度过低！已开启紧急升温")
            logger.warning("FP 紧急: 严寒 %.1f → 空调制热", value)

    @fp.add_rule("co2", cooldown=20)
    def co2_rule(value, old, ctx):
        # 仅处理紧急告警: AI决策引擎处理日常CO₂调节
        if value > 2000:
            ctx.run_action("set_air_purifier", level=3)
            ctx.run_action("set_fan", speed=3)
            ctx.run_action("tts", text="⚠️ CO₂浓度过高！请立即开窗通风！")
            logger.warning("FP 紧急: CO₂ %.0f → 全速通风", value)

    @fp.add_rule("light", cooldown=5)
    def light_rule(value, old, ctx):
        person = ctx.get_sensor("person_present", False)
        if not person:
            if ctx.get_device_state("light", "off") == "on":
                # 无人延时关灯由调度器处理
                pass
            return
        if value < 20:
            ctx.run_action("set_light", state="on", brightness=255)
        elif value < 50:
            ctx.run_action("set_light", state="on", brightness=100)
        elif value > 300 and ctx.get_device_state("light") == "on":
            ctx.run_action("set_light", state="off")
