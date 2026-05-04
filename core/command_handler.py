# 用户指令处理器 — Agent 路由 + LLM 推理 + 工具执行
import json
import logging
import re
import time

from agents.environment_agent import SYSTEM_PROMPT as ENV_PROMPT, AGENT_NAME as ENV_NAME
from agents.food_agent import SYSTEM_PROMPT as FOOD_PROMPT, AGENT_NAME as FOOD_NAME
from agents.life_agent import SYSTEM_PROMPT as LIFE_PROMPT, AGENT_NAME as LIFE_NAME
from core.tool_registry import registry
from core.scene_engine import SceneEngine
from llm.context import ContextManager
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
    (["穿", "天气", "建议", "今天", "明天", "周末", "几号", "几点", "日期", "时间",
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


# ===== 模糊指令检测 (v5.2) =====
_AMBIGUITY_PATTERNS = [
    (r'(?:把它|把这个|那个|这个)\s*(?:关|开|调|弄|搞)', '您是指灯、风扇还是空调？'),
    (r'^开一(?:下|哈|个)', '想开哪个设备呢？（灯/风扇/空调/净化器）'),
    (r'^关一(?:下|哈|个)', '想关哪个设备呢？（灯/风扇/空调/净化器）'),
    (r'^(?:然后|还有|另外|接着|再|接下来)$', '还需要我做什么呢？'),
    (r'有点不?舒服', '是温度不合适还是空气质量不好？'),
    (r'^(?:嗯|哦|好|可以|行|对|是)\s*$', None),  # 不是追问场景，是确认回应
]
_AMBIGUITY_OPTIONS = {
    "灯还是风扇": ["灯", "风扇", "空调", "净化器"],
    "想开哪个": ["灯", "风扇", "空调", "净化器"],
    "想关哪个": ["灯", "风扇", "空调", "净化器"],
    "还需要我做": ["查看天气", "检查食材", "不用了"],
    "温度不合适": ["温度偏高，降温", "空气质量不好，通风", "都有"],
}


def _detect_ambiguity(text: str) -> dict | None:
    """检测模糊指令，返回追问dict或None"""
    t = text.strip()
    if not t or len(t) > 20:
        return None
    for pattern, question in _AMBIGUITY_PATTERNS:
        if re.search(pattern, t):
            if question is None:
                return None
            # 匹配选项
            options = ["是", "不用"]
            for key, opts in _AMBIGUITY_OPTIONS.items():
                if key in question:
                    options = opts
                    break
            return {"type": "clarification", "text": question, "options": options}
    return None


def _parse_cot_reply(raw: str) -> str:
    """从CoT输出中提取'分析'字段作为自然语言回复"""
    for pattern in (r'分析\s*[:：]\s*(.+?)(?:\n|$)', r'分析\s*[:：]\s*(.+?)$'):
        m = re.search(pattern, raw, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def _is_meta_cot(text: str) -> bool:
    """判断CoT分析是否为元描述(需要获取数据)而非最终答案"""
    meta_patterns = [
        r'^需(?:要)?获取', r'^需(?:要)?查询', r'^调用', r'^查(?:询|看)',
        r'^获取', r'^先获取', r'^先查询',
    ]
    return any(re.search(p, text) for p in meta_patterns)


def _format_action_results(actions: list[dict]) -> str:
    """将工具执行结果格式化为自然语言"""
    if not actions:
        return ""
    for a in actions:
        result = a.get("result", "")
        tool = a.get("tool", "")
        # 格式化日期时间
        if tool == "get_date_time" and isinstance(result, dict):
            return f"今天是{result.get('date', '?')} {result.get('weekday', '')}，{result.get('time', '?')[:5]}"
        # 格式化天气
        if tool == "get_weather" and isinstance(result, dict):
            return f"室外{result.get('condition', '?')}，温度{result.get('temp', '?')}°C，湿度{result.get('humidity', '?')}%"
    return ""


class CommandHandler:
    def __init__(self, llm=None, db=None, sensors=None):
        self._llm = llm
        self._db = db
        self._sensors = sensors
        self._scene_engine = SceneEngine(llm=llm)
        self._context = ContextManager()
        self._pending_clarification: tuple | None = None  # (orig_text, question, agent)
        self._profile_engine = None  # 延迟初始化, 避免重复创建

    def set_llm(self, llm):
        self._llm = llm
        self._scene_engine._llm = llm

    def set_profile_engine(self, pe):
        self._profile_engine = pe

    def handle(self, text: str, history: list[dict] | None = None) -> dict:
        # 对话超时检查
        self._context.prune_if_stale()

        # 检查是否是对待澄清问题的回答
        if self._pending_clarification:
            orig_text, question, agent = self._pending_clarification
            self._pending_clarification = None
            combined = f"{orig_text} | 用户补充: {text}"
            self._context.add_user(combined)
            return self._process(combined, agent)

        # 场景识别: 命中关键词 → 直接执行工具链
        scene = self._scene_engine.recognize(text)
        if scene:
            logger.info("场景命中: %s → %d工具", scene["name"], len(scene["tools"]))
            actions = registry.execute_plan(scene["tools"])
            if self._db:
                self._db.log_event("scene_trigger", f"{scene['name']}: {text[:80]}")
                for a in actions:
                    self._db.log_event("tool_exec",
                        f"{a.get('tool')}: {str(a.get('result', ''))[:80]}")
                # 行为追踪
                import datetime
                now = datetime.datetime.now()
                self._db.log_behavior("scene_trigger", text[:200], agent="scene",
                                      hour=now.hour, day=now.weekday())
            self._context.add_user(text)
            self._context.add_assistant(scene["reply"], action=scene["name"])
            return {
                "reply": scene["reply"],
                "actions": actions,
                "agent": "scene",
                "llm_used": False,
                "llm_raw": "",
            }

        # 模糊指令检测 → 追问用户
        ambiguity = _detect_ambiguity(text)
        if ambiguity:
            agent_name = route(text)
            self._pending_clarification = (text, ambiguity["text"], agent_name)
            self._context.add_user(text)
            logger.info("追问: %s → %s", text[:40], ambiguity["text"])
            return {
                "reply": f"🤔 {ambiguity['text']}",
                "actions": [],
                "agent": agent_name,
                "llm_used": False,
                "llm_raw": "",
                "clarification": ambiguity,
            }

        # 复杂指令走ReAct多步推理
        complex_kw = ("为什么", "怎么样", "检查", "分析", "如何", "怎么回事", "怎么办")
        if len(text) > 15 or any(kw in text for kw in complex_kw):
            self._context.add_user(text)
            return self._react_loop(text, max_iter=AI_MAX_REACT_ITER)

        agent_name = route(text)
        self._context.add_user(text)
        return self._process(text, agent_name)

    def _process(self, text: str, agent_name: str) -> dict:
        """统一处理入口：路由→prompt→LLM→解析→执行"""
        logger.info("路由: '%s' -> %s", text[:40], agent_name)
        agent_role = _AGENT_MAP[agent_name]
        tool_block = registry.get_prompt_block()
        context = self._build_context()
        system_prompt = self._build_system_prompt(agent_name, agent_role, tool_block)

        user_prompt = f"当前环境: {context}\n\n用户指令: {text}"

        # LLM 生成
        tool_chain = []
        llm_used = False
        raw = ""
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
        reply = self._build_reply(agent_name, actions, text, llm_used, raw)

        if self._db:
            self._db.log_event("user_command", f"[{agent_name}] {text[:100]}")
            for a in actions:
                self._db.log_event(
                    "tool_exec",
                    f"{a.get('tool')}: {str(a.get('result', ''))[:80]}",
                )
            # 行为追踪
            import datetime
            now = datetime.datetime.now()
            self._db.log_behavior("command", text[:200], agent=agent_name,
                                  hour=now.hour, day=now.weekday())

        self._context.add_assistant(reply, action=agent_name)
        return {
            "reply": reply,
            "actions": actions,
            "agent": agent_name,
            "llm_used": llm_used,
            "llm_raw": raw,
        }

    def _build_system_prompt(self, agent_name: str, agent_role: str, tool_block: str) -> str:
        """构建CoT结构化的system prompt"""
        # 用户画像上下文 (使用缓存的实例)
        persona = ""
        if self._profile_engine:
            persona = self._profile_engine.get_persona_context()
        elif self._db:
            try:
                from core.profile_engine import ProfileEngine
                self._profile_engine = ProfileEngine(self._db)
                persona = self._profile_engine.get_persona_context()
            except Exception:
                pass

        # 对话历史上下文
        conv_ctx = self._context.build_injectable()
        conv_block = f"对话历史: {conv_ctx}\n" if conv_ctx else ""

        persona_block = f"{persona}\n" if persona else ""

        return (
            "你是智能家居AI管家。进行任务时请按以下步骤思考：\n\n"
            "观察: 理解用户指令和当前环境。\n"
            "分析: 推理用户真正需要什么操作。\n"
            "决策: 输出JSON工具链。\n\n"
            "=== 规则 ===\n"
            '1. 必须包含「观察/分析/决策」三个步骤\n'
            "2. 决策部分只输出JSON数组，首字符[末字符]\n"
            "3. 不确定用户意图时，用 [反问] 格式代替决策\n"
            "4. 不需操作时决策输出[]\n"
            "5. 不得输出EXPLANATION/TOOLS/行动等标记\n\n"
            "=== 示例 ===\n"
            "用户: 太热了 环境: temperature=32°C, humidity=70%\n"
            "观察: 用户反馈热，当前32°C湿度70%体感闷热\n"
            "分析: 应降温除湿，开空调制冷26°C同时风扇辅助\n"
            "决策: [{\"tool\":\"ac_control\",\"params\":{\"mode\":\"cool\",\"temp\":26,"
            "\"fan_speed\":\"auto\"}},{\"tool\":\"set_fan\",\"params\":{\"speed\":2}}]\n\n"
            "用户: 冰箱里有什么\n"
            "观察: 用户查询冰箱库存\n"
            "分析: 列出所有食材即可\n"
            "决策: [{\"tool\":\"list_foods\",\"params\":{}}]\n\n"
            f"用户: 你好\n"
            "观察: 用户打招呼\n"
            "分析: 无需操作，友好回应\n"
            "决策: []\n\n"
            f"{conv_block}"
            f"{persona_block}"
            f"角色: {agent_role}\n\n"
            f"可用工具:\n{tool_block}\n\n"
            "输出:"
        )

    def _react_loop(self, text: str, max_iter: int = None) -> dict:
        """ReAct循环: LLM行动→观察结果→再决策, 最多max_iter轮"""
        if max_iter is None:
            max_iter = AI_MAX_REACT_ITER
        agent_name = route(text)
        agent_role = _AGENT_MAP[agent_name]
        tool_block = registry.get_prompt_block()
        context = self._build_context()

        all_actions = []
        observation = ""
        llm_used = False
        reasoning_trace = []

        for iteration in range(max_iter):
            if iteration == 0:
                user_prompt = (
                    f"当前环境: {context}\n\n"
                    f"用户指令: {text}\n\n"
                    f"请按观察→分析→决策的步骤输出。"
                )
            else:
                trace_text = "\n".join(reasoning_trace[-3:])
                user_prompt = (
                    f"当前环境: {context}\n"
                    f"已执行结果: {observation}\n"
                    f"之前分析: {trace_text}\n"
                    f"还需要更多操作吗？不需要输出决策: []。"
                )

            system_prompt = (
                "你是智能家居工具调用器。\n"
                f"角色: {agent_role}\n\n"
                "规则:\n"
                "1. 观察: 理解当前状态\n"
                "2. 分析: 推理需要什么\n"
                "3. 决策: 输出JSON数组(不需操作输出[])\n"
                f"这是第{iteration+1}/{max_iter}步。\n\n"
                f"可用工具:\n{tool_block}\n\n"
                "输出:"
            )

            if not self._llm or not self._llm.is_loaded:
                break

            try:
                raw = self._llm.generate(user_prompt, system=system_prompt)
                tool_chain = _parse_tool_chain(raw)
                llm_used = True
                # 提取分析轨迹
                cot = _parse_cot_reply(raw)
                if cot:
                    reasoning_trace.append(cot)
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
            import datetime
            now = datetime.datetime.now()
            self._db.log_behavior("command", text[:200], agent=agent_name,
                                  hour=now.hour, day=now.weekday())

        self._context.add_assistant(reply, action=f"{agent_name}_react")
        return {
            "reply": reply,
            "actions": all_actions,
            "agent": agent_name,
            "llm_used": llm_used,
            "llm_raw": raw if llm_used else "",
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
        self, agent: str, actions: list[dict], text: str, llm_used: bool,
        llm_raw: str = "",
    ) -> str:
        # 格式化工具结果 (优先处理信息查询类工具)
        formatted = _format_action_results(actions)
        if formatted:
            return formatted

        # CoT分析作为自然语言回复 (信息查询类结果已在上面处理)
        if llm_raw:
            cot_reply = _parse_cot_reply(llm_raw)
            if cot_reply and not _is_meta_cot(cot_reply):
                return cot_reply

        if actions:
            parts = []
            for a in actions:
                r = a.get("result", "")
                parts.append(f"{a['tool']}: {r}" if r else a["tool"])
            return "\n".join(parts)

        # CoT分析即使"meta"也算回复
        if llm_raw:
            cot_reply = _parse_cot_reply(llm_raw)
            if cot_reply:
                return cot_reply

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

    # 格式6: AIBrain 格式泄漏 "TOOLS: [...]" / "TOOLS: [{...}]"
    tools_m = re.search(r'TOOLS:\s*(\[[\s\S]*?\])', raw, re.IGNORECASE)
    if tools_m:
        try:
            parsed = json.loads(tools_m.group(1))
            if isinstance(parsed, list):
                return _normalize(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # 格式7: 函数调用风格 "tool_name(p1, v1), tool2(p2)" → 转工具链
    # 例: "set_air_purifier(level=2), notify_display(title,body)"
    func_matches = re.findall(
        r'(\w+)\(([^)]*)\)', raw
    )
    if func_matches:
        chain = []
        for tool_name, args_str in func_matches:
            params = {}
            if args_str.strip():
                for part in args_str.split(","):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        v = v.strip().strip('"').strip("'")
                        try:
                            v = int(v)
                        except ValueError:
                            try:
                                v = float(v)
                            except ValueError:
                                pass
                        params[k.strip()] = v
                    else:
                        params["value"] = part.strip().strip('"').strip("'")
            chain.append({"tool": tool_name, "params": params})
        if chain:
            logger.info("Parsed %d tools from function-call syntax", len(chain))
            return _normalize(chain)

    # 格式8: CoT "决策: [...]" 格式 (v5.2)
    cot_m = re.search(r'决策\s*[:：]\s*(\[[\s\S]*?\])', raw)
    if cot_m:
        try:
            parsed = json.loads(cot_m.group(1))
            if isinstance(parsed, list):
                return _normalize(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

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
