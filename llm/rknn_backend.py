# RKNN-LLM 后端 (RK3588 NPU)
import logging

logger = logging.getLogger("llm.rknn")


class RKNNBackend:
    """RK3588 NPU LLM 推理 (需 RKNN-LLM SDK)"""

    def __init__(self, model_path: str):
        self.model_path = model_path
        try:
            from rknnllm import RKNNLLM  # type: ignore
            self.model = RKNNLLM(model_path)
            logger.info("RKNN 后端就绪: %s", model_path)
        except ImportError:
            raise ImportError("RKNN-LLM SDK 未安装, 需要 RK3588 NPU 环境")

    def generate(self, prompt: str, system: str = "") -> str:
        # TODO: NPU 推理
        # return self.model.generate(system + "\n" + prompt)
        logger.info("[RKNN] prompt=%.80s...", prompt)
        return ""

    def release(self):
        pass
