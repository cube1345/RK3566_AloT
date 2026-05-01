# 场景识别引擎 (v5.1) — 关键词匹配+自动触发
import time
from config import SCENE_TRIGGERS


class SceneEngine:
    """预定义场景识别：关键词匹配 + 时间自动触发"""

    def __init__(self, llm=None):
        self._llm = llm
        self._last_auto: dict[str, float] = {}  # scene_id → last trigger timestamp

    def recognize(self, text: str) -> dict | None:
        """关键词匹配 → 返回 scene 定义；未命中 → None"""
        text_lower = text.lower()
        for scene_id, cfg in SCENE_TRIGGERS.items():
            for kw in cfg["keywords"]:
                if kw in text_lower:
                    return {
                        "scene_id": scene_id,
                        "name": cfg["name"],
                        "tools": cfg["tools"],
                        "reply": cfg["reply"],
                    }
        return None

    def check_auto_trigger(self, hour: int, motion_active: bool,
                            light_on: bool) -> dict | None:
        """基于时间的自动触发：深夜无人无灯 → 睡觉场景"""
        sleep_cfg = SCENE_TRIGGERS.get("sleep", {})
        auto_range = sleep_cfg.get("auto_hour_range")
        if not auto_range:
            return None

        start, end = auto_range
        in_range = (hour >= start or hour < end)

        if not in_range:
            return None

        # 防抖: 30分钟内不重复触发
        last = self._last_auto.get("sleep", 0)
        if time.time() - last < 1800:
            return None

        # 触发条件: 深夜时段 + 无人移动 + 灯灭
        if not motion_active and not light_on:
            self._last_auto["sleep"] = time.time()
            return {
                "scene_id": "sleep",
                "name": sleep_cfg["name"],
                "tools": sleep_cfg["tools"],
                "reply": sleep_cfg["reply"],
                "auto": True,
            }
        return None

    def list_scenes(self) -> list[dict]:
        """列举所有已注册场景"""
        return [
            {"id": sid, "name": cfg["name"], "keywords": cfg["keywords"]}
            for sid, cfg in SCENE_TRIGGERS.items()
        ]
