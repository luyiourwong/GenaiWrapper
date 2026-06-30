"""Backend 抽象介面"""

from abc import ABC, abstractmethod
from typing import Any

from fastapi import Request
from fastapi.responses import Response

from genaiwrapper.models import ModelInfo


class BaseBackend(ABC):
    """Backend 抽象介面，定義所有 backend 必須實作的方法"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend 名稱"""
        pass

    @property
    @abstractmethod
    def target_url(self) -> str:
        """目標 API URL"""
        pass

    @abstractmethod
    async def get_headers(self, request: Request) -> dict[str, str]:
        """
        取得轉發請求所需的 headers

        Args:
            request: 原始請求物件

        Returns:
            dict[str, str]: 轉發請求的 headers
        """
        pass

    @abstractmethod
    def get_model_list(self) -> list[ModelInfo]:
        """
        取得此 backend 支援的模型列表

        Returns:
            list[ModelInfo]: 模型列表
        """
        pass

    @abstractmethod
    def get_model_by_id(self, model_id: str) -> ModelInfo | None:
        """
        根據 ID 取得模型資訊

        Args:
            model_id: 模型 ID

        Returns:
            ModelInfo | None: 模型資訊，若不存在則回傳 None
        """
        pass

    async def prepare_request_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """
        準備請求 body（可選覆寫）

        Args:
            body: 原始請求 body

        Returns:
            dict[str, Any]: 處理後的請求 body
        """
        return body

    async def handle_chat_completions(self, request: Request) -> Response:
        """
        處理 /v1/chat/completions 請求

        Args:
            request: FastAPI 請求物件

        Returns:
            Response: API 回應
        """
        pass
