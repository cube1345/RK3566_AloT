# 用户指令处理器 — Agent 路由 + LLM 推理 + 工具执行
import json
import logging
import re

from agents.environment_agent import SYSTEM_PROMPT as ENV_PROMPT, AGENT_NAME as ENV_NAME
from agents.food_agent import SYSTEM_PROMPT as FOOD_PROMPT, AGENT_NAME as FOOD_NAME
from agents.life_agent import SYSTEM_PROMPT as LIFE_PROMPT, AGENT_NAME as LIFE_NAME
from core.tool_registry import registry
from config import AI_MAX_REACT_ITER

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
        # 复杂指令走ReAct多步推理
        complex_kw = ("为什么", "怎么样", "检查", "分析", "如何", "怎么回事", "怎么办")
        if len(text) > 15 or any(kw in text for kw in complex_kw):
            return self._react_loop(text, max_iter=AI_MAX_REACT_ITER)

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

    def _react_loop(self, text: str, max_iter: int = 2) -> dict:
        """ReAct循环: LLM行动→观察结果→再决策, 最多max_iter轮"""
        agent_name = route(text)
        agent_role = _AGENT_MAP[agent_name]
        tool_block = registry.get_prompt_block()
        context = self._build_context()

        all_actions = []
        observation = ""
        llm_used = False

        for iteration in range(max_iter):
            if iteration == 0:
                user_prompt = f"当前环境: {context}\n\n用户指令: {text}\n\n需要什么工具？只输出JSON数组。"
            else:
                user_prompt = (
                    f"当前环境: {context}\n"
                    f"已执行结果: {observation}\n"
                    f"还需要更多操作吗？不需要输出[]。"
                )

            system_prompt = (
                "你是智能家居工具调用器。\n"
                f"角色: {agent_role}\n\n"
                "规则: 只输出JSON数组。没有文字。不需要就[]。\n"
                f"这是第{iteration+1}/{max_iter}步。\n\n"
                f"### 可用工具\n{tool_block}\n\n"
                "只输出JSON。"
            )

            if not self._llm or not self._llm.is_loaded:
                break

            try:
                raw = self._llm.generate(user_prompt, system=system_prompt)
                tool_chain = _parse_tool_chain(raw)
                llm_used = True
            except Exception as e:
                logger.warning("ReAct迭代%d失败: %s", iteration+1, e)
                break

            if not tool_chain:
                break

            actions = registry.execute_plan(tool_chain)
            all_actions.extend(actions)

            observation = "; ".join(
                f"{a['tool']}→{str(a.get('result',''))[:80]}"
                for a in actions
            )
            logger.info("ReAct[%d/%d]: %d工具 → %s",
                        iteration+1, max_iter, len(actions), observation[:100])

        reply = self._build_reply(agent_name, all_actions, text, llm_used)

        if self._db:
            self._db.log_event("user_command", f"[{agent_name}][ReAct] {text[:100]}")
            for a in all_actions:
                self._db.log_event("tool_exec",
                    f"{a.get('tool')}: {str(a.get('result',''))[:80]}")

        return {
            "reply": reply,
            "actions": all_actions,
            "agent": agent_name,
            "llm_used": llm_used,
            "iterations": iteration + 1,
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
    """正则提取食材指令: 支持带数量和不带数量两种模式"""
    import datetime

    t = text.strip()
    today = datetime.date.today()

    # 模式1: 数量在名称前 "买了3斤苹果明天到期" "买了5个鸡蛋过期"
    m = re.match(
        r'买(?:了)?(\d+)\s*(个|斤|袋|盒|瓶|包|箱)\s*(.+?)\s*'
        r'(?:明天|(\d{1,2})月(\d{1,2})日?)?\s*(?:到期|过期)',
        t,
    )
    if m:
        quantity = float(m.group(1))
        unit = m.group(2)
        name = m.group(3).strip()
        if "明天" in t:
            expiry = (today + datetime.timedelta(days=1)).isoformat()
        elif m.group(4) and m.group(5):
            expiry = datetime.date(2026, int(m.group(4)), int(m.group(5))).isoformat()
        else:
            expiry = (today + datetime.timedelta(days=7)).isoformat()
        return [{"tool": "add_food", "params": {"name": name, "expiry_date": expiry, "quantity": quantity, "unit": unit}}]

    # 模式2: 无数量 或 数量在名称后 "买了鸡蛋明天到期" / "买了鸡蛋10个明天到期"
    m = re.match(
        r'买(?:了)?(.+?)(?:\s*(\d+)\s*(个|斤|袋|盒|瓶|包|箱))?\s*'
        r'(?:明天|(\d{1,2})月(\d{1,2})日?)?\s*(?:到期|过期)',
        t,
    )
    if m:
        name = m.group(1).strip()
        quantity = float(m.group(2)) if m.group(2) else 1
        unit = m.group(3) if m.group(3) else "个"
        if "明天" in t:
            expiry = (today + datetime.timedelta(days=1)).isoformat()
        elif m.group(4) and m.group(5):
            expiry = datetime.date(2026, int(m.group(4)), int(m.group(5))).isoformat()
        else:
            expiry = (today + datetime.timedelta(days=7)).isoformat()
        return [{"tool": "add_food", "params": {"name": name, "expiry_date": expiry, "quantity": quantity, "unit": unit}}]

    # 模式3: 库存查询
    if any(kw in t for kw in ("冰箱里有什么", "有什么快过期", "什么快过期", "快过期", "快到期")):
        if "快过期" in t or "快到期" in t or "过期" in t:
            return [{"tool": "list_foods", "params": {"expiring_days": 3}}]
        return [{"tool": "list_foods", "params": {}}]

    return None


def _parse_tool_chain(raw: str) -> list[dict]:
    """解析 LLM 输出的工具链, 兼容 5 种格式 + 多层回退"""
    if not raw:
        return []

    # 格式1: 裸 JSON
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return _normalize(parsed)
    except (json.JSONDecodeError, TypeError):
        pass

    # 格式2: 代码块 ```json [...] ```
    for pattern in (
        r'```(?:json)?\s*(\[[\s\S]*?\])\s*```',
        r'```(?:json)?\s*(\[[\s\S]*\])\s*```',
    ):
        m = re.search(pattern, raw)
        if m:
            try:
                parsed = json.loads(m.group(1))
                if isinstance(parsed, list):
                    return _normalize(parsed)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    # 格式3: 行内 `[tool1, tool2]`
    m = re.search(r'`(\[[\s\S]*?\])`', raw)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                return _normalize(parsed)
        except (json.JSONDecodeError, TypeError):
            pass

    # 格式4: 贪婪提取, 找第一个 [ 到最后一个 ]
    start = raw.find('[')
    end = raw.rfind(']')
    if start != -1 and end > start:
        try:
            parsed = json.loads(raw[start:end + 1])
            if isinstance(parsed, list):
                return _normalize(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # 格式5: 单工具对象 {"tool": "...", ...} 不在数组里 (含嵌套)
    idx = raw.find('"tool"')
    if idx != -1:
        # 往前找 {, 往后数 } 找到匹配的闭合
        start = raw.rfind('{', 0, idx)
        if start != -1:
            depth = 0
            for end in range(start, len(raw)):
                if raw[end] == '{':
                    depth += 1
                elif raw[end] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(raw[start:end + 1])
                            if isinstance(parsed, dict) and "tool" in parsed:
                                return _normalize([parsed])
                        except (json.JSONDecodeError, TypeError):
                            pass
                        break

    logger.warning("LLM non-JSON: %.160s", raw)
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
