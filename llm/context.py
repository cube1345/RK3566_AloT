# 上下文管理器 — 多轮对话历史压缩
import json
from dataclasses import dataclass, field
from typing import Any

from config import AGENT


@dataclass
class Session:
    role: str
    content: str
    key_action: str = ""  # 用于摘要的关键事件


class ContextManager:
    def __init__(self, max_rounds: int = None):
        self.max_rounds = max_rounds or AGENT["max_history_rounds"]
        self._history: list[Session] = []

    def add_user(self, text: str):
        self._history.append(Session(role="user", content=text))

    def add_assistant(self, text: str, action: str = ""):
        self._history.append(Session(role="assistant", content=text, key_action=action))

    def add_system(self, text: str):
        self._history.append(Session(role="system", content=text))

    def build_messages(self, system_prompt: str = "", tool_defs: str = "") -> list[dict]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if tool_defs:
            messages.append({"role": "system", "content": tool_defs})

        # 历史压缩: 只保留最近 N 轮, 旧轮次摘要
        if len(self._history) > self.max_rounds:
            old = self._history[:-self.max_rounds]
            summary_parts = []
            for s in old:
                if s.key_action:
                    summary_parts.append(s.key_action)
                elif s.role == "assistant":
                    summary_parts.append(s.content[:50])
            if summary_parts:
                messages.append({
                    "role": "system",
                    "content": f"[前情摘要]: {'; '.join(summary_parts[:5])}",
                })
            recent = self._history[-self.max_rounds:]
        else:
            recent = self._history

        for s in recent:
            messages.append({"role": s.role, "content": s.content})

        return messages

    def clear(self):
        self._history.clear()

    @property
    def history(self) -> list[Session]:
        return self._history
