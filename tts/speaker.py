# TTS 语音播报 — 本地离线 (piper > espeak > log fallback)
import asyncio
import logging
import os
import subprocess
import shutil
from pathlib import Path

logger = logging.getLogger("tts")

# piper 中文女声模型下载地址 (任选其一放到 ~/piper_models/)
_PIPER_VOICES = {
    "zh-CN": "zh_CN-huayan-medium.onnx",     # 华燕, 中等质量 ~130MB
    "zh-CN-low": "zh_CN-ljspeech-low.onnx",  # 低质量 ~32MB, 更快
}

_DEFAULT_VOICE = "zh-CN"


class TTSEngine:
    def __init__(self, voice: str = "zh-CN", piper_model_dir: str = None):
        self._voice = voice
        self._piper_dir = Path(piper_model_dir or os.path.expanduser("~/piper_models"))
        self._backend = self._detect_backend()
        logger.info("TTS 后端: %s", self._backend or "无(仅日志)")

    def _detect_backend(self) -> str:
        # piper 优先 (神经网络, 音质好)
        model_file = _PIPER_VOICES.get(self._voice, "")
        if shutil.which("piper") and model_file and (self._piper_dir / model_file).exists():
            return "piper"
        if shutil.which("piper-tts"):
            return "piper-tts"
        if shutil.which("espeak") or shutil.which("espeak-ng"):
            return "espeak"
        return ""

    def speak(self, text: str):
        if not text:
            return
        try:
            if self._backend == "piper":
                model = self._piper_dir / _PIPER_VOICES.get(self._voice, _PIPER_VOICES[_DEFAULT_VOICE])
                # piper 通过 stdin/stdout 流式合成, 输出 wav 直接播放
                proc = subprocess.run(
                    ["piper", "--model", str(model), "--output-raw"],
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=15,
                )
                if proc.returncode == 0 and proc.stdout:
                    # raw 16kHz 16-bit mono → aplay 播放
                    subprocess.run(
                        ["aplay", "-r", "22050", "-f", "S16_LE", "-c", "1"],
                        input=proc.stdout,
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    logger.warning("piper 合成失败: %s", proc.stderr.decode()[:100])
            elif self._backend == "piper-tts":
                subprocess.run(
                    ["piper-tts", "--text", text, "--voice", self._voice],
                    capture_output=True, timeout=10,
                )
            elif self._backend == "espeak":
                voice = "zh" if self._voice.startswith("zh") else self._voice
                subprocess.run(
                    ["espeak-ng", "-v", voice, text, "-s", "150"],
                    capture_output=True, timeout=5,
                )
            else:
                logger.info("[TTS] %s", text)
        except Exception as e:
            logger.warning("TTS 失败: %s", e)
            logger.info("[TTS-fallback] %s", text)

    async def speak_async(self, text: str):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.speak, text)

    def say(self, text: str):
        self.speak(text)

    @property
    def available(self) -> bool:
        return bool(self._backend)
