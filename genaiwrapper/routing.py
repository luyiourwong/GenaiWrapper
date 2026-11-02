"""上游路由模組，依模型設定決定實際的 Vertex AI 端點與模型 ID"""

import logging

from pydantic import BaseModel

from genaiwrapper.config import settings
from genaiwrapper.models import get_model_by_id

logger = logging.getLogger(__name__)

# 多區域 location 使用 regional endpoint (rep) 網域
MULTI_REGIONS: frozenset[str] = frozenset({"us", "eu"})


class UpstreamTarget(BaseModel):
    """轉發目標"""

    url: str
    model: str


def build_target_url(endpoint: str, location: str) -> str:
    """組出 OpenAI 相容 chat completions 的上游 URL"""
    return (
        f"https://{endpoint}/v1/projects/{settings.project_id}/locations/{location}/endpoints/openapi/chat/completions"
    )


def endpoint_for_location(location: str) -> str:
    """依 location 推得對應的 API 網域"""
    if location == "global":
        return "aiplatform.googleapis.com"
    if location in MULTI_REGIONS:
        return f"aiplatform.{location}.rep.googleapis.com"
    return f"{location}-aiplatform.googleapis.com"


# 預設目標 URL（未指定 location 的模型使用）
DEFAULT_TARGET_URL: str = build_target_url(settings.endpoint, settings.region)


def resolve_upstream(model: str) -> UpstreamTarget:
    """解析請求的模型名稱，回傳實際的上游 URL 與模型 ID"""
    model_info = get_model_by_id(model)
    if model_info is None or model_info.location is None:
        upstream_model = model_info.upstream_model if model_info else model
        return UpstreamTarget(url=DEFAULT_TARGET_URL, model=upstream_model)

    location = model_info.location
    target = UpstreamTarget(
        url=build_target_url(endpoint_for_location(location), location),
        model=model_info.upstream_model,
    )
    logger.debug(f"模型 {model} 路由至 {location}: {target.url} (upstream model: {target.model})")
    return target
