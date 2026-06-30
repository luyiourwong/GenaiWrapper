"""OpenRouter Backend 實作"""

import json
import logging
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from genaiwrapper.backends.base import BaseBackend
from genaiwrapper.config import settings
from genaiwrapper.models import ModelInfo
from genaiwrapper.models.openrouter_models import (
    get_openrouter_model_by_id,
    get_openrouter_model_list,
)
from genaiwrapper.stats import PricingInfo, UsageInfo

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

    for line in sse_chunk.split("\n"):
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                candidate = _extract_usage_metadata(data)
                if candidate.total_tokens > 0:
                    last_usage = candidate
            except json.JSONDecodeError:
                continue
    return last_usage


class OpenRouterBackend(BaseBackend):
    """OpenRouter Backend"""

    TARGET_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self) -> None:
        """初始化 OpenRouter Backend"""
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY 環境變數未設定")

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def target_url(self) -> str:
        return self.TARGET_URL

    async def get_headers(self, request: Request) -> dict[str, str]:
        """取得 OpenRouter 轉發請求所需的 headers"""
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/QwenLM/qwen-code",
            "X-OpenRouter-Title": "Qwen Code"
        }

        return headers

    def get_model_list(self) -> list[ModelInfo]:
        """取得 OpenRouter 支援的模型列表"""
        return get_openrouter_model_list()

    def get_model_by_id(self, model_id: str) -> ModelInfo | None:
        """根據 ID 取得 OpenRouter 模型資訊"""
        return get_openrouter_model_by_id(model_id)

    def _get_pricing(self, model_id: str) -> PricingInfo | None:
        """取得模型的價格資訊"""
        model_info = self.get_model_by_id(model_id)
        if model_info:
            return PricingInfo(
                input=model_info.pricing.input,
                cached_input=model_info.pricing.cached_input,
                output=model_info.pricing.output,
            )
        return None

    async def prepare_request_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """準備請求 body，處理 provider 設定"""
        model_id = body.get("model", "")
        model_info = self.get_model_by_id(model_id)

        if model_info and hasattr(model_info, "providers") and model_info.providers:
            # OpenRouter 支援 provider 參數
            body = {**body, "provider": {"order": model_info.providers}}

        return body

    async def handle_chat_completions(self, request: Request) -> Response:
        """
        處理 /v1/chat/completions 請求
        支援 streaming 與非 streaming 模式
        """
        from genaiwrapper.stats import stats_manager

        try:
            body = await request.json()
            model = body.get("model", "unknown")
            messages = body.get("messages", [])
            stream = body.get("stream", False)

            logger.info(f"呼叫模型: {model}, 訊息數: {len(messages)}")

            headers = await self.get_headers(request)
            body = await self.prepare_request_body(body)

            if stream:
                return await self._handle_streaming_request(body, headers, model, len(messages), stats_manager)
            else:
                return await self._handle_normal_request(body, headers, model, len(messages), stats_manager)

        except Exception as e:
            logger.error(f"處理請求時發生錯誤: {e}", exc_info=True)
            raise

    async def _handle_streaming_request(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
        model: str,
        message_count: int,
        stats_manager: Any,
    ) -> StreamingResponse:
        """處理 streaming 請求"""

        async def stream_generator() -> Any:
            usage = UsageInfo()
            buffer = ""
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST",
                        self.TARGET_URL,
                        json=body,
                        headers=headers,
                    ) as response:
                        response.raise_for_status()

                        async for chunk in response.aiter_bytes():
                            chunk_str = chunk.decode("utf-8", errors="ignore")
                            buffer += chunk_str

                            while "\n\n" in buffer:
                                event, buffer = buffer.split("\n\n", 1)
                                chunk_usage = _extract_usage_from_sse(event)
                                if chunk_usage.total_tokens > 0:
                                    usage = chunk_usage

                            yield chunk

                        if buffer.strip():
                            chunk_usage = _extract_usage_from_sse(buffer)
                            if chunk_usage.total_tokens > 0:
                                usage = chunk_usage

                stats_manager.record_request(
                    model, message_count, is_streaming=True, usage=usage, pricing=self._get_pricing(model)
                )
                logger.info(
                    f"Token 使用 - 模型: {model}, "
                    f"輸入: {usage.prompt_tokens}, "
                    f"輸出: {usage.completion_tokens}, "
                    f"緩存: {usage.cached_tokens}, "
                    f"總計: {usage.total_tokens}"
                )
            except Exception:
                raise

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
        self,
        body: dict[str, Any],
        headers: dict[str, str],
        model: str,
        message_count: int,
        stats_manager: Any,
    ) -> Response:
        """處理非 streaming 請求"""
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                self.TARGET_URL,
                json=body,
                headers=headers,
            )
            response.raise_for_status()

        response_json = response.json()
        usage = _extract_usage_metadata(response_json)

        stats_manager.record_request(
            model, message_count, is_streaming=False, usage=usage, pricing=self._get_pricing(model)
        )
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
