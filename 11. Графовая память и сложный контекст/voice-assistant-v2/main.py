from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from app.config import Settings, normalize_proxy_env
from app.knowledge import begin_warmup, close_knowledge_base
from app.llm_client import LlmClient
from app.stt import SttEngine
from app.tts import TtsEngine
from app.ws_gateway import handle_websocket

STATIC_DIR = Path(__file__).resolve().parent / "app/static"

# системный прокси вида socks:// чиним до создания httpx клиентов
normalize_proxy_env()

# движки общие на процесс, тяжелые модели грузятся один раз
settings = Settings()
stt_engine = SttEngine(settings)
tts_engine = TtsEngine(settings)
llm_client = LlmClient(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Греет модель эмбеддингов на старте, закрывает Neo4j на стопе"""
    warmup_task = begin_warmup()
    yield
    if not warmup_task.done():
        warmup_task.cancel()
    await close_knowledge_base()


app = FastAPI(title="voice-assistant demo", lifespan=lifespan)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Единственный websocket, несет аудио и события в обе стороны"""
    await handle_websocket(ws, settings, stt_engine, tts_engine, llm_client)


# статику монтируем последней, чтобы не перекрыть /ws
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def run() -> None:
    """Точка входа для локального запуска через python main.py"""
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
