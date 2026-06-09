"""主要轉發器模組，提供 OpenAI 相容的 API 端點"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from genaiwrapper.auth import token_provider
from genaiwrapper.config import settings
from genaiwrapper.models import ModelInfo, get_model_list
from genaiwrapper.stats import UsageInfo, stats_manager

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _extract_usage_metadata(response_data: dict[str, Any]) -> UsageInfo:
    """從 API 回應中提取 token 使用資訊 (OpenAI 相容格式)"""
    usage = response_data.get("usage", {})
    prompt_tokens_details = usage.get("prompt_tokens_details", {}) or {}
    return UsageInfo(
        prompt_tokens=usage.get("prompt_tokens", 0) or 0,
        completion_tokens=usage.get("completion_tokens", 0) or 0,
        cached_tokens=prompt_tokens_details.get("cached_tokens", 0) or 0,
        total_tokens=usage.get("total_tokens", 0) or 0,
    )


def _extract_usage_from_sse(sse_chunk: str) -> UsageInfo:
    """從 SSE 事件中提取 token 使用資訊"""
    last_usage = UsageInfo()

    # SSE 格式: "data: {...}"，一個 chunk 可能包含多行，取最後一個有 usage 的
    for line in sse_chunk.split("\n"):
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])  # 移除 "data: " 前綴
                candidate = _extract_usage_metadata(data)
                if candidate.total_tokens > 0:
                    last_usage = candidate
            except json.JSONDecodeError:
                continue
    return last_usage


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    """應用生命週期管理"""
    # Startup
    logger.info("GenaiWrapper 服務啟動")
    yield
    # Shutdown
    stats_manager.print_summary()


# 建立 FastAPI 應用
app = FastAPI(
    title="GenaiWrapper",
    description="本地轉發器，將 OpenAI 相容請求轉發到 GCP Vertex AI",
    version="0.1.0",
    lifespan=lifespan,
)


# 建立目標 URL
TARGET_URL = f"https://{settings.endpoint}/v1/projects/{settings.project_id}/locations/{settings.region}/endpoints/openapi/chat/completions"


@app.get("/")
async def root() -> dict[str, str]:
    """根端點，檢查服務是否運行"""
    return {
        "status": "running",
        "service": "GenaiWrapper",
        "target": TARGET_URL,
    }


@app.get("/v1/models")
async def list_models() -> dict[str, list[ModelInfo]]:
    """列出所有支援的模型"""
    return {"data": get_model_list()}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """
    處理 OpenAI 相容的 /v1/chat/completions 請求
    支援 streaming 與非 streaming 模式
    """
    try:
        # 取得請求 body
        body = await request.json()

        # 記錄請求資訊：模型名稱與訊息數
        model = body.get("model", "unknown")
        messages = body.get("messages", [])
        logger.info(f"呼叫模型: {model}, 訊息數: {len(messages)}")

        # 判斷是否為 streaming 請求
        stream = body.get("stream", False)

        # 取得 Google Cloud Access Token
        token = await token_provider.get_token()

        # 準備轉發請求的 headers
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # 保留原請求中的其他相關 headers
        if "x-goog-api-key" in request.headers:
            headers["x-goog-api-key"] = request.headers["x-goog-api-key"]

        if stream:
            # Streaming 模式：透傳 SSE 回應
            return await _handle_streaming_request(body, headers, model, len(messages))
        else:
            # 非 Streaming 模式：直接回傳 JSON
            return await _handle_normal_request(body, headers, model, len(messages))

    except Exception as e:
        logger.error(f"處理請求時發生錯誤: {e}", exc_info=True)
        raise


async def _handle_streaming_request(
    body: dict[str, Any],
    headers: dict[str, str],
    model: str,
    message_count: int,
) -> StreamingResponse:
    """處理 streaming 請求，透傳 SSE 回應"""

    async def stream_generator() -> Any:
        """SSE 串流生成器"""
        usage = UsageInfo()
        buffer = ""
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    TARGET_URL,
                    json=body,
                    headers=headers,
                ) as response:
                    response.raise_for_status()

                    async for chunk in response.aiter_bytes():
                        chunk_str = chunk.decode("utf-8", errors="ignore")
                        buffer += chunk_str

                        # 從 buffer 中提取完整的 SSE 事件（以 \n\n 分隔）
                        while "\n\n" in buffer:
                            event, buffer = buffer.split("\n\n", 1)
                            chunk_usage = _extract_usage_from_sse(event)
                            if chunk_usage.total_tokens > 0:
                                usage = chunk_usage

                        yield chunk

                    # 處理 buffer 中剩餘的資料
                    if buffer.strip():
                        chunk_usage = _extract_usage_from_sse(buffer)
                        if chunk_usage.total_tokens > 0:
                            usage = chunk_usage

            # Streaming 完成後記錄統計與 log
            stats_manager.record_request(model, message_count, is_streaming=True, usage=usage)
            logger.info(
                f"Token 使用 - 模型: {model}, "
                f"輸入: {usage.prompt_tokens}, "
                f"輸出: {usage.completion_tokens}, "
                f"緩存: {usage.cached_tokens}, "
                f"總計: {usage.total_tokens}"
            )
        except Exception:
            # 發生錯誤時不記錄統計
            raise

    # 回傳 StreamingResponse，保持 SSE 格式
    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _handle_normal_request(
    body: dict[str, Any],
    headers: dict[str, str],
    model: str,
    message_count: int,
) -> Response:
    """處理非 streaming 請求，直接回傳 JSON"""

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            TARGET_URL,
            json=body,
            headers=headers,
        )
        response.raise_for_status()

    # 解析回應以提取 token 使用資訊
    response_json = response.json()
    usage = _extract_usage_metadata(response_json)

    # 記錄統計與 log
    stats_manager.record_request(model, message_count, is_streaming=False, usage=usage)
    logger.info(
        f"Token 使用 - 模型: {model}, "
        f"輸入: {usage.prompt_tokens}, "
        f"輸出: {usage.completion_tokens}, "
        f"緩存: {usage.cached_tokens}, "
        f"總計: {usage.total_tokens}"
    )

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=dict(response.headers),
    )


def main() -> None:
    """啟動服務"""

    logger.info(f"啟動 GenaiWrapper 服務在 http://{settings.host}:{settings.port}")
    logger.info(f"目標 URL: {TARGET_URL}")

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
