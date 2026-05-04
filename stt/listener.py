# STT 语音识别 — Vosk 离线引擎 (中文)
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("stt")

_DEFAULT_MODEL = os.path.expanduser("~/vosk_models/vosk-model-small-cn-0.22")


class STTEngine:
    def __init__(self, model_path: str = None):
        self._model_path = model_path or _DEFAULT_MODEL
        self._model = None
        self._available = self._load()

    def _load(self) -> bool:
        if not Path(self._model_path).exists():
            logger.info("Vosk 模型未下载: %s", self._model_path)
            return False
        try:
            import vosk
            vosk.SetLogLevel(-1)
            self._model = vosk.Model(str(self._model_path))
            logger.info("STT 已加载: vosk (offline, %s)", Path(self._model_path).name)
            return True
        except ImportError:
            logger.info("vosk 未安装: pip install vosk")
            return False
        except Exception as e:
            logger.warning("Vosk 加载失败: %s", e)
            return False

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """将 WAV PCM 16-bit mono 音频转文字"""
        if not self._model:
            return ""
        try:
            import vosk
            rec = vosk.KaldiRecognizer(self._model, float(sample_rate))
            rec.AcceptWaveform(audio_data)
            result = json.loads(rec.FinalResult())
            text = result.get("text", "").strip()
            if text:
                logger.info("STT: %s", text)
            return text
        except Exception as e:
            logger.warning("STT 识别失败: %s", e)
            return ""

    def transcribe_file(self, wav_path: str) -> str:
        """从 WAV 文件转文字"""
        data = Path(wav_path).read_bytes()
        return self.transcribe(data)

    @property
    def available(self) -> bool:
        return self._model is not None
