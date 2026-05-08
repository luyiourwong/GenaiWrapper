"""配置管理模組，使用 Pydantic Settings 讀取環境變數"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """應用程式設定"""

    # Google Cloud 設定
    project_id: str
    region: str
    endpoint: str

    # 服務設定
    host: str = "127.0.0.1"
    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 全域設定實例
settings = Settings()
