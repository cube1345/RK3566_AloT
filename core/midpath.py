# MidPath 异步补充处理器
# 规则先行执行 → LLM 异步生成自然语言回复
import asyncio
import logging

from config import AGENT

logger = logging.getLogger("midpath")


class MidPathHandler:
    """处理中等置信度指令: 规则先执行, LLM 异步补充"""

    def __init__(self, llm=None):
        self._llm = llm

    def set_llm(self, llm):
        self._llm = llm

    async def supplement(self, user_input: str, actions_taken: list[dict],
                         context: dict) -> str:
        """异步生成回复补充"""
        if not self._llm or not self._llm.is_loaded:
            # LLM 不可用时, 给出模板回复
            action_names = [a.get("tool", a.get("action", "设备")) for a in actions_taken]
            return f"已执行: {', '.join(action_names)}"

        prompt = self._build_prompt(user_input, actions_taken, context)
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._llm.generate, prompt
            )
            return result.strip() or self._fallback_reply(actions_taken)
        except Exception as e:
            logger.warning("MidPath LLM 补充失败: %s", e)
            return self._fallback_reply(actions_taken)

    def _build_prompt(self, user_input: str, actions: list[dict],
                      context: dict) -> str:
        """构造 MidPath 补充 prompt"""
        action_str = "\n".join(
            f"- {a.get('tool', '?')}: {a.get('result', '')}"
            for a in actions
        )
        return f"""用户说: {user_input}

系统已执行以下操作:
{action_str}

当前环境: temp={context.get('temp', '?')}°C, humidity={context.get('humidity', '?')}%, CO₂={context.get('co2', '?')}ppm

请用一句自然语言回应用户, 简要说明已完成的操作用户, 语气友好。"""

    def _fallback_reply(self, actions: list[dict]) -> str:
        acts = [a.get("tool", "操作") for a in actions]
        return f"已完成: {', '.join(acts)}"
