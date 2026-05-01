# AI 决策引擎 — LLM 驱动的主动决策、异常检测、偏好学习、洞察生成
import json
import logging
import re
import statistics
import time

from config import AI_ANOMALY_THRESHOLD, AI_MIN_CONFIDENCE, AI_MAX_REACT_ITER

logger = logging.getLogger("ai_brain")


class AIBrain:
    """LLM 驱动的智能决策核心"""

    def __init__(self, llm=None, db=None, sensors=None):
        self._llm = llm
        self._db = db
        self._sensors = sensors
        self._recent_decisions: list[dict] = []

    def set_llm(self, llm):
        self._llm = llm

    # ===== P0: AI 决策引擎 =====

    def evaluate(self, sensor_snapshot: dict) -> tuple[list[dict], str]:
        """每30s评估传感器快照，返回 (tool_chain, explanation)"""
        if not self._llm or not self._llm.is_loaded:
            return [], ""

        recent = self._get_recent_actions_context()
        prefs = self._get_preferences_context()

        system_prompt = (
            "你是智能家居AI管家。基于传感器数据决定是否需要操作。\n\n"
            "原则:\n"
            "1. 仅明确需要时才行动。舒适与节能并重。\n"
            "2. 不重复刚执行的操作。\n"
            "3. 尊重用户偏好。\n"
            "4. CO₂>1500需通风, 温度>30需降温, 温度<16需升温。\n"
            "5. 有人→舒适优先, 无人→节能优先。\n\n"
            "输出格式(严格):\n"
            "EXPLANATION: <一句中文解释>\n"
            "TOOLS: <JSON数组, 不需要则输出[]>\n\n"
            "可用工具: ac_control(mode,temp,fan_speed), set_fan(speed), "
            "set_light(state,brightness), set_air_purifier(level), "
            "tts(text), notify_display(title,body)\n\n"
            "示例:\n"
            "EXPLANATION: 温度29°C湿度75%,开启空调降温。\n"
            'TOOLS: [{"tool":"ac_control","params":{"mode":"cool","temp":26,"fan_speed":"auto"}},{"tool":"set_fan","params":{"speed":1}}]\n\n'
            "EXPLANATION: 传感器正常,无需操作。\n"
            "TOOLS: []\n\n"
            "只输出上述格式。禁止额外文字。"
        )

        co2 = sensor_snapshot.get("co2", "?")
        temp = sensor_snapshot.get("temperature", "?")
        humidity = sensor_snapshot.get("humidity", "?")
        light = sensor_snapshot.get("light", "?")
        person = "有人" if sensor_snapshot.get("person_present") else "无人"

        user_prompt = (
            f"当前传感器:\n"
            f"  CO₂={co2}ppm 温度={temp}°C 湿度={humidity}% 光照={light}lux {person}\n"
            + (f"\n最近操作: {recent}\n" if recent else "\n")
            + (f"用户偏好: {prefs}\n" if prefs else "")
            + "\n需要操作吗？"
        )

        try:
            raw = self._llm.generate(user_prompt, system=system_prompt)
        except Exception as e:
            logger.warning("AI决策LLM调用失败: %s", e)
            return [], ""

        explanation, tool_chain = self._parse_decision_output(raw)

        if self._db:
            self._db.log_ai_decision(
                json.dumps(sensor_snapshot, ensure_ascii=False),
                json.dumps(tool_chain, ensure_ascii=False),
                raw[:500],
            )

        self._recent_decisions.append({
            "snapshot": sensor_snapshot,
            "actions": tool_chain,
            "time": time.time(),
        })
        if len(self._recent_decisions) > 10:
            self._recent_decisions = self._recent_decisions[-10:]

        return tool_chain, explanation

    # ===== P3: 异常检测 =====

    def detect_anomaly(self, sensor_name: str, old_value: float, new_value: float) -> bool:
        """z-score检测传感器读数跳变"""
        if not self._db:
            return False
        try:
            recent = self._db.query_sensor(sensor_name, hours=1)
        except Exception:
            return False
        if len(recent) < 5:
            return False
        values = [r["value"] for r in recent[-10:]]
        std = statistics.stdev(values) if len(values) > 1 else 1.0
        if std == 0:
            return False
        mean = statistics.mean(values)
        z = abs(new_value - mean) / std
        return z > AI_ANOMALY_THRESHOLD

    # ===== P4: 主动洞察 =====

    def generate_insight(self, sensor_trends: dict, events: list[dict]) -> str:
        """分析24h趋势生成个性化建议"""
        if not self._llm or not self._llm.is_loaded:
            return ""
        stats = {}
        for name, values in sensor_trends.items():
            if values:
                stats[name] = {
                    "min": round(min(values), 1),
                    "max": round(max(values), 1),
                    "avg": round(sum(values) / len(values), 1),
                }
        event_summary = [e.get("detail", "")[:60] for e in events[:10]]
        prompt = (
            "分析以下24h家庭数据，给出一条具体可操作的中文建议。\n\n"
            f"传感器统计: {json.dumps(stats, ensure_ascii=False)}\n"
            f"最近事件: {', '.join(event_summary) if event_summary else '无'}\n\n"
            "格式: 一句具体建议。如'CO₂每天14-17时超过1200ppm,建议13:30提前通风。'"
        )
        try:
            raw = self._llm.generate(prompt, system="你是家庭数据分析师。请具体、数据驱动。")
        except Exception as e:
            logger.warning("AI洞察生成失败: %s", e)
            return ""
        return raw.strip()

    # ===== P2: 偏好学习 =====

    def learn_preference(self, trigger: str, user_action: str):
        """记录用户覆盖行为，积累偏好"""
        if not self._db:
            return
        self._db.save_pref(key=trigger, value=user_action)
        logger.info("偏好学习: %s → %s", trigger, user_action)

    # ===== 内部方法 =====

    def _parse_decision_output(self, raw: str) -> tuple[str, list[dict]]:
        """解析 LLM 输出: EXPLANATION + TOOLS"""
        explanation = ""
        tool_chain = []

        exp_m = re.search(r'EXPLANATION:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
        if exp_m:
            explanation = exp_m.group(1).strip()

        tools_m = re.search(r'TOOLS:\s*(\[[\s\S]*?\])', raw, re.IGNORECASE)
        if tools_m:
            try:
                parsed = json.loads(tools_m.group(1))
                if isinstance(parsed, list):
                    tool_chain = _normalize_chain(parsed)
            except json.JSONDecodeError:
                pass

        if not tool_chain:
            from core.command_handler import _parse_tool_chain
            tool_chain = _parse_tool_chain(raw)

        return explanation, tool_chain

    def _get_recent_actions_context(self) -> str:
        if not self._recent_decisions:
            return ""
        parts = []
        for d in self._recent_decisions[-3:]:
            actions = [a.get("tool", "?") for a in d.get("actions", [])]
            if actions:
                elapsed = int(time.time() - d.get("time", 0))
                parts.append(f"{'→'.join(actions)} ({elapsed}s前)")
        return "; ".join(parts) if parts else ""

    def _get_preferences_context(self) -> str:
        if not self._db:
            return ""
        prefs = self._db.get_prefs(min_confidence=AI_MIN_CONFIDENCE)
        if not prefs:
            return ""
        return "; ".join(f"{k}:{v}" for k, v in prefs.items())


def _normalize_chain(chain: list) -> list[dict]:
    """统一工具链格式 [{tool, params}]"""
    out = []
    for item in chain:
        if isinstance(item, dict) and "tool" in item:
            item.setdefault("params", {})
            out.append(item)
        elif isinstance(item, str):
            out.append({"tool": item, "params": {}})
    return out
