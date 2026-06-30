"""OpenRouter 模型定義"""

from genaiwrapper.models.base import ModelInfo, ModelPricing


class OpenRouterModelInfo(ModelInfo):
    """OpenRouter 模型資訊，包含 providers 欄位"""

    providers: list[str]


# OpenRouter 支援的模型列表
# 價格單位: USD per 1M tokens
OPENROUTER_MODELS: dict[str, OpenRouterModelInfo] = {
    "z-ai/glm-5.2": OpenRouterModelInfo(
        id="z-ai/glm-5.2",
        pricing=ModelPricing(
            input=3.0,
            cached_input=3.0,
            output=15.0,
        ),
        providers=["wandb/fp8", "venice/fp8", "novita/fp8", "z-ai/fp8", "siliconflow/fp8", "atlas-cloud/fp8"],
    ),
}


def get_openrouter_model_list() -> list[OpenRouterModelInfo]:
    """取得 OpenRouter 支援的模型列表"""
    return list(OPENROUTER_MODELS.values())


def get_openrouter_model_by_id(model_id: str) -> OpenRouterModelInfo | None:
    """根據 ID 取得 OpenRouter 模型資訊"""
    return OPENROUTER_MODELS.get(model_id)
