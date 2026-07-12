import asyncio
import logging
from typing import Any

from app.config import Settings
from app.embeddings import Embedder

# драйвер neo4j опционален, без него инструмент честно говорит
# модели, что база знаний не установлена
try:
    from neo4j import AsyncGraphDatabase
except ImportError:
    AsyncGraphDatabase = None

logger = logging.getLogger(__name__)

# имя векторного индекса по фрагментам документов, его же создает
# seed_knowledge.py при наполнении базы
VECTOR_INDEX_NAME = "chunk_embedding"

# описание инструмента в формате OpenAI function calling,
# по аналогии с get_weather из weather.py
KNOWLEDGE_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "Ищет ответ во внутренней базе знаний о сотрудниках, "
            "компаниях, проектах и технологиях. Используй, когда "
            "пользователь спрашивает про людей, их навыки, проекты, "
            "команды или стек технологий."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Вопрос пользователя своими словами, на русском"
                    ),
                },
            },
            "required": ["question"],
        },
    },
}

# векторный поиск фрагментов лексического графа по эмбеддингу вопроса,
# заголовок родительского документа добираем через PART_OF
CHUNKS_QUERY = """
CALL db.index.vector.queryNodes($index_name, $top_k, $vector)
YIELD node, score
OPTIONAL MATCH (node)-[:PART_OF]->(doc:Document)
RETURN node.id AS id, coalesce(doc.title, '') AS title,
       node.text AS text, score
ORDER BY score DESC
"""


def build_facts_query(max_hops: int) -> str:
    """Собирает Cypher обхода графа от сущностей из найденных фрагментов

    Глубину обхода нельзя передать параметром запроса, поэтому она
    подставляется в текст после проверки, что это разумное целое.
    Связи MENTIONS из обхода исключаются - интересен только доменный
    граф, а не соседние документы
    """
    hops = int(max_hops)
    if hops < 1 or hops > 5:
        raise ValueError("max_hops должен быть в пределах от 1 до 5")
    return (
        "MATCH (chunk:Chunk)-[:MENTIONS]->(seed)\n"
        "WHERE chunk.id IN $chunk_ids\n"
        f"MATCH (seed)-[rels*1..{hops}]-(other)\n"
        "WHERE none(rel IN rels WHERE type(rel) = 'MENTIONS')\n"
        "UNWIND rels AS rel\n"
        "WITH DISTINCT startNode(rel) AS a, type(rel) AS rel_type,\n"
        "     endNode(rel) AS b\n"
        "RETURN a.name AS subject, rel_type AS relation, b.name AS object\n"
        "LIMIT $max_facts"
    )


def format_fact(row: dict[str, Any]) -> str:
    """Превращает строку результата обхода в факт вида 'a -[REL]-> b'"""
    return f"{row['subject']} -[{row['relation']}]-> {row['object']}"


