"""模型定義模組，手動維護支援的模型列表與價格資訊"""

from pydantic import BaseModel


class ModelPricing(BaseModel):
    """模型價格資訊 (單位: USD per 1M tokens)"""

    input: float
    cached_input: float
    output: float


class ModelInfo(BaseModel):
    """模型資訊"""

    id: str
    pricing: ModelPricing


# 手動維護的模型列表
# 價格單位: USD per 1M tokens
SUPPORTED_MODELS: dict[str, ModelInfo] = {
    "google/gemini-3.1-pro-preview": ModelInfo(
        id="google/gemini-3.1-pro-preview",
        pricing=ModelPricing(
            input=2,
            cached_input=0.2,
            output=12,
        ),
    ),
    "google/gemini-2.5-pro": ModelInfo(
        id="google/gemini-2.5-pro",
        pricing=ModelPricing(
            input=1.25,
            cached_input=0.13,
            output=10,
        ),
    ),
    "google/gemini-3.5-flash": ModelInfo(
        id="google/gemini-3.5-flash",
        pricing=ModelPricing(
            input=1.5,
            cached_input=0.15,
            output=9,
        ),
    ),
    "google/gemini-2.5-flash": ModelInfo(
        id="google/gemini-2.5-flash",
        pricing=ModelPricing(
            input=0.3,
            cached_input=0.03,
            output=2.5,
        ),
    ),
    "google/gemini-3.1-flash-lite": ModelInfo(
        id="google/gemini-3.1-flash-lite",
        pricing=ModelPricing(
            input=0.25,
            cached_input=0.025,
            output=1.5,
        ),
    ),
    "google/gemini-2.5-flash-lite": ModelInfo(
        id="google/gemini-2.5-flash-lite",
        pricing=ModelPricing(
            input=0.3,
            cached_input=0.03,
            output=2.5,
        ),
    ),
    "xai/grok-4.3": ModelInfo(
        id="xai/grok-4.3",
        pricing=ModelPricing(
            input=1.25,
            cached_input=0.2,
            output=2.5,
        ),
    ),
    "xai/grok-4.20-reasoning": ModelInfo(
        id="xai/grok-4.20-reasoning",
        pricing=ModelPricing(
            input=1.25,
            cached_input=0.2,
            output=2.5,
        ),
    ),
    "xai/grok-4.20-non-reasoning": ModelInfo(
        id="xai/grok-4.20-non-reasoning",
        pricing=ModelPricing(
            input=1.25,
            cached_input=0.2,
            output=2.5,
        ),
    ),
    "xai/grok-4.1-fast-reasoning": ModelInfo(
        id="xai/grok-4.1-fast-reasoning",
        pricing=ModelPricing(
            input=0,
            cached_input=0,
            output=0,
        ),
    ),
    "xai/grok-4.1-fast-non-reasoning": ModelInfo(
        id="xai/grok-4.1-fast-non-reasoning",
        pricing=ModelPricing(
            input=0,
            cached_input=0,
            output=0,
        ),
    ),
    "moonshotai/kimi-k2-thinking-maas": ModelInfo(
        id="moonshotai/kimi-k2-thinking-maas",
        pricing=ModelPricing(
            input=0.6,
            cached_input=0.06,
            output=2.5,
        ),
    ),
    "minimaxai/minimax-m2-maas": ModelInfo(
        id="minimaxai/minimax-m2-maas",
        pricing=ModelPricing(
            input=0.3,
            cached_input=0.03,
            output=1.2,
        ),
    ),
    "openai/gpt-oss-120b-maas": ModelInfo(
        id="openai/gpt-oss-120b-maas",
        pricing=ModelPricing(
            input=0.09,
            cached_input=0.09,
            output=0.36,
        ),
    ),
    "zai-org/glm-5-maas": ModelInfo(
        id="zai-org/glm-5-maas",
        pricing=ModelPricing(
            input=1,
            cached_input=0.1,
            output=3.2,
        ),
    ),
    "zai-org/glm-4.7-maas": ModelInfo(
        id="zai-org/glm-4.7-maas",
        pricing=ModelPricing(
            input=0.6,
            cached_input=0.6,
            output=2.2,
        ),
    ),
}


def get_model_list() -> list[ModelInfo]:
    """取得所有支援的模型列表"""
    return list(SUPPORTED_MODELS.values())


def get_model_by_id(model_id: str) -> ModelInfo | None:
    """根據 ID 取得模型資訊"""
    return SUPPORTED_MODELS.get(model_id)
