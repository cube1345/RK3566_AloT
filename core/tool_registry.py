# 工具注册与执行引擎
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDef:
    name: str
    fn: Callable
    description: str
    params_schema: dict = field(default_factory=dict)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, name: str = None, description: str = ""):
        """装饰器: 注册工具"""
        def decorator(fn):
            n = name or fn.__name__
            sig = inspect.signature(fn)
            self._tools[n] = ToolDef(
                name=n,
                fn=fn,
                description=description or fn.__doc__ or "",
                params_schema={
                    k: str(v.annotation) for k, v in sig.parameters.items()
                    if k != "self"
                },
            )
            return fn
        return decorator

    def execute(self, tool_name: str, **kwargs) -> Any:
        if tool_name not in self._tools:
            raise KeyError(f"工具 '{tool_name}' 未注册")
        return self._tools[tool_name].fn(**kwargs)

    def execute_plan(self, plan: list[dict]) -> list[dict]:
        """并行执行工具链 (所有工具并行, 后续可加依赖编排)"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _run_one(call: dict):
            return {
                "tool": call["tool"],
                "result": self.execute(call["tool"], **call.get("params", {})),
            }

        with ThreadPoolExecutor(max_workers=min(len(plan) or 1, 8)) as pool:
            futures = {pool.submit(_run_one, c): c for c in plan}
            return [f.result() for f in as_completed(futures)]

    def list_tools(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "params": t.params_schema}
            for t in self._tools.values()
        ]

    def get_prompt_block(self) -> str:
        """给 LLM 的工具描述 (压缩格式)"""
        lines = ["可用工具:"]
        for t in self._tools.values():
            params = ", ".join(t.params_schema.keys())
            lines.append(f"  {t.name}({params}) - {t.description}")
        return "\n".join(lines)

    def __getitem__(self, name: str) -> ToolDef:
        return self._tools[name]

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# 全局单例
registry = ToolRegistry()
