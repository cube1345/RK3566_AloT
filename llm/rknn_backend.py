# RKNN-LLM 后端 (RK3588 NPU)
import logging

logger = logging.getLogger("llm.rknn")


class RKNNBackend:
    """RK3588 NPU LLM 推理 (占位, 需 RKNN-LLM SDK)"""

    def __init__(self, model_path: str):
        self.model_path = model_path
        # TODO: RKNN-LLM 初始化
        # from rknnllm import RKNNLLM
        # self.model = RKNNLLM(model_path)
        logger.info("RKNN 后端就绪: %s", model_path)

    def generate(self, prompt: str, system: str = "") -> str:
        # TODO: NPU 推理
        # return self.model.generate(system + "\n" + prompt)
        logger.info("[RKNN] prompt=%.80s...", prompt)
        return ""

    def release(self):
        pass
