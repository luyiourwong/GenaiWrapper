"""主要轉發器模組，提供 OpenAI 相容的 API 端點"""

import logging
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from genaiwrapper.auth import token_provider
from genaiwrapper.config import settings

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 建立 FastAPI 應用
app = FastAPI(
    title="GenaiWrapper",
    description="本地轉發器，將 OpenAI 相容請求轉發到 GCP Vertex AI",
    version="0.1.0",
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


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """
    處理 OpenAI 相容的 /v1/chat/completions 請求
    支援 streaming 與非 streaming 模式
    """
    try:
        # 取得請求 body
        body = await request.json()

        # 記錄請求資訊：模型名稱與輸入長度
        model = body.get("model", "unknown")
        messages = body.get("messages", [])
        input_chars = sum(len(msg.get("content") or "") for msg in messages)
        logger.info(f"呼叫模型: {model}, 訊息數: {len(messages)}, 輸入字元數: {input_chars}")

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
            return await _handle_streaming_request(body, headers)
        else:
            # 非 Streaming 模式：直接回傳 JSON
            return await _handle_normal_request(body, headers)

    except Exception as e:
        logger.error(f"處理請求時發生錯誤: {e}", exc_info=True)
        raise


async def _handle_streaming_request(body: dict[str, Any], headers: dict[str, str]) -> StreamingResponse:
    """處理 streaming 請求，透傳 SSE 回應"""

    async def stream_generator() -> Any:
        """SSE 串流生成器"""
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                TARGET_URL,
                json=body,
                headers=headers,
            ) as response:
                response.raise_for_status()

                # 透傳 SSE 事件
                async for chunk in response.aiter_bytes():
                    yield chunk

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


async def _handle_normal_request(body: dict[str, Any], headers: dict[str, str]) -> Response:
    """處理非 streaming 請求，直接回傳 JSON"""

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            TARGET_URL,
            json=body,
            headers=headers,
        )
        response.raise_for_status()

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=dict(response.headers),
    )


def main() -> None:
    """啟動服務"""
    import uvicorn

    logger.info(f"啟動 GenaiWrapper 服務在 http://{settings.host}:{settings.port}")
    logger.info(f"目標 URL: {TARGET_URL}")

    uvicorn.run(
        "genaiwrapper.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
