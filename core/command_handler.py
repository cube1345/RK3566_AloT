# 用户指令处理器 — Agent 路由 + LLM 推理 + 工具执行
import json
import logging
import re

from agents.environment_agent import SYSTEM_PROMPT as ENV_PROMPT, AGENT_NAME as ENV_NAME
from agents.food_agent import SYSTEM_PROMPT as FOOD_PROMPT, AGENT_NAME as FOOD_NAME
from agents.life_agent import SYSTEM_PROMPT as LIFE_PROMPT, AGENT_NAME as LIFE_NAME
from core.tool_registry import registry

logger = logging.getLogger("command")

# ===== Agent 路由器 =====
_ROUTE_RULES = [
    (["热", "冷", "闷", "温度", "湿度", "空调", "风扇", "通风",
      "CO2", "co2", "二氧化碳", "空气", "灯光", "灯", "亮", "暗",
      "环境", "传感器", "设备"], ENV_NAME),
    (["买", "鸡蛋", "牛奶", "肉", "菜", "食材", "冰箱", "过期",
      "到期", "保质期", "菜谱", "做菜", "吃", "番茄", "水果",
      "厨", "调味"], FOOD_NAME),
    (["穿", "天气", "建议", "今天", "明天", "周末",
      "提醒", "贴士", "小贴士", "日报", "总结", "报告",
      "一般", "随便", "看看"], LIFE_NAME),
]
_DEFAULT_AGENT = LIFE_NAME
_AGENT_MAP = {
    ENV_NAME: ENV_PROMPT,
    FOOD_NAME: FOOD_PROMPT,
    LIFE_NAME: LIFE_PROMPT,
}


def route(text: str) -> str:
    t = text.lower()
    for keywords, agent_name in _ROUTE_RULES:
        if any(kw in t for kw in keywords):
            return agent_name
    return _DEFAULT_AGENT


class CommandHandler:
    def __init__(self, llm=None, db=None, sensors=None):
        self._llm = llm
        self._db = db
        self._sensors = sensors

    def set_llm(self, llm):
        self._llm = llm

    def handle(self, text: str, history: list[dict] | None = None) -> dict:
        agent_name = route(text)
        logger.info("路由: '%s' -> %s", text[:40], agent_name)

        agent_role = _AGENT_MAP[agent_name]
        tool_block = registry.get_prompt_block()
        context = self._build_context()

        system_prompt = (
            "你是一个智能家居工具调用器。\n"
            f"角色: {agent_role}\n\n"
            "## 规则\n"
            "只输出 JSON 数组，不要输出任何其他文字。\n"
            "每个需要做的事都必须通过调用工具完成。\n\n"
            "### 示例\n"
            '太热了 -> [{"tool": "ac_control", "params": {"mode": "cool", "temp": 26, '
            '"fan_speed": "auto"}}, {"tool": "set_fan", "params": {"speed": 1}}]\n\n'
            '买了鸡蛋明天到期 -> [{"tool": "add_food", "params": {"name": "鸡蛋", '
            '"expiry_date": "2026-04-30", "quantity": 1, "unit": "个", '
            '"storage": "冷藏"}}]\n\n'
            '冰箱里有什么 -> [{"tool": "list_foods", "params": {}}]\n\n'
            '今天穿什么 -> [{"tool": "get_weather", "params": {}}]\n\n'
            '你好 -> []\n\n'
            "### 可用工具\n"
            f"{tool_block}\n\n"
            "只输出 JSON。"
        )

        user_prompt = f"当前环境: {context}\n\n用户指令: {text}"

        # LLM 生成
        tool_chain = []
        llm_used = False
        if self._llm and self._llm.is_loaded:
            try:
                raw = self._llm.generate(user_prompt, system=system_prompt)
                logger.info("LLM raw: %.200s", raw)
                tool_chain = _parse_tool_chain(raw)
                llm_used = True
            except Exception as e:
                logger.warning("LLM failed: %s", e)

        if not isinstance(tool_chain, list):
            tool_chain = []

        # 兜底: LLM 未输出工具链时, 尝试正则提取食材指令
        # 兜底: LLM 未输出工具链时, 尝试正则提取食材指令
        if not tool_chain:
            food_chain = _try_food_regex(text)
            if food_chain:
                tool_chain = food_chain

        # 执行工具
        actions = []
        if tool_chain:
            try:
                actions = registry.execute_plan(tool_chain)
                logger.info("executed %d tools", len(actions))
            except Exception as e:
                logger.warning("tool exec failed: %s", e)

        # 构建回复
        reply = self._build_reply(agent_name, actions, text, llm_used)

        if self._db:
            self._db.log_event("user_command", f"[{agent_name}] {text[:100]}")
            for a in actions:
                self._db.log_event(
                    "tool_exec",
                    f"{a.get('tool')}: {str(a.get('result', ''))[:80]}",
                )

        return {
            "reply": reply,
            "actions": actions,
            "agent": agent_name,
            "llm_used": llm_used,
        }

    def _build_context(self) -> str:
        if not self._sensors:
            return "无传感器数据"
        parts = []
        for name in ("co2", "temperature", "light", "motion"):
            try:
                r = self._sensors.read(name)
                parts.append(f"{name}={r.value:.0f}{r.unit}")
            except Exception:
                pass
        try:
            r = self._sensors.read("temperature")
            if r.raw and "humidity" in r.raw:
                parts.append(f"humidity={r.raw['humidity']:.0f}%")
        except Exception:
            pass
        return ", ".join(parts)

    def _build_reply(
        self, agent: str, actions: list[dict], text: str, llm_used: bool
    ) -> str:
        if actions:
            parts = []
            for a in actions:
                r = a.get("result", "")
                parts.append(f"{a['tool']}: {r}" if r else a["tool"])
            return "\n".join(parts)

        # 无工具调用: 纯 LLM 对话
        if llm_used:
            try:
                return self._llm.generate(
                    f"用户说: {text}\n请作为智能家居助手用一句话友好回复。",
                    system=_AGENT_MAP.get(agent, ""),
                )
            except Exception:
                pass
        return f"收到: {text[:60]}"