def format_knowledge_context(
    facts: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> str:
    """Собирает результат гибридного поиска в текст для модели

    Функция чистая, аргументы не меняет. Если не нашлось ни фактов,
    ни фрагментов, возвращает пустую строку - что с ней делать,
    решает обработчик инструмента
    """
    parts: list[str] = []
    if facts:
        parts.append("Факты из графа знаний:")
        parts.extend("- " + format_fact(row) for row in facts)
    if chunks:
        if parts:
            parts.append("")
        parts.append("Выдержки из базы знаний:")
        parts.extend(
            "- " + str(row.get("title", "")) + ": " + str(row.get("text", ""))
            for row in chunks
        )
    return "\n".join(parts)


class KnowledgeBase:
    """Гибридный поиск по базе знаний в Neo4j

    Сначала векторный поиск фрагментов документов по смыслу вопроса,
    затем обход доменного графа от сущностей, упомянутых в найденных
    фрагментах. Исключения наружу не выпускает - модель всегда
    получает текст, тот же контракт, что у инструмента погоды
    """

    def __init__(
        self,
        settings: Settings,
        embedder: Embedder | None = None,
        driver: Any = None,
    ) -> None:
        """Запоминает настройки, драйвер создается лениво при поиске

        Готовые embedder и driver можно подложить в тестах
        """
        self._settings = settings
        self._embedder = (
            embedder if embedder is not None else Embedder(settings)
        )
        self._driver = driver

    @property
    def available(self) -> bool:
        """Есть ли эмбеддинги и хоть какой-то способ достучаться до БД"""
        if not self._embedder.available:
            return False
        return self._driver is not None or AsyncGraphDatabase is not None

    def warmup(self) -> None:
        """Синхронно загружает модель эмбеддингов"""
        self._embedder.warmup()

    async def search(self, question: str) -> str:
        """Ищет ответ на вопрос, возвращает контекст одной строкой"""
        if not question.strip():
            return "Вопрос пустой, уточни его у пользователя"
        if not self.available:
            return (
                "База знаний не установлена на сервере, "
                "ответить по ней не получится"
            )
        try:
            vector = await asyncio.to_thread(
                self._embedder.embed_query, question
            )
            chunks, facts = await self._retrieve(vector)
        except Exception:
            logger.exception("сбой гибридного поиска по базе знаний")
            return "База знаний сейчас недоступна, попробуйте позже"
        context = format_knowledge_context(facts, chunks)
        if not context:
            return "В базе знаний ничего не найдено по этому вопросу"
        return context

    async def _retrieve(
        self, vector: list[float]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Два запроса к Neo4j - фрагменты по вектору, факты по связям"""
        driver = self._ensure_driver()
        async with driver.session(
            database=self._settings.neo4j_database
        ) as session:
            chunks = await session.execute_read(
                self._fetch_chunks, vector, self._settings.kb_top_k
            )
            chunk_ids = [row["id"] for row in chunks]
            facts: list[dict[str, Any]] = []
            if chunk_ids:
                facts = await session.execute_read(
                    self._fetch_facts,
                    chunk_ids,
                    self._settings.kb_max_hops,
                    self._settings.kb_max_facts,
                )
        return chunks, facts

    @staticmethod
    async def _fetch_chunks(
        tx: Any, vector: list[float], top_k: int
    ) -> list[dict[str, Any]]:
        """Читает top_k ближайших фрагментов из векторного индекса"""
        result = await tx.run(
            CHUNKS_QUERY,
            index_name=VECTOR_INDEX_NAME,
            top_k=top_k,
            vector=vector,
        )
        return await result.data()

    @staticmethod
    async def _fetch_facts(
        tx: Any, chunk_ids: list[int], max_hops: int, max_facts: int
    ) -> list[dict[str, Any]]:
        """Читает факты доменного графа вокруг упомянутых сущностей"""
        result = await tx.run(
            build_facts_query(max_hops),
            chunk_ids=chunk_ids,
            max_facts=max_facts,
        )
        return await result.data()

    def _ensure_driver(self) -> Any:
        """Лениво создает единственный на процесс драйвер neo4j"""
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(
                    self._settings.neo4j_username,
                    self._settings.neo4j_password,
                ),
            )
        return self._driver

    async def close(self) -> None:
        """Закрывает драйвер, зовется при остановке сервиса"""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None


# единый на процесс экземпляр, создается при первом вызове инструмента
_knowledge_base: KnowledgeBase | None = None

# событие окончания прогрева. Изначально установлено - пока прогрев
# не запускался, блокировать клиента нечем
_warmup_finished = asyncio.Event()
_warmup_finished.set()


def get_knowledge_base() -> KnowledgeBase:
    """Возвращает общий экземпляр базы знаний, создавая при нужде"""
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase(Settings())
    return _knowledge_base


async def search_knowledge(question: str) -> str:
    """Обработчик инструмента search_knowledge для LLM"""
    return await get_knowledge_base().search(question)


async def warmup_knowledge_base() -> None:
    """Заранее греет модель эмбеддингов в фоне при старте сервиса

    Без прогрева первый вопрос к базе знаний ждет загрузку модели
    десятки секунд на CPU. Отказ прогрева не страшен - поиск при
    нужде загрузит модель сам
    """
    try:
        kb = get_knowledge_base()
        if not kb.available:
            return
        await asyncio.to_thread(kb.warmup)
        logger.info("модель эмбеддингов прогрета")
    except Exception:
        logger.exception("не удалось прогреть модель эмбеддингов")
    finally:
        _warmup_finished.set()


def begin_warmup() -> "asyncio.Task[None]":
    """Стартует фоновый прогрев и помечает его незаконченным

    Событие сбрасывается синхронно, чтобы клиент, подключившийся
    сразу после старта сервера, уже видел идущую загрузку
    """
    _warmup_finished.clear()
    return asyncio.create_task(warmup_knowledge_base())


def warmup_pending() -> bool:
    """Идет ли еще прогрев модели эмбеддингов"""
    return not _warmup_finished.is_set()


async def wait_warmup() -> None:
    """Ждет окончания прогрева, без прогрева возвращается сразу"""
    await _warmup_finished.wait()


async def close_knowledge_base() -> None:
    """Закрывает соединение с Neo4j при остановке приложения"""
    global _knowledge_base
    if _knowledge_base is not None:
        await _knowledge_base.close()
        _knowledge_base = None
