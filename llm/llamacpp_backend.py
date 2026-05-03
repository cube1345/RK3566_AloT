# llama.cpp 后端 (CPU 推理)
import logging
import threading
from pathlib import Path

logger = logging.getLogger("llm.llamacpp")


class LlamaCppBackend:
    def __init__(self, model_path: str, n_ctx: int = 4096,
                 max_tokens: int = 512, temperature: float = 0.7):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._model = None
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        from llama_cpp import Llama
        path = str(Path(self.model_path).resolve())
        logger.info("加载模型: %s (ctx=%d)", path, self.n_ctx)
        self._model = Llama(
            model_path=path,
            n_ctx=self.n_ctx,
            n_threads=4,
            verbose=False,
        )
        logger.info("模型加载完成")

    def generate(self, prompt: str, system: str = "") -> str:
        if not self._model:
            return ""

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        with self._lock:
            try:
                result = self._model.create_chat_completion(
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    stop=["<|im_end|>", "<|endoftext|>"],
                )
                return result["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.error("推理失败: %s", e)
                return ""
