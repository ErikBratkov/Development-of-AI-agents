import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings

# piper опционален, без него ответы приходят только текстом
try:
    from piper import PiperVoice
except ImportError:
    PiperVoice = None


class TtsUnavailableError(RuntimeError):
    """Локальный TTS не установлен или файл голоса не найден"""


class TtsEngine:
    """Локальный синтез речи через Piper, отдает PCM16 по предложениям"""

    def __init__(self, settings: Settings) -> None:
        """Запоминает настройки, голосовая модель грузится лениво"""
        self._settings = settings
        self._voice: Any = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """Готов ли движок - piper установлен, включен и голос на месте"""
        if self._settings.tts_engine != "piper":
            return False
        if PiperVoice is None:
            return False
        return Path(self._settings.tts_voice).exists()

    async def get_sample_rate(self) -> int:
        """Возвращает частоту дискретизации голосовой модели"""
        async with self._lock:
            voice = await asyncio.to_thread(self._ensure_voice)
        return int(voice.config.sample_rate)

    async def synthesize(self, text: str) -> bytes:
        """Озвучивает кусок текста, возвращает сырые кадры PCM16"""
        if not text.strip():
            return b""
        async with self._lock:
            return await asyncio.to_thread(self._synthesize_sync, text)

    def _synthesize_sync(self, text: str) -> bytes:
        """Синхронный синтез, крутится в отдельном потоке"""
        voice = self._ensure_voice()
        # у старых версий piper-tts потоковый метод, у новых - итератор чанков
        if hasattr(voice, "synthesize_stream_raw"):
            return b"".join(voice.synthesize_stream_raw(text))
        chunks = voice.synthesize(text)
        return b"".join(chunk.audio_int16_bytes for chunk in chunks)

    def _ensure_voice(self) -> Any:
        """Лениво загружает голосовую модель Piper"""
        if not self.available:
            raise TtsUnavailableError("piper или файл голоса недоступны")
        if self._voice is None:
            self._voice = PiperVoice.load(self._settings.tts_voice)
        return self._voice
