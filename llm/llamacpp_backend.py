# llama.cpp 后端 (CPU fallback)
import logging

logger = logging.getLogger("llm.llamacpp")


class LlamaCppBackend:
    """llama.cpp CPU 推理 (占位, 需 llama-cpp-python)"""

    def __init__(self, model_path: str, n_ctx: int = 4096,
                 max_tokens: int = 512, temperature: float = 0.7):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.max_tokens = max_tokens
        self.temperature = temperature
        # TODO: llama-cpp-python 初始化
        # from llama_cpp import Llama
        # self.model = Llama(model_path, n_ctx=n_ctx)
        logger.info("llama.cpp 后端就绪: %s", model_path)

    def generate(self, prompt: str, system: str = "") -> str:
        # TODO: CPU 推理
        # return self.model.create_completion(
        #     prompt, max_tokens=self.max_tokens, temperature=self.temperature
        # )["choices"][0]["text"]
        logger.info("[llama.cpp] prompt=%.80s...", prompt)
        return ""
