"""Google Cloud 認證模組，提供動態 Token 刷新功能"""

import logging
from datetime import datetime, timedelta, timezone

from google.auth import default
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)


# Vertex AI 所需的 OAuth Scope
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GoogleTokenProvider:
    """Google Cloud Token 提供者，自動管理憑證刷新"""

    def __init__(self) -> None:
        """初始化並取得預設憑證"""
        self._credentials, _ = default(scopes=_SCOPES)
        logger.info("Google Cloud 憑證初始化完成")

    async def get_token(self) -> str:
        """
        取得有效的 Access Token
        如果 Token 即將過期（5分鐘內），則自動刷新

        Returns:
            str: 有效的 Bearer Token
        """
        # 檢查是否需要刷新
        needs_refresh = not self._credentials.token or (
            self._credentials.expiry
            and (self._credentials.expiry.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc))
            < timedelta(minutes=5)
        )

        if needs_refresh:
            logger.debug("Token 不存在或即將過期，開始刷新...")
            self._credentials.refresh(Request())
            logger.debug("Token 刷新完成")

        token = self._credentials.token
        if not token:
            raise RuntimeError("無法取得 Google Cloud Access Token")

        return token


# 全域 Token 提供者實例
token_provider = GoogleTokenProvider()
