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
        self._pending_question: dict | None = None
        self._profile_engine = None
        self._suggestion_cooldown: dict = {}
        self._suggestion_interval = 1800  # 同类型建议冷却30分钟

    def set_llm(self, llm):
        self._llm = llm

    def set_profile_engine(self, pe):
        self._profile_engine = pe

    # ===== P0: AI 决策引擎 =====

    def evaluate(self, sensor_snapshot: dict) -> dict:
        """
        每30s评估传感器快照。
        返回:
          {"type": "action", "tool_chain": [...], "explanation": "..."}
          {"type": "question", "text": "...", "options": [...], "pending_tools": [...]}
          {"type": "none"}
        """
        if not self._llm or not self._llm.is_loaded:
            return {"type": "none"}

        recent = self._get_recent_actions_context()
        prefs = self._get_preferences_context()
        knowledge = self._build_knowledge_context(sensor_snapshot)
        persona = self._get_persona_context()

        system_prompt = (
            "你是智能家居AI管家。基于传感器数据决定是否需要操作。\n\n"
            "原则: CO₂>1500需通风, 温度>30需降温, 温度<16需升温。有人→舒适优先, 无人→节能优先。"
            "不重复刚执行的操作。尊重用户偏好。不确定时反问用户。\n\n"
            "=== 输出格式 (严格按此结构) ===\n\n"
            "如需行动:\n"
            "观察: <传感器状态解读>\n"
            "分析: <推理是否/为何行动>\n"
            "决策: [{\"tool\":\"ac_control\",\"params\":{\"mode\":\"cool\",\"temp\":26,\"fan_speed\":\"auto\"}}]\n\n"
            "如需反问:\n"
            "观察: <传感器状态解读>\n"
            "分析: <推理, 说明为何不确定>\n"
            "反问: <问题>\n"
            "选项: [\"选项1\",\"选项2\"]\n"
            "决策: [{\"tool\":\"set_fan\",\"params\":{\"speed\":3}}]\n\n"
            "如无需操作:\n"
            "观察: <传感器状态解读>\n"
            "分析: <推理为何无需行动>\n"
            "决策: []\n\n"
            "示例:\n"
            "观察: 温度30°C偏高, CO₂ 600正常, 有人在场\n"
            "分析: 温度超出舒适范围, 需制冷降温, 无需通风\n"
            "决策: [{\"tool\":\"ac_control\",\"params\":{\"mode\":\"cool\",\"temp\":26,\"fan_speed\":\"auto\"}}]\n\n"
            "可用工具: ac_control(mode,temp,fan_speed), set_fan(speed), "
            "set_light(state,brightness), set_air_purifier(level), "
            "tts(text), notify_display(title,body)\n"
            f"{persona}\n"
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
            + (f"\n历史参考:\n{knowledge}\n" if knowledge else "\n")
            + (f"最近操作: {recent}\n" if recent else "")
            + (f"用户偏好: {prefs}\n" if prefs else "")
            + "\n需要操作吗？"
        )

        try:
            raw = self._llm.generate(user_prompt, system=system_prompt)
        except Exception as e:
            logger.warning("AI决策LLM调用失败: %s", e)
            return {"type": "none"}

        result = self._parse_decision_output(raw)

        # 无操作时尝试主动建议
        if result.get("type") == "none":
            suggestion = self._get_proactive_suggestion()
            if suggestion:
                result = suggestion

        if self._db:
            tool_chain = result.get("tool_chain", [])
            self._db.log_ai_decision(
                json.dumps(sensor_snapshot, ensure_ascii=False),
                json.dumps(tool_chain, ensure_ascii=False),
                raw[:500],
            )

        self._recent_decisions.append({
            "snapshot": sensor_snapshot,
            "actions": result.get("tool_chain", []),
            "time": time.time(),
        })
        if len(self._recent_decisions) > 10:
            self._recent_decisions = self._recent_decisions[-10:]

        # 记录待答问题
        if result.get("type") == "question":
            self._pending_question = result
        else:
            self._pending_question = None

        return result

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

    def _parse_decision_output(self, raw: str) -> dict:
        """解析 CoT LLM 输出 → {"type":"action"|"question"|"none", ...}"""
        explanation = ""
        tool_chain = []

        # 提取"分析"字段作为解释 (CoT格式)
        exp_m = re.search(r'分析\s*[:：]\s*(.+?)(?:\n|$)', raw)
        if exp_m:
            explanation = exp_m.group(1).strip()
        # 向后兼容旧格式 EXPLANATION:
        if not explanation:
            exp_old = re.search(r'EXPLANATION:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
            if exp_old:
                explanation = exp_old.group(1).strip()

        # 检测反问格式 (v5.3 CoT): 反问: ... 选项: [...] 决策: [...]
        q_m = re.search(r'反问\s*[:：]\s*(.+?)(?:\n|$)', raw)
        if q_m:
            q_text = q_m.group(1).strip()
            options = []
            pending_tools = []
            opt_m = re.search(r'选项\s*[:：]\s*(\[[\s\S]*?\])', raw)
            if opt_m:
                try:
                    options = json.loads(opt_m.group(1))
                except json.JSONDecodeError:
                    options = ["是", "不用"]
            # 从决策字段提取待执行工具
            dec_m = re.search(r'决策\s*[:：]\s*(\[[\s\S]*?\])', raw)
            if dec_m:
                try:
                    pending_tools = _normalize_chain(json.loads(dec_m.group(1)))
                except json.JSONDecodeError:
                    pass
            return {"type": "question", "text": q_text,
                    "options": options, "pending_tools": pending_tools}

        # 向后兼容旧格式: QUESTION: ... OPTIONS: [...] PENDING: [...]
        q_m_old = re.search(r'QUESTION:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
        if q_m_old:
            q_text = q_m_old.group(1).strip()
            options = []
            pending_tools = []
            opt_m = re.search(r'OPTIONS:\s*(\[[\s\S]*?\])', raw, re.IGNORECASE)
            if opt_m:
                try:
                    options = json.loads(opt_m.group(1))
                except json.JSONDecodeError:
                    options = ["是", "不用"]
            pend_m = re.search(r'PENDING:\s*(\[[\s\S]*?\])', raw, re.IGNORECASE)
            if pend_m:
                try:
                    pending_tools = _normalize_chain(json.loads(pend_m.group(1)))
                except json.JSONDecodeError:
                    pass
            return {"type": "question", "text": q_text,
                    "options": options, "pending_tools": pending_tools}

        # 提取决策 JSON: CoT格式 "决策: [...]" 优先
        dec_m = re.search(r'决策\s*[:：]\s*(\[[\s\S]*?\])', raw)
        if dec_m:
            try:
                parsed = json.loads(dec_m.group(1))
                if isinstance(parsed, list):
                    tool_chain = _normalize_chain(parsed)
            except json.JSONDecodeError:
                pass

        # 向后兼容旧格式: TOOLS: [...]
        if not tool_chain:
            tools_m = re.search(r'TOOLS:\s*(\[[\s\S]*?\])', raw, re.IGNORECASE)
            if tools_m:
                try:
                    parsed = json.loads(tools_m.group(1))
                    if isinstance(parsed, list):
                        tool_chain = _normalize_chain(parsed)
                except json.JSONDecodeError:
                    pass

        # 兜底: 通用工具链解析
        if not tool_chain:
            from core.command_handler import _parse_tool_chain
            tool_chain = _parse_tool_chain(raw)

        if tool_chain:
            return {"type": "action", "tool_chain": tool_chain, "explanation": explanation}
        return {"type": "none"}

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

    def _get_persona_context(self) -> str:
        """获取用户画像注入文本"""
        if self._profile_engine:
            ctx = self._profile_engine.get_persona_context()
            if ctx:
                return ctx
        return ""

    def _get_proactive_suggestion(self) -> dict | None:
        """基于时间返回主动建议"""
        if self._profile_engine:
            hour = time.localtime().tm_hour
            return self._profile_engine.get_proactive_suggestion(hour)
        return None

    def _build_knowledge_context(self, snapshot: dict) -> str:
        """构建知识上下文: 历史同时段对比 + 日均趋势"""
        if not self._db:
            return ""
        parts = []
        for sensor in ("co2", "temperature"):
            try:
                same_hour = self._db.query_sensor_same_hour(sensor, days=7)
                if same_hour and sensor in snapshot:
                    vals = [r["value"] for r in same_hour]
                    current = snapshot[sensor]
                    if isinstance(current, (int, float)) and vals:
                        avg = sum(vals) / len(vals)
                        pct = abs(current - avg) / max(abs(avg), 1)
                        if pct > 0.15:
                            direction = "偏高" if current > avg else "偏低"
                            parts.append(
                                f"{sensor}当前{current:.0f}, "
                                f"过去7天同时段均值{avg:.0f} ({direction}{abs(current-avg):.0f})"
                            )
            except Exception:
                continue
        return "\n".join(parts) if parts else ""

    def get_pending_question(self) -> dict | None:
        """返回当前待答问题"""
        return self._pending_question

    def clear_pending_question(self):
        """清除待答问题"""
        self._pending_question = None


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
