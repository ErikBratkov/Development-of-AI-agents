import logging
import threading
from typing import Any

from app.config import Settings

# sentence-transformers опционален, он тянет torch, поэтому живет
# в отдельной группе зависимостей rag
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

logger = logging.getLogger(__name__)

# модели семейства e5 обучены с префиксами, без них качество
# поиска заметно проседает
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


class Embedder:
    """Локальная модель эмбеддингов на sentence-transformers

    Одна и та же модель кодирует заметки при наполнении базы и вопросы
    при поиске, иначе векторы будут несовместимы. Вызовы синхронные и
    блокирующие, в асинхронном коде их надо заворачивать в to_thread
    """

    def __init__(self, settings: Settings) -> None:
        """Запоминает настройки, модель грузится лениво при первом вызове"""
        self._settings = settings
        self._model: Any = None
        # защита от параллельной загрузки модели из нескольких потоков
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Установлен ли sentence-transformers в окружении"""
        return SentenceTransformer is not None

    def embed_passage(self, text: str) -> list[float]:
        """Кодирует текст заметки для записи в базу"""
        return self._encode(PASSAGE_PREFIX + text)

    def embed_query(self, text: str) -> list[float]:
        """Кодирует поисковый вопрос пользователя"""
        return self._encode(QUERY_PREFIX + text)

    def warmup(self) -> None:
        """Загружает модель заранее, чтобы первый поиск не ждал ее"""
        self._ensure_model()

    def _encode(self, text: str) -> list[float]:
        """Общее кодирование, возвращает вектор списком float"""
        model = self._ensure_model()
        vector = model.encode(text, normalize_embeddings=True)
        return [float(value) for value in vector]

    def _ensure_model(self) -> Any:
        """Лениво загружает модель, первый вызов может быть долгим

        Без блокировки конкурентные вызовы (например отмененный ход
        и новый) грузили бы модель наперегонки в нескольких потоках,
        замедляя друг друга
        """
        if not self.available:
            raise RuntimeError("sentence-transformers не установлен")
        if self._model is None:
            with self._lock:
                if self._model is None:
                    logger.info(
                        "загрузка модели эмбеддингов %s",
                        self._settings.embedding_model,
                    )
                    self._model = SentenceTransformer(
                        self._settings.embedding_model
                    )
        return self._model