def _try_food_regex(text: str) -> list[dict] | None:
    """正则提取食材指令: 买了<name>[<数量>][<单位>][<日期>]到期"""
    import datetime

    t = text.strip()
    # 模式1: "买了鸡蛋明天到期" / "买了牛奶5月20到期"
    m = re.match(r'买(?:了)?(.+?)(?:(\d+)\s*(?:个|斤|袋|盒|瓶|包|箱|kg|g))?\s*(?:明天|(\d{1,2})月(\d{1,2})日?)?\s*(?:到期|过期)', t)
    if m:
        name = m.group(1).strip()
        quantity = float(m.group(2)) if m.group(2) else 1
        unit = "个"
        if m.group(2):
            unit_match = re.search(r'(\d+)\s*(个|斤|袋|盒|瓶|包|箱|kg|g)', t)
            if unit_match:
                unit = unit_match.group(2)
        # 日期
        today = datetime.date.today()
        if '明天' in t:
            expiry = (today + datetime.timedelta(days=1)).isoformat()
        elif m.group(3) and m.group(4):
            month, day = int(m.group(3)), int(m.group(4))
            expiry = datetime.date(2026, month, day).isoformat()
        else:
            # 默认7天后过期
            expiry = (today + datetime.timedelta(days=7)).isoformat()
        return [{"tool": "add_food", "params": {"name": name, "expiry_date": expiry, "quantity": quantity, "unit": unit}}]

    # 模式2: "冰箱里有什么" / "什么快过期了"
    if any(kw in t for kw in ("冰箱里有什么", "有什么快过期", "什么快过期", "快过期", "快到期")):
        if "快过期" in t or "快到期" in t or "过期" in t:
            return [{"tool": "list_foods", "params": {"expiring_days": 3}}]
        return [{"tool": "list_foods", "params": {}}]

    return None


def _parse_tool_chain(raw: str) -> list[dict]:
    """解析 LLM 输出的工具链, 兼容 [tool1, tool2] 和完整格式"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return _normalize(parsed)
    except json.JSONDecodeError:
        pass
    for pattern in (
        r'```(?:json)?\s*(\[[\s\S]*?\])\s*```',
        r'(\[[\s\S]*?\])',
    ):
        m = re.search(pattern, raw)
        if m:
            try:
                parsed = json.loads(m.group(1))
                if isinstance(parsed, list):
                    return _normalize(parsed)
            except json.JSONDecodeError:
                pass
    logger.warning("LLM non-JSON: %.120s", raw)
    return []


def _normalize(chain: list) -> list[dict]:
    """统一为 [{tool, params}] 格式"""
    out = []
    for item in chain:
        if isinstance(item, dict) and "tool" in item:
            item.setdefault("params", {})
            out.append(item)
        elif isinstance(item, str):
            out.append({"tool": item, "params": {}})
        else:
            logger.warning("skip item: %s", item)
    return out
