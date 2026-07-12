import asyncio
from typing import Any

import numpy as np

from app.config import Settings

# faster-whisper опционален, без него работает текстовый режим
try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None


class SttUnavailableError(RuntimeError):
    """Локальный STT не установлен или не смог загрузиться"""


class SttEngine:
    """Распознавание речи на faster-whisper

    Конец реплики определяет клиент (push-to-talk), поэтому реплика
    распознается целиком за один вызов. VAD внутри whisper только
    отсекает тишину по краям записи
    """

    def __init__(self, settings: Settings) -> None:
        """Запоминает настройки, модель грузится лениво при первом вызове"""
        self._settings = settings
        self._model: Any = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """Установлен ли faster-whisper в окружении"""
        return WhisperModel is not None

    async def transcribe(self, pcm: bytes) -> str:
        """Распознает реплику из PCM16 mono 16 kHz, возвращает текст"""
        if not self.available:
            raise SttUnavailableError("faster-whisper не установлен")
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, bytes(pcm))

    def _transcribe_sync(self, pcm: bytes) -> str:
        """Синхронная часть распознавания, крутится в отдельном потоке"""
        model = self._ensure_model()
        audio = np.frombuffer(pcm, dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
        segments, _info = model.transcribe(
            audio,
            language=self._settings.whisper_language,
            beam_size=1,
            vad_filter=True,
        )
        parts = [segment.text.strip() for segment in segments]
        return " ".join(part for part in parts if part).strip()

    def _ensure_model(self) -> Any:
        """Лениво загружает модель, первый вызов может быть долгим"""
        if self._model is None:
            self._model = WhisperModel(
                self._settings.whisper_model,
                device=self._settings.whisper_device,
                compute_type="int8",
            )
        return self._model
