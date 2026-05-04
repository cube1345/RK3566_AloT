# llama.cpp 后端 (CPU 推理) — 专用工作线程确保线程安全
import logging
import queue
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
        self._load()

        # 专用工作线程: 所有 llama.cpp 调用在该线程执行
        self._queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

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

    def _worker_loop(self):
        while True:
            try:
                item = self._queue.get()
                if item is None:  # 停止信号
                    break
                messages, result_holder, result_event = item
                output = self._do_generate(messages)
                result_holder["output"] = output
                result_event.set()
            except Exception as e:
                logger.error("Worker LLM异常: %s", e)

    def _do_generate(self, messages: list[dict]) -> str:
        if not self._model:
            return ""
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

    def generate(self, prompt: str, system: str = "") -> str:
        if not self._model:
            return ""

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        result_event = threading.Event()
        result_holder = {"output": ""}
        self._queue.put((messages, result_holder, result_event))
        result_event.wait()
        return result_holder["output"]

    def generate_stream(self, prompt: str, system: str = ""):
        """流式生成, yield token块"""
        if not self._model:
            yield ""
            return

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

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
