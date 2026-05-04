# 用户画像引擎 — 行为追踪 + 规律发现 + 个性化
import json
import logging
import time
from collections import Counter

logger = logging.getLogger("profile_engine")

_PROACTIVE_SCHEDULE = {
    (6, 9): "早上好！需要我帮你看看今天的天气和穿衣建议吗？",
    (11, 13): "中午了，要看看冰箱里有什么可以做的吗？",
    (17, 19): "天快黑了，需要提前开灯或调整温度吗？",
    (21, 23): "夜深了，要开启睡眠模式吗？",
}


class ProfileEngine:
    """分析用户行为，构建画像，发现规律，驱动个性化响应"""

    def __init__(self, db=None, llm=None):
        self._db = db
        self._llm = llm
        self._persona_cache: dict = {}
        self._last_update: float = 0
        self._suggestion_cooldown: dict = {}
        self._suggestion_interval = 1800  # 同类型建议冷却30分钟

    # ===== 画像构建 =====

    def update_profile(self) -> dict:
        """从行为数据重建用户画像，返回所有维度"""
        if not self._db:
            return {}
        self._last_update = time.time()

        stats = self._db.get_behavior_stats(hours=168)  # 7天
        commands = self._db.get_recent_commands(limit=200)
        prefs = self._db.get_prefs(min_confidence=0.0)

        # 1. 活跃时段
        if stats.get("by_hour"):
            self._db.save_profile_dimension(
                "active_hours", json.dumps(stats["by_hour"]), 0.1
            )

        # 2. Agent 使用分布
        if stats.get("by_agent"):
            self._db.save_profile_dimension(
                "agent_usage", json.dumps(stats["by_agent"]), 0.1
            )

        # 3. 指令长度偏好
        if commands:
            lengths = [len(c.get("detail", "")) for c in commands]
            lengths.sort()
            n = len(lengths)
            self._db.save_profile_dimension(
                "command_length",
                json.dumps({
                    "avg": round(sum(lengths) / n, 1),
                    "p50": lengths[n // 2],
                    "p90": lengths[int(n * 0.9)] if n > 1 else lengths[0],
                    "max": lengths[-1],
                }),
                0.15,
            )

        # 4. 温度偏好 (从user_prefs提取)
        temp_prefs = {}
        for k, v in prefs.items():
            if "temp" in k.lower() or "温度" in k or "ac" in k.lower():
                # 尝试解析数值
                import re
                nums = re.findall(r'(\d+)', v)
                if nums:
                    temp_prefs[k] = int(nums[0])
        if temp_prefs:
            self._db.save_profile_dimension(
                "temp_preference", json.dumps(temp_prefs), 0.05
            )

        # 5. 场景触发频次 (从事件日志推断)
        try:
            events = self._db.query_events(168)  # 7天
            scene_counts = Counter(
                e.get("detail", "").split(":")[0]
                for e in events
                if e.get("event_type") == "scene_trigger"
            )
            if scene_counts:
                self._db.save_profile_dimension(
                    "scene_frequency", json.dumps(dict(scene_counts.most_common(10))), 0.1
                )
        except Exception:
            pass

        self._persona_cache = self._db.get_profile()
        return self._persona_cache

    # ===== 画像注入 =====

    def get_persona_context(self) -> str:
        """返回 ~50 字画像摘要，注入 LLM system prompt"""
        if not self._db:
            return ""
        if not self._persona_cache or time.time() - self._last_update > 3600:
            self._persona_cache = self._db.get_profile()

        if not self._persona_cache:
            return ""

        parts = []

        # 活跃时段
        ah = self._persona_cache.get("active_hours", {}).get("value", {})
        if ah:
            try:
                active = sorted(ah.items(), key=lambda x: -x[1])[:3]
                hours_str = "/".join(f"{h}时" for h, _ in active)
                parts.append(f"活跃{hours_str}")
            except Exception:
                pass

        # 指令风格
        cl = self._persona_cache.get("command_length", {}).get("value", {})
        if cl and cl.get("avg", 0) > 0:
            style = "简洁" if cl["avg"] < 10 else "详细"
            parts.append(f"偏爱{style}")

        # 温度偏好
        tp = self._persona_cache.get("temp_preference", {}).get("value", {})
        if tp:
            temps = list(tp.values())
            if temps:
                parts.append(f"常用{sum(temps)//len(temps)}°C")

        # 场景频率
        sf = self._persona_cache.get("scene_frequency", {}).get("value", {})
        if sf and len(sf) >= 2:
            top = sorted(sf.items(), key=lambda x: -x[1])[:2]
            parts.append(f"常{'/'.join(k for k,v in top)}")

        return f"用户画像: {'; '.join(parts)}" if parts else ""

    # ===== 规律发现 =====

    def detect_routines(self, min_occurrences: int = 3, min_days: int = 2) -> list[dict]:
        """发现重复行为模式，自动创建routine条目"""
        if not self._db:
            return []
        patterns = self._db.detect_routines_sql(min_occurrences, min_days)
        routines = []
        for p in patterns:
            name = f"每日{p['hour']}:00 {p['agent'] or p['behavior_type']}"
            pattern_json = json.dumps(
                {"hour": p["hour"], "type": p["behavior_type"], "agent": p["agent"]},
                ensure_ascii=False,
            )
            confidence = min(0.95, 0.3 + p["day_count"] * 0.1 + p["total"] * 0.02)
            try:
                rid = self._db.save_routine(
                    name=name,
                    pattern_data=pattern_json,
                    pattern_type="hourly",
                    auto_detected=True,
                )
                self._db.update_routine(rid, confidence=confidence)
                routines.append({"id": rid, "name": name, "confidence": round(confidence, 2),
                                 "total": p["total"], "day_count": p["day_count"]})
            except Exception as e:
                logger.warning("规律保存失败: %s", e)
        return routines

    # ===== 主动建议 =====

    def get_proactive_suggestion(self, hour: int) -> dict | None:
        """基于时间返回主动建议，含冷却"""
        for (start, end), text in _PROACTIVE_SCHEDULE.items():
            if start <= hour < end:
                key = f"proactive_{start}_{end}"
                last = self._suggestion_cooldown.get(key, 0)
                if time.time() - last >= self._suggestion_interval:
                    self._suggestion_cooldown[key] = time.time()
                    return {
                        "type": "proactive_suggestion",
                        "text": text,
                        "options": ["好的", "不用"],
                        "time_slot": f"{start}-{end}时",
                    }
        return None

    def anticipate_need(self, hour: int, day: int = None) -> str | None:
        """基于规律预判用户需求"""
        if not self._db:
            return None
        # 查询同时段高频行为
        rows = self._db.conn.execute(
            """SELECT detail, COUNT(*) AS cnt FROM user_behaviors
               WHERE hour_of_day=? AND behavior_type='command'
               GROUP BY detail ORDER BY cnt DESC LIMIT 3""",
            (hour,),
        ).fetchall()
        if rows and rows[0][1] >= 2:
            top = [r[0][:30] for r in rows if r[1] >= 2]
            if top:
                return f"这个时段你常{'/'.join(top[:2])}，需要吗？"
        return None

    # ===== 响应个性化 =====

    def personalize_reply(self, base_reply: str) -> str:
        """根据画像调整回复风格"""
        if not self._persona_cache:
            return base_reply
        cl = self._persona_cache.get("command_length", {}).get("value", {})
        if cl and cl.get("avg", 10) < 8:
            # 用户偏爱简洁，截断长回复
            if len(base_reply) > 80:
                return base_reply[:80] + "…"
        return base_reply

    # ===== 完整画像 (API) =====

    def get_full_profile(self) -> dict:
        if not self._db:
            return {"dimensions": {}, "updated_at": 0}
        if not self._persona_cache or self.is_stale():
            self._persona_cache = self._db.get_profile()
        return {"dimensions": self._persona_cache, "updated_at": self._last_update}

    # ===== 画像时效 =====

    def is_stale(self, max_age: int = 3600) -> bool:
        return time.time() - self._last_update > max_age
