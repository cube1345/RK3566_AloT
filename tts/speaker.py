# TTS 语音播报 — 本地离线
import asyncio
import logging
import subprocess
import shutil

logger = logging.getLogger("tts")


class TTSEngine:
    def __init__(self, voice: str = "zh-CN"):
        self._voice = voice
        self._available = self._check_available()

    def _check_available(self) -> bool:
        if shutil.which("espeak"):
            return True
        if shutil.which("piper"):
            return True
        # 纯文本 fallback
        return False

    def speak(self, text: str):
        """同步 TTS"""
        if not text:
            return
        try:
            if shutil.which("espeak"):
                subprocess.run(
                    ["espeak", "-v", self._voice, text, "-s", "150"],
                    capture_output=True, timeout=5,
                )
            else:
                logger.info("[TTS] %s", text)
        except Exception as e:
            logger.warning("TTS 失败: %s", e)

    async def speak_async(self, text: str):
        """异步 TTS"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.speak, text)

    def say(self, text: str):
        """快捷方法: 静默降级"""
        self.speak(text)
