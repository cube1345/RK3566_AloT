# 上下文管理器 — 多轮对话历史压缩
import json
import time
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
        self._last_interaction: float = 0
        self._timeout = 300  # 5分钟无交互自动清空

    def add_user(self, text: str):
        self._last_interaction = time.time()
        self._history.append(Session(role="user", content=text))

    def add_assistant(self, text: str, action: str = ""):
        self._last_interaction = time.time()
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

    def build_injectable(self) -> str:
        """返回压缩后的对话摘要，用于注入LLM prompt"""
        if not self._history:
            return ""
        parts = []
        for s in self._history[-6:]:  # 最近6条
            role = "用户" if s.role == "user" else "系统" if s.role == "system" else "Agent"
            text = s.content[:80]
            parts.append(f"{role}: {text}")
        return " | ".join(parts) if parts else ""

    def get_last_n_rounds(self, n: int = 3) -> list[dict]:
        """返回最近N轮对话"""
        result = []
        for s in self._history[-n * 2:]:  # user+assistant = 2 per round
            result.append({"role": s.role, "content": s.content})
        return result

    def prune_if_stale(self):
        """超过timeout自动清空"""
        if self._last_interaction and time.time() - self._last_interaction > self._timeout:
            self.clear()

    def clear(self):
        self._history.clear()
        self._last_interaction = 0

    @property
    def is_active(self) -> bool:
        return bool(self._last_interaction and time.time() - self._last_interaction < self._timeout)

    @property
    def history(self) -> list[Session]:
        return self._history
