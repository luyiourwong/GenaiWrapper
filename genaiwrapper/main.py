"""主要轉發器模組，提供 OpenAI 相容的 API 端點"""

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

from genaiwrapper.backends import BaseBackend, OpenRouterBackend, VertexBackend
from genaiwrapper.config import settings
from genaiwrapper.stats import stats_manager

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _create_backend() -> BaseBackend:
    """根據設定建立對應的 backend 實例"""
    if settings.backend == "vertex":
        logger.info("使用 Vertex AI Backend")
        return VertexBackend()
    elif settings.backend == "openrouter":
        logger.info("使用 OpenRouter Backend")
        return OpenRouterBackend()
    else:
        raise ValueError(f"不支援的 backend: {settings.backend}")


# 全域 backend 實例
backend: BaseBackend


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    """應用生命週期管理"""
    global backend
    # Startup
    backend = _create_backend()
    logger.info(f"GenaiWrapper 服務啟動 (backend: {backend.name})")
    yield
    # Shutdown
    stats_manager.print_summary()


# 建立 FastAPI 應用
app = FastAPI(
    title="GenaiWrapper",
    description="本地轉發器，將 OpenAI 相容請求轉發到 Vertex AI 或 OpenRouter",
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict[str, str]:
    """根端點，檢查服務是否運行"""
    return {
        "status": "running",
        "service": "GenaiWrapper",
        "backend": backend.name,
        "target": backend.target_url,
    }


@app.get("/v1/models")
async def list_models() -> dict[str, list]:
    """列出所有支援的模型"""
    return {"data": backend.get_model_list()}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """
    處理 OpenAI 相容的 /v1/chat/completions 請求
    支援 streaming 與非 streaming 模式
    """
    return await backend.handle_chat_completions(request)


def main() -> None:
    """啟動服務"""
    logger.info(f"啟動 GenaiWrapper 服務在 http://{settings.host}:{settings.port}")
    logger.info(f"Backend: {settings.backend}")

    try:
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    except KeyboardInterrupt:
        logger.info("Server Stopping by KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Error occurred when starting Server: {e}")
    logger.info("Server Stopped")


if __name__ == "__main__":
    main()
