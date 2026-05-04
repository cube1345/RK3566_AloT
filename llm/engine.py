# LLM 推理引擎 — 统一接口 (llama.cpp / RKNN-LLM)
import json
import logging
import time
from typing import Any
from pathlib import Path

from config import LLM_CTX_SIZE, LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_N_THREADS, LLM_N_BATCH

logger = logging.getLogger("llm")


class LLMEngine:
    """LLM 推理引擎: 先尝试 RKNN-LLM, fallback 到 llama.cpp"""

    def __init__(self, model_path: str):
        self.model_path = Path(model_path)
        self._backend = None
        self._loaded = False
        self._load()

    def _load(self):
        if not self.model_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {self.model_path}")

        # 优先 RKNN-LLM (RK3588 NPU)
        if self._try_rknn():
            return

        # Fallback: llama.cpp
        if self._try_llamacpp():
            return

        raise RuntimeError("无法加载 LLM: 无可用后端")

    def _try_rknn(self) -> bool:
        try:
            from llm.rknn_backend import RKNNBackend
            self._backend = RKNNBackend(str(self.model_path))
            self._loaded = True
            self._backend_name = "rknn"
            logger.info("LLM 后端: RKNN-LLM (NPU)")
            return True
        except ImportError:
            return False

    def _try_llamacpp(self) -> bool:
        try:
            from llm.llamacpp_backend import LlamaCppBackend
            self._backend = LlamaCppBackend(
                str(self.model_path),
                n_ctx=LLM_CTX_SIZE,
                max_tokens=LLM_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
                n_threads=LLM_N_THREADS,
                n_batch=LLM_N_BATCH,
            )
            self._loaded = True
            self._backend_name = "llamacpp"
            logger.info("LLM 后端: llama.cpp (CPU)")
            return True
        except ImportError:
            return False

    def generate(self, prompt: str, system: str = "") -> str:
        if not self._loaded:
            return ""
        try:
            return self._backend.generate(prompt, system)
        except Exception as e:
            logger.error("LLM 推理失败: %s", e)
            return ""

    def generate_stream(self, prompt: str, system: str = ""):
        """流式生成, yield token块"""
        if not self._loaded:
            yield ""
            return
        try:
            yield from self._backend.generate_stream(prompt, system)
        except Exception as e:
            logger.error("流式生成失败: %s", e)
            yield ""

    def generate_structured(self, prompt: str, system: str = "") -> list[dict]:
        """生成结构化工具调用链"""
        raw = self.generate(prompt, system)
        return self._parse_structured(raw)

    def _parse_structured(self, raw: str) -> list[dict]:
        """从 LLM 输出中解析工具链"""
        try:
            # 尝试 JSON 直接解析
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 尝试从代码块提取
        import re
        m = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', raw)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        logger.warning("LLM 输出非结构化 JSON: %.100s", raw)
        return []

    @property
    def is_loaded(self) -> bool:
        return self._loaded
