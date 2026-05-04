# llama.cpp 后端 (CPU 推理) — 线程锁确保线程安全
import logging
import threading
from pathlib import Path

logger = logging.getLogger("llm.llamacpp")


class LlamaCppBackend:
    def __init__(self, model_path: str, n_ctx: int = 4096,
                 max_tokens: int = 512, temperature: float = 0.7,
                 n_threads: int = 4, n_batch: int = 512):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.n_threads = n_threads
        self.n_batch = n_batch
        self._model = None
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        from llama_cpp import Llama
        path = str(Path(self.model_path).resolve())
        logger.info("加载模型: %s (ctx=%d, threads=%d, batch=%d)",
                    path, self.n_ctx, self.n_threads, self.n_batch)
        self._model = Llama(
            model_path=path,
            n_ctx=self.n_ctx,
            n_threads=self.n_threads,
            n_batch=self.n_batch,
            verbose=False,
        )
        logger.info("模型加载完成")

    def _build_messages(self, prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def generate(self, prompt: str, system: str = "") -> str:
        if not self._model:
            return ""
        messages = self._build_messages(prompt, system)
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

    def generate_stream(self, prompt: str, system: str = ""):
        """流式生成, yield token块 — 锁保护整个迭代过程"""
        if not self._model:
            yield ""
            return

        messages = self._build_messages(prompt, system)
        with self._lock:
            try:
                stream = self._model.create_chat_completion(
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    stop=["<|im_end|>", "<|endoftext|>"],
                    stream=True,
                )
                for chunk in stream:
                    choices = chunk.get("choices", [])
                    if choices and choices[0].get("delta", {}).get("content"):
                        yield choices[0]["delta"]["content"]
            except Exception as e:
                logger.error("流式推理失败: %s", e)
                yield ""
